# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Read the configured store, prove the approval gate on it, and put back what it changed.

    python merchant/scripts/smoke_live.py [--read-only]

The test suite runs every one of these paths against canned Admin API responses, which
proves the example's logic and proves nothing about the store. This script is the other
half: it talks to a real Shopify store, and what it checks is the handful of things only a
real store can tell you — that the token's scopes cover the reads, that the documents in
``queries.py`` match the schema the store is serving, and that a write lands where the
Admin API says it will.

It needs no Anthropic credentials. The model is not in the loop here: the script stages and
applies through the same tool executor a conversation would, so the gates are the real ones
without a turn being generated.

The write it performs is a price move on one product, chosen as the smallest reversible
change the example can make. It reads the old price first, applies the new one, reads it
back, and puts the old one back — and if the revert itself fails, it says so loudly with the
price it left behind, because a smoke test that quietly leaves a store changed is worse than
one that fails. ``--read-only`` skips the write entirely.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXAMPLE_DIR.parent))

from commerce_common.memory import InMemoryMemoryStore  # noqa: E402
from demo_common import load_demo_env  # noqa: E402
from merchant_agent import (  # noqa: E402
    ActorKind,
    MerchantAgentConfig,
    MerchantSessionContext,
    MerchantSessionState,
    PriceUpdateItem,
)
from merchant_agent.changes import ChangeNotApplicable  # noqa: E402
from merchant_agent.executor import MerchantToolExecutor  # noqa: E402
from merchant_agent_runtime import MerchantAgent  # noqa: E402
from merchant.api.admin_client import (  # noqa: E402
    AdminAPIError,
    AdminGraphQLClient,
)
from merchant.api.agent_config import (  # noqa: E402
    SKILLS_DIR,
    MissingCredentials,
    build_merchant_config,
    load_settings,
)
from merchant.api.queries import TOKEN_SCOPES  # noqa: E402
from merchant.api.shopify_backend import ShopifyMerchantBackend  # noqa: E402

# As in seed_store.py: the credentials are in the example's .env, and a script run from a
# shell sees only the environment until this is called.
load_demo_env(EXAMPLE_DIR)

PASS = "  ok  "
FAIL = " FAIL "
SKIP = " skip "


class Report:
    """What passed and what did not, so the script can run every check and then exit once."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.skipped: list[str] = []

    def check(self, condition: bool, description: str, detail: str = "") -> bool:
        print(f"[{PASS if condition else FAIL}] {description}{f' — {detail}' if detail else ''}")
        if not condition:
            self.failures.append(description)
        return condition

    def note(self, text: str) -> None:
        print(f"         {text}")

    def skip(self, description: str, reason: str) -> None:
        """A read this token cannot make. Not a failure: a deployment's token holds the
        scopes its store granted, and a read the interface offers can be out of reach
        without the rest of it being wrong. Saying which one, and why, is the point of
        running this against a real store."""
        print(f"[{SKIP}] {description} — {reason}")
        self.skipped.append(description)


async def read_checks(backend: ShopifyMerchantBackend, session, report: Report) -> None:
    """Every abstract read on the interface, against the store. The assertions are weak on
    purpose: the figures belong to whatever store this is pointed at, so what is checked is
    that each read answers in the interface's own types and not that it answers 25."""
    profile = await backend.profile()
    report.check(bool(profile.name), "shop profile", f"{profile.name} · {profile.currency}")

    listings = backend.all_listings()
    report.check(bool(listings), "the catalog read", f"{len(listings)} active listings")
    if not listings:
        report.note("An empty catalog leaves the rest of the reads with nothing to describe.")
        report.note("Run scripts/seed_store.py first.")
        return

    subject = listings[0]
    detail = await backend.get_listing(session, subject.listing_id)
    report.check(detail is not None, "get_listing", subject.listing_id)

    found = await backend.search_listings(session, subject.title.split()[0], None, 10)
    report.check(bool(found), "search_listings", f"{len(found)} matches for a word in the title")

    snapshot = await backend.get_business_snapshot(session, None)
    report.check(
        snapshot.period != "",
        "get_business_snapshot",
        f"{snapshot.period}: {snapshot.currency} {snapshot.sales} over {snapshot.orders} orders",
    )
    if snapshot.sales == 0:
        report.note(
            "No sales in the period. A store seeded today has no history yet; the snapshot "
            "reports the window it measured rather than inventing one."
        )

    series = await backend.query_metrics(session, "sales")
    report.check(bool(series.points), "query_metrics", f"{len(series.points)} points")

    alerts = await backend.get_inventory_alerts(session)
    report.check(True, "get_inventory_alerts", f"{len(alerts)} alerts")

    issues = await backend.get_order_issues(session)
    report.check(True, "get_order_issues", f"{len(issues)} issues")

    pricing = await backend.get_pricing_context(session, subject.listing_id)
    report.check(
        pricing is not None, "get_pricing_context", f"floor {pricing.min_price if pricing else '?'}"
    )

    try:
        campaigns = await backend.get_campaign_performance(session, None)
    except ChangeNotApplicable as error:
        report.skip("get_campaign_performance", str(error))
    else:
        report.check(True, "get_campaign_performance", f"{len(campaigns)} campaigns")

    context = await backend.get_merchant_context(session)
    report.check(bool(context), "get_merchant_context")
    for key, value in (context or {}).items():
        report.note(f"{key}: {value}")


