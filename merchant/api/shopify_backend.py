# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""``MerchantBackend`` over one Shopify store's Admin API.

This is the whole integration: the merchant agent's prompt, tools, skills, gates, and the
three runtimes are unchanged, and everything below is the mapping from sixteen interface
methods onto Admin GraphQL. Nothing in ``merchant-agent/core`` was touched to make it work,
which is the claim the example exists to demonstrate.

Where Shopify and the interface disagree, the mapping is narrowed rather than invented.
The places that happens are worth knowing before reading the code:

- Product GIDs are the listing ids. They round-trip verbatim, so an id the agent quotes is
  an id a write can act on and an id it invents resolves to nothing.
- The interface has no variant dimension. A listing's price is its first variant's and a
  price move scales the ladder (see ``staging.py``); its stock is the sum across tracked
  variants.
- ``paused`` means an unpublished (draft) product, because that is what the portal's pause
  action produces. Archived products are left out of the catalog entirely.
- Money and margin figures are computed here, from ``unitCost`` where the store records
  one, and are absent rather than estimated where it does not.
- Campaigns read from marketing activities. Spend and revenue are not exposed there, so
  they report zero and the campaign carries a note saying so.
- ``execute_analysis_query`` stays unimplemented: this deployment exposes no SQL surface,
  so the config leaves analysis off rather than offering a tool that always fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from demo_common.merchant_fixtures import change_pct, filter_listings, margin_pct, stage_campaign
from merchant_agent import (
    ActorKind,
    BusinessSnapshot,
    Campaign,
    CampaignDraft,
    ChangeItem,
    ChangeKind,
    ChangeLedger,
    ChangeStatus,
    InventoryActionItem,
    InventoryAlert,
    Listing,
    ListingDetails,
    ListingFilters,
    MerchantAgentConfig,
    MerchantBackend,
    MerchantSessionContext,
    MetricSeries,
    OrderIssue,
    PriceUpdateItem,
    PricingContext,
    PromotionDraft,
    StagedChange,
)
from merchant_agent.changes import ChangeNotApplicable, GuardrailViolation, check_guardrails

from .admin_client import AdminAPIError, AdminExecutor
from .agent_config import DATA_DIR, ShopifySettings
from .alerts import AlertBuilder, AlertRules
from .catalog import CatalogCache, ProductRecord, normalize_product_id
from .metrics import MetricsSource, resolve_period
from .orders import OrderScan, daily_rows
from .queries import MARKETING_ACTIVITIES, SHOP_PROFILE
from .staging import (
    LISTING_FIELDS,
    PRICE_FIELD,
    STATUS_FIELD,
    STOCK_FIELD,
    ShopifyWriter,
    WriteFailed,
)

_CAMPAIGN_STATUS = {
    "ACTIVE": "active",
    "PAUSED": "paused",
    "INACTIVE": "paused",
    "DISCONNECTED": "paused",
    "DRAFT": "draft",
    "PENDING": "draft",
    "SCHEDULED": "draft",
}
_CAMPAIGN_NOTE = "spend and revenue are not exposed on marketing activities"


@dataclass(frozen=True)
class ShopProfile:
    name: str
    domain: str
    currency: str
    timezone: str | None