async def gate_checks(
    backend: ShopifyMerchantBackend,
    config: MerchantAgentConfig,
    session,
    report: Report,
    *,
    read_only: bool,
) -> None:
    """Staging writes nothing; a chat-path apply is held; a host-approved apply writes.

    This is the example's whole safety claim, checked against a store rather than a fake.
    """
    subject = next((entry for entry in backend.all_listings() if entry.price > 1), None)
    if subject is None:
        report.check(False, "a priced listing to move", "the catalog has none")
        return

    before = subject.price
    target = round(before * 1.05, 2)

    staged = await backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=subject.listing_id, new_price=target)]
    )
    report.check(
        staged.change_id.startswith("chg-"),
        "stage_price_update returns a staged change",
        f"{staged.change_id}: {subject.title} {before} → {target}",
    )

    reread = await backend.get_listing(session, subject.listing_id)
    report.check(
        reread is not None and reread.price == before,
        "staging left the store's price alone",
        f"still {reread.price if reread else '?'}",
    )

    agent = MerchantAgent(
        backend=backend,
        skills_dir=SKILLS_DIR,
        config=config,
        memory_store=InMemoryMemoryStore(),
    )
    state = MerchantSessionState()
    state.remember_change(staged)

    def executor() -> MerchantToolExecutor:
        return MerchantToolExecutor(
            backend=backend,
            config=agent.config,
            skills=agent.skills,
            session=session,
            state=state,
            memory=agent.memory,
        )

    unapproved = await executor().execute("apply_change", {"change_id": staged.change_id})
    report.check(
        unapproved.blocked is not None,
        "an apply with no host approval is held",
        unapproved.blocked or "not held",
    )
    held = await backend.get_listing(session, subject.listing_id)
    report.check(
        held is not None and held.price == before, "the held apply wrote nothing", f"still {before}"
    )

    if read_only:
        await backend.discard_change(session, staged.change_id, ActorKind.OPERATOR)
        report.note("--read-only: the staged change was discarded and nothing was written.")
        return

    # What the portal's Approve button does: mark the id, run the executor, drop the mark.
    state.approved_change_ids.add(staged.change_id)
    applied = await executor().execute("apply_change", {"change_id": staged.change_id})
    state.approved_change_ids.discard(staged.change_id)
    report.check(
        not applied.is_error and applied.blocked is None,
        "a host-approved apply goes through",
        applied.result_text[:120],
    )

    after = await backend.get_listing(session, subject.listing_id)
    report.check(
        after is not None and after.price == target,
        "the new price is in the store",
        f"{after.price if after else '?'} (wanted {target})",
    )

    # Put it back. This is a fresh change through the same gate, so the revert is as
    # auditable as the change it undoes.
    revert = await backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=subject.listing_id, new_price=before)]
    )
    state.remember_change(revert)
    state.approved_change_ids.add(revert.change_id)
    reverted = await executor().execute("apply_change", {"change_id": revert.change_id})
    state.approved_change_ids.discard(revert.change_id)
    final = await backend.get_listing(session, subject.listing_id)
    if not report.check(
        not reverted.is_error and final is not None and final.price == before,
        "the price was put back",
        f"{final.price if final else '?'} (wanted {before})",
    ):
        print()
        print(
            f"!!! {subject.title} ({subject.listing_id}) is still priced at "
            f"{final.price if final else 'an unknown amount'}. Set it back to {before}."
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--read-only", action="store_true", help="run the reads and the gate, write nothing"
    )
    args = parser.parse_args()

    try:
        settings = load_settings()
    except MissingCredentials as error:
        print(error)
        return 2

    client = AdminGraphQLClient(
        shop_domain=settings.shop_domain,
        access_token=settings.admin_token,
        api_version=settings.api_version,
    )
    config = build_merchant_config(settings.store_name or settings.shop_domain)
    backend = ShopifyMerchantBackend(client, settings, config)
    session = MerchantSessionContext(
        session_id="smoke", merchant_id=settings.merchant_id, operator=settings.operator
    )
    report = Report()

    print(f"{settings.shop_domain} · Admin API {settings.api_version}")
    print()
    try:
        try:
            scopes = await client.execute(TOKEN_SCOPES)
        except AdminAPIError as error:
            report.note(f"could not read the token's scopes: {error}")
        else:
            handles = [
                entry.get("handle")
                for entry in (scopes.get("currentAppInstallation") or {}).get("accessScopes") or []
            ]
            report.note(f"scopes: {', '.join(sorted(str(handle) for handle in handles))}")

        await backend.warm()
        await read_checks(backend, session, report)
        print()
        await gate_checks(backend, config, session, report, read_only=args.read_only)
    except AdminAPIError as error:
        print()
        print(f"[{FAIL}] the Admin API failed: {error}")
        return 1
    finally:
        await client.aclose()

    print()
    if report.failures:
        print(f"{len(report.failures)} failed: " + "; ".join(report.failures))
        return 1
    if report.skipped:
        print(
            f"all checks passed, {len(report.skipped)} out of reach for this token: "
            + "; ".join(report.skipped)
        )
        return 0
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