class ShopifyMerchantBackend(MerchantBackend):
    """One store, read and written through the Admin API."""

    def __init__(
        self,
        executor: AdminExecutor,
        settings: ShopifySettings,
        config: MerchantAgentConfig,
    ) -> None:
        self._executor = executor
        self._settings = settings
        self._config = config
        self.ledger = ChangeLedger(config)
        self.catalog = CatalogCache(executor)
        self.orders = OrderScan(executor)
        self._rules = AlertRules.load(
            DATA_DIR / "thresholds.json",
            low_stock_default=settings.low_stock_default,
            fulfilment_sla_days=settings.fulfilment_sla_days,
        )
        self.alerts = AlertBuilder(self.catalog, self.orders, self._rules)
        self._writer = ShopifyWriter(executor, self.catalog)
        self._profile: ShopProfile | None = None
        self._metrics: MetricsSource | None = None

    # -- Store profile ---------------------------------------------------------------

    async def profile(self) -> ShopProfile:
        """The shop's own name, currency, and timezone, read once. The configured store
        name wins when one is set, so a demo can be labelled without renaming the store."""
        if self._profile is None:
            data = await self._executor.execute(SHOP_PROFILE, {})
            shop = data.get("shop") or {}
            self._profile = ShopProfile(
                name=self._settings.store_name or shop.get("name") or self._settings.shop_domain,
                domain=shop.get("myshopifyDomain") or self._settings.shop_domain,
                currency=shop.get("currencyCode") or "USD",
                timezone=shop.get("ianaTimezone"),
            )
        return self._profile

    @property
    def store_name(self) -> str:
        """The display name, available synchronously for the portal. Falls back to the
        configured name until the profile has been read."""
        if self._profile is not None:
            return self._profile.name
        return self._settings.store_name or self._settings.shop_domain

    @property
    def display_currency(self) -> str:
        """The store's currency for the portal's synchronous reads. ``warm()`` fills this
        in at startup; before that it is the default rather than a blocking read."""
        return self._profile.currency if self._profile is not None else "USD"

    async def currency(self) -> str:
        return (await self.profile()).currency

    async def metrics(self) -> MetricsSource:
        if self._metrics is None:
            self._metrics = MetricsSource(
                self._executor,
                order_scan=self.orders,
                catalog=self.catalog,
                currency=await self.currency(),
                shopifyql_enabled=self._settings.shopifyql_enabled,
            )
        return self._metrics

    # -- Performance -------------------------------------------------------------------

    async def get_business_snapshot(
        self, session: MerchantSessionContext, period: str | None = None
    ) -> BusinessSnapshot:
        metrics = await self.metrics()
        counts = await self.alerts.counts(len(self.ledger.pending()))
        return await metrics.snapshot(period, counts)

    async def query_metrics(
        self,
        session: MerchantSessionContext,
        metric: str,
        period: str | None = None,
        granularity: str = "day",
        segment: str | None = None,
    ) -> MetricSeries:
        metrics = await self.metrics()
        return await metrics.series(metric, period, granularity, segment)

    async def get_campaign_performance(
        self, session: MerchantSessionContext, campaign_id: str | None = None
    ) -> list[Campaign]:
        """Marketing activities as campaigns. A store with no marketing app connected has
        none, and a token without the marketing scope cannot see them; both are reported as
        an unmanaged system rather than as an empty result, because "no campaigns" and "no
        campaign data" lead to different advice."""
        currency = await self.currency()
        try:
            data = await self._executor.execute(MARKETING_ACTIVITIES, {"first": 25})
        except AdminAPIError as error:
            raise ChangeNotApplicable(
                "campaign performance is not available for this store — the access token "
                "cannot read marketing activities"
            ) from error
        campaigns = [
            self._campaign(node, currency)
            for node in ((data.get("marketingActivities") or {}).get("nodes") or [])
        ]
        if not campaigns:
            raise ChangeNotApplicable(
                "this store has no marketing activities, so there are no campaigns to report"
            )
        if campaign_id:
            return [entry for entry in campaigns if entry.campaign_id == campaign_id]
        return campaigns

    @staticmethod
    def _campaign(node: dict[str, Any], currency: str) -> Campaign:
        budget = ((node.get("budget") or {}).get("total") or {}).get("amount")
        return Campaign(
            campaign_id=node["id"],
            name=node.get("title") or node["id"],
            status=_CAMPAIGN_STATUS.get((node.get("status") or "").upper(), "ended"),
            objective=None,
            channel=(node.get("marketingChannelType") or "").lower() or None,
            budget=float(budget) if budget else 0.0,
            spend=0.0,
            revenue=0.0,
            currency=currency,
            starts=(node.get("createdAt") or "")[:10] or None,
        )

    # -- Catalog -----------------------------------------------------------------------

    async def search_listings(
        self,
        session: MerchantSessionContext,
        query: str,
        filters: ListingFilters | None = None,
        limit: int = 8,
    ) -> list[Listing]:
        currency = await self.currency()
        # A wider set is fetched than asked for, because the shared filters cut it again.
        records = await self.catalog.search(query, limit * 3)
        if not records and (direct := await self._by_id(query)) is not None:
            records = [direct]
        units = await self.orders.units_by_product(days=30)
        listings = [record.to_listing(currency) for record in records]
        return filter_listings(
            listings, filters, limit, sales_of=lambda listing_id: units.get(listing_id, 0)
        )

    async def _by_id(self, query: str) -> ProductRecord | None:
        """A query that is itself a listing id. Text scoring ranks a raw GID poorly, and an
        operator who pastes one has named a listing rather than described one."""
        candidate = normalize_product_id(query.strip())
        if candidate == query.strip() and not candidate.startswith("gid://"):
            return None
        return await self.catalog.get(candidate)

    async def get_listing(
        self, session: MerchantSessionContext, listing_id: str
    ) -> ListingDetails | None:
        record = await self.catalog.get(listing_id)
        if record is None:
            return None
        currency = await self.currency()
        units = await self.orders.units_by_product(days=30)
        refunds = await self.orders.refund_units_by_product(days=30)
        sold = units.get(record.product_id, 0)
        refunded = refunds.get(record.product_id, 0)
        return record.to_details(
            currency,
            sales_last_30d=sold,
            return_rate_pct=round(refunded / sold * 100, 1) if sold else None,
        )

    def all_listings(self) -> list[Listing]:
        """Every listing, for the portal's catalog view. Synchronous because the shared
        router reads it that way, so it serves the cached catalog rather than fetching."""
        return [record.to_listing(self.display_currency) for record in self.catalog.cached()]

    # -- Inventory and order health ------------------------------------------------------

    async def get_inventory_alerts(self, session: MerchantSessionContext) -> list[InventoryAlert]:
        return await self.alerts.inventory_alerts()

    async def get_order_issues(self, session: MerchantSessionContext) -> list[OrderIssue]:
        return await self.alerts.order_issues()

    # -- Pricing --------------------------------------------------------------------------

    async def get_pricing_context(
        self, session: MerchantSessionContext, listing_id: str
    ) -> PricingContext | None:
        record = await self.catalog.get(listing_id)
        if record is None:
            return None
        cost = record.unit_cost
        delta_cap = self._config.max_price_delta_pct
        floor = round(record.price * (1 - delta_cap / 100), 2)
        return PricingContext(
            listing_id=record.product_id,
            current_price=record.price,
            currency=await self.currency(),
            unit_cost=cost,
            margin_pct=margin_pct(record.price, cost) if cost and record.price > 0 else None,
            # The cap the deployment enforces, raised to unit cost where the store records
            # one: a move the guardrails would allow can still be a move below cost.
            min_price=max(floor, round(cost, 2)) if cost else floor,
            max_price=round(record.price * (1 + delta_cap / 100), 2),
            max_price_delta_pct=delta_cap,
            max_promotion_discount_pct=self._config.max_promotion_discount_pct,
            demand_signal=await self._demand_signal(record.product_id),
            last_changed=(record.updated_at or "")[:10] or None,
        )

    async def _demand_signal(self, product_id: str) -> str | None:
        """The last fortnight's units against the one before it. None when neither
        fortnight sold any, because a flat zero is not a steady trend."""
        recent = await self.orders.units_by_product(days=14)
        wider = await self.orders.units_by_product(days=28)
        current = recent.get(product_id, 0)
        prior = wider.get(product_id, 0) - current
        if not current and not prior:
            return None
        shift = change_pct(current, prior)
        if shift is None:
            return "rising" if current else "falling"
        return "rising" if shift > 15 else "falling" if shift < -15 else "steady"

    # -- Staged writes ---------------------------------------------------------------------

    async def stage_listing_update(
        self,
        session: MerchantSessionContext,
        listing_id: str,
        fields: dict[str, Any],
        note: str | None = None,
    ) -> StagedChange:
        record = await self._require(listing_id)
        listing = record.to_listing(await self.currency())
        # Checked here rather than at apply time. A field this deployment cannot write would
        # otherwise stage cleanly, reach the operator as a card with an Approve button, and
        # fail on the click — after the approval, which is the wrong end of the gate. The
        # guardrails own the fields that have their own tool, so those pass through to the
        # message that names it.
        blocked = {name.casefold() for name in self._config.listing_update_blocked_fields}
        if unwritable := [
            name for name in fields if name not in LISTING_FIELDS and name.casefold() not in blocked
        ]:
            raise ChangeNotApplicable(
                f"this deployment does not write {', '.join(sorted(unwritable))} on a listing. "
                f"It writes: {', '.join(sorted(LISTING_FIELDS))}"
            )
        items = [
            ChangeItem(
                target=record.product_id,
                field=name,
                before=getattr(listing, name, None) or listing.attributes.get(name),
                after=value,
            )
            for name, value in fields.items()
        ]
        # Staged here means proposed by the assistant on the operator's behalf; the apply
        # is the operator's own act, and only the host can record it.
        return self.ledger.stage(
            kind=ChangeKind.LISTING_UPDATE,
            summary=note or f"Update listing content on {record.title}",
            items=items,
            actor=session.operator,
            actor_kind=ActorKind.AGENT,
        )

    async def stage_price_update(
        self,
        session: MerchantSessionContext,
        items: list[PriceUpdateItem],
        note: str | None = None,
    ) -> StagedChange:
        currency = await self.currency()
        change_items: list[ChangeItem] = []
        margins: list[tuple[float, float]] = []
        margin_notes: list[str] = []
        margin_impact = 0.0
        units = await self.orders.units_by_product(days=30)
        for item in items:
            record = await self._require(item.listing_id)
            before = record.price
            cost = record.unit_cost or 0.0
            pace = units.get(record.product_id, 0) / 30
            margin_impact += (item.new_price - before) * pace * 7
            if cost and before > 0:
                margin_before = margin_pct(before, cost)
                margin_after = margin_pct(item.new_price, cost)
                margins.append((margin_before, margin_after))
                margin_notes.append(
                    f"{record.title} margin: {margin_before}% → {margin_after}% "
                    f"({margin_after - margin_before:+.1f} pts)"
                )
            if len(record.variants) > 1:
                margin_notes.append(
                    f"{record.title} has {len(record.variants)} variants; every one moves by "
                    "the same percentage when this is applied"
                )
            change_items.append(
                ChangeItem(
                    target=record.product_id,
                    field=PRICE_FIELD,
                    before=before,
                    after=item.new_price,
                )
            )
        return self.ledger.stage(
            kind=ChangeKind.PRICE_UPDATE,
            summary=note or f"Price update for {len(items)} listing(s)",
            items=change_items,
            actor=session.operator,
            actor_kind=ActorKind.AGENT,
            currency=currency,
            margin_impact=round(margin_impact, 2),
            margin_before_pct=margins[0][0] if len(margins) == 1 else None,
            margin_after_pct=margins[0][1] if len(margins) == 1 else None,
            guardrail_notes=margin_notes or None,
        )

    async def stage_inventory_action(
        self,
        session: MerchantSessionContext,
        items: list[InventoryActionItem],
        note: str | None = None,
    ) -> StagedChange:
        change_items: list[ChangeItem] = []
        notes: list[str] = []
        for item in items:
            record = await self._require(item.listing_id)
            if item.action == "restock":
                if not record.tracks_inventory:
                    raise ChangeNotApplicable(
                        f"{record.title} does not track inventory, so it cannot be restocked"
                    )
                change_items.append(
                    ChangeItem(
                        target=record.product_id,
                        field=STOCK_FIELD,
                        before=record.stock,
                        after=record.stock + (item.quantity or 0),
                    )
                )
                if len(record.variants) > 1:
                    notes.append(
                        f"{record.title} has {len(record.variants)} variants; the units go to "
                        f"'{record.variants[0].title}' unless staged one variant at a time"
                    )
                continue
            wanted = "paused" if item.action == "pause" else "active"
            change_items.append(
                ChangeItem(
                    target=record.product_id,
                    field=STATUS_FIELD,
                    before=record.status,
                    after=wanted,
                )
            )
            if wanted == "paused":
                notes.append(
                    f"pausing {record.title} makes it a draft, which removes it from every "
                    "sales channel, not only the online store"
                )
        return self.ledger.stage(
            kind=ChangeKind.INVENTORY_ACTION,
            summary=note or f"Inventory action for {len(items)} listing(s)",
            items=change_items,
            actor=session.operator,
            actor_kind=ActorKind.AGENT,
            guardrail_notes=notes or None,
        )

    async def stage_promotion(
        self, session: MerchantSessionContext, promotion: PromotionDraft
    ) -> StagedChange:
        """A promotion is staged and previewed like any other change, and applying it
        records the decision without creating a discount (see ``staging.py``). The note is
        on the change itself so the operator reads it on the preview card, before
        approving, rather than afterwards."""
        currency = await self.currency()
        items: list[ChangeItem] = []
        margins: list[tuple[float, float]] = []
        notes = [
            "applying this records the decision in the change ledger; it does not create a "
            "discount on the store"
        ]
        margin_impact = 0.0
        units = await self.orders.units_by_product(days=30)
        for listing_id in promotion.listing_ids:
            record = await self._require(listing_id)
            promo_price = round(record.price * (1 - promotion.discount_pct / 100), 2)
            pace = units.get(record.product_id, 0) / 30
            margin_impact -= (record.price - promo_price) * pace * 7
            cost = record.unit_cost
            if cost and promo_price > 0:
                margin_before = margin_pct(record.price, cost)
                margin_after = margin_pct(promo_price, cost)
                margins.append((margin_before, margin_after))
                notes.append(
                    f"{record.title} margin: {margin_before}% → {margin_after}% "
                    f"({margin_after - margin_before:+.1f} pts) for the window"
                )
            items.append(
                ChangeItem(
                    target=record.product_id,
                    field="promotion_price",
                    before=record.price,
                    after=promo_price,
                )
            )
        return self.ledger.stage(
            kind=ChangeKind.PROMOTION,
            summary=(
                f"{promotion.name} ({promotion.discount_pct:.0f}% off, "
                f"{promotion.starts} to {promotion.ends})"
            ),
            items=items,
            actor=session.operator,
            actor_kind=ActorKind.AGENT,
            currency=currency,
            margin_impact=round(margin_impact, 2),
            margin_before_pct=margins[0][0] if len(margins) == 1 else None,
            margin_after_pct=margins[0][1] if len(margins) == 1 else None,
            guardrail_notes=notes,
        )

    async def stage_campaign(
        self, session: MerchantSessionContext, campaign: CampaignDraft
    ) -> StagedChange:
        """Campaign drafts use the shared staging helper. Existing campaigns are looked up
        so a budget change previews against the current figure; a store with no marketing
        activities stages against nothing, which is the same as a new campaign."""
        try:
            existing = {
                entry.campaign_id: entry for entry in await self.get_campaign_performance(session)
            }
        except ChangeNotApplicable:
            existing = {}
        return stage_campaign(
            self.ledger,
            existing,
            campaign,
            actor=session.operator,
            currency=await self.currency(),
        )

    async def _require(self, listing_id: str) -> ProductRecord:
        """The product an id names, refusing an id that resolves to nothing. Staged, such
        an id would preview cleanly and then apply to no product at all."""
        record = await self.catalog.get(listing_id)
        if record is None:
            raise ChangeNotApplicable(f"no listing {listing_id} in this store")
        return record

    # -- Change lifecycle -------------------------------------------------------------------

    async def get_pending_changes(self, session: MerchantSessionContext) -> list[StagedChange]:
        return self.ledger.pending()

    async def apply_change(self, session: MerchantSessionContext, change_id: str) -> StagedChange:
        """Write the change to the store, then record it as applied.

        The guardrails are re-checked here before anything is sent, because the config may
        have tightened since the change was staged. The ledger is advanced only after
        Shopify has accepted the write, so a failed write leaves the change staged and
        re-approvable and there is no ledger state to unwind. What that does not undo is a
        partial write across several listings: ``WriteFailed`` names the ones already
        written, and that message is what the operator sees."""
        change = self.ledger.get(change_id)
        if change is None:
            raise ChangeNotApplicable(f"no change with id {change_id!r} to apply")
        if change.status is not ChangeStatus.STAGED:
            raise ChangeNotApplicable(
                f"change {change_id} is {change.status.value}, not staged — nothing to apply"
            )
        # Raised as a guardrail rather than a plain failure, so the tool executor reports
        # it on the guardrail gate exactly as it reports one caught at staging time.
        if violations := check_guardrails(change.kind, change.items, self._config):
            raise GuardrailViolation(violations)
        try:
            notes = await self._writer.apply(change)
        except WriteFailed as error:
            raise ChangeNotApplicable(
                f"the store did not accept change {change_id}: {error}. It is still staged."
            ) from error
        self.orders.invalidate()
        applied = self.ledger.apply(change_id, session.operator)
        if notes:
            applied.guardrail_notes = [*applied.guardrail_notes, *notes]
        return applied

    async def discard_change(
        self,
        session: MerchantSessionContext,
        change_id: str,
        actor_kind: ActorKind = ActorKind.OPERATOR,
    ) -> StagedChange:
        return self.ledger.discard(change_id, session.operator, actor_kind)

    # -- Merchant context ---------------------------------------------------------------------

    async def get_merchant_context(self, session: MerchantSessionContext) -> dict[str, Any] | None:
        """The small block of store facts sent with every request. It names where the
        figures come from, because the agent should be able to say "traffic is not available
        for this store" instead of "traffic is zero"."""
        profile = await self.profile()
        metrics = await self.metrics()
        period = resolve_period(None, default_days=7)
        return {
            "store": profile.name,
            "shop_domain": profile.domain,
            "currency": profile.currency,
            "timezone": profile.timezone,
            "catalog_size": len(await self.catalog.all()),
            "default_period": period.label,
            "order_history_days": self.orders.window_days,
            "data_source": metrics.source_note,
            "campaigns": _CAMPAIGN_NOTE,
            "pending_changes": len(self.ledger.pending()),
        }

    # -- Portal extras -------------------------------------------------------------------------
    #
    # The shared router calls ``overview_extras`` synchronously, so both reads below serve
    # the caches. That is safe rather than lucky: ``/overview`` awaits the snapshot and the
    # alert lists first, which is what fills them.

    def kpi_trends(self) -> dict[str, list[dict[str, Any]]]:
        """The sparkline series behind the portal's KPI cards, derived from the order scan.

        There is no ``conversion`` series, and its card renders without a sparkline as a
        result. Orders carry no session data, so the only series this example could produce
        would be a flat zero — a shape that reads as measured and steady rather than as
        absent.

        The headline figures above these charts come from whichever source ``MetricsSource``
        is using, which may be ShopifyQL; ShopifyQL's ``total_sales`` nets out discounts and
        returns where an order total does not, so the two can differ by a few percent. The
        charts are there for shape, and the agent states its own source when asked."""
        end = datetime.now(UTC).date() - timedelta(days=1)
        rows = daily_rows(self.orders.cached(), end - timedelta(days=29), end)
        return {
            "sales": [{"date": row.day.isoformat(), "value": row.sales} for row in rows],
            "orders": [{"date": row.day.isoformat(), "value": float(row.orders)} for row in rows],
            "average_order_value": [
                {
                    "date": row.day.isoformat(),
                    "value": round(row.sales / row.orders, 2) if row.orders else 0.0,
                }
                for row in rows
            ],
        }

    def home_insights(self, limit: int = 3) -> list[dict[str, Any]]:
        """The overview's assistant cards: what an operator would want to see before asking
        anything, each with the question the portal prefills into the conversation.

        Every card applies the same rule the agent's own alert reads apply, so the portal
        and the conversation cannot disagree about what needs attention."""
        inventory = self.alerts.inventory_alerts_cached()
        issues = self.alerts.order_issues_cached()
        insights: list[dict[str, Any]] = []

        low = [alert for alert in inventory if alert.kind == "low_stock"]
        if low:
            shallowest = low[0]
            insights.append(
                {
                    "insight_id": "low-stock",
                    "kind": "inventory",
                    "headline": (
                        f"{len(low)} listing(s) are at or below their stock threshold"
                        if len(low) > 1
                        else f"{shallowest.title} is down to {shallowest.stock} units"
                    ),
                    "detail": ", ".join(f"{alert.title} ({alert.stock} left)" for alert in low[:3]),
                    "prompt": (
                        "Which of my low-stock listings should I restock first, and how many "
                        "units each?"
                    ),
                }
            )

        slow = [alert for alert in inventory if alert.kind == "slow_mover"]
        if slow:
            insights.append(
                {
                    "insight_id": "slow-movers",
                    "kind": "inventory",
                    "headline": f"{len(slow)} listing(s) are holding stock that is not moving",
                    "detail": ", ".join(
                        f"{alert.title} ({alert.stock} in stock, "
                        f"{alert.sales_last_30d or 0} sold in 30d)"
                        for alert in slow[:3]
                    ),
                    "prompt": (
                        "Which slow-moving listings are worth a price move or better content, "
                        "and which should I just let run down?"
                    ),
                }
            )

        if delayed := [issue for issue in issues if issue.kind == "delayed"]:
            insights.append(
                {
                    "insight_id": "delayed-orders",
                    "kind": "orders",
                    "headline": f"{len(delayed)} order(s) are past the fulfilment window",
                    "detail": delayed[0].summary,
                    "prompt": "Walk me through the orders that are running late and what to do "
                    "about each one.",
                }
            )

        if spikes := [issue for issue in issues if issue.kind == "return_spike"]:
            insights.append(
                {
                    "insight_id": "return-spikes",
                    "kind": "returns",
                    "headline": f"{len(spikes)} listing(s) are being refunded above the threshold",
                    "detail": spikes[0].summary,
                    "prompt": f"{spikes[0].summary} — what is the likely cause, and what would "
                    "you change on the listing?",
                }
            )

        if pending := self.ledger.pending():
            insights.append(
                {
                    "insight_id": "pending-changes",
                    "kind": "changes",
                    "headline": f"{len(pending)} change(s) are waiting for your approval",
                    "detail": pending[0].summary,
                    "prompt": "Summarise the changes waiting for my approval and what each one "
                    "would do.",
                }
            )

        return insights[:limit]

    async def warm(self) -> None:
        """One pass over the reads the portal's first paint needs, so the profile, catalog,
        and orders are in hand before the first request rather than during it."""
        await self.profile()
        await self.catalog.all()
        await self.orders.orders()
