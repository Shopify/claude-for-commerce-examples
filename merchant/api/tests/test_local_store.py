# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The local store: the mode that runs with no Shopify account.

The rest of this suite proves the backend against canned Admin API responses. This file
proves the other thing a reader needs before trusting a demo they ran on their laptop: that
the local store answers every read the backend makes, that it *applies* what an approved
change writes, and that the round-trip an operator sees — stage, approve on the card, look
again — really does come back changed.

It also pins the two limits, so nobody reads a local run as more than it is: sessions and
conversion are not answered, and the local store's own ``LocalStore`` is not the Shopify
schema. ``scripts/smoke_live.py`` is what settles those against a development store.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from commerce_common.memory import InMemoryMemoryStore
from demo_common import SESSION_HEADER
from merchant_agent import (
    ChangeNotApplicable,
    InventoryActionItem,
    MerchantSessionContext,
    PriceUpdateItem,
)
from merchant.api.admin_client import AdminUserError
from merchant.api.agent_config import DATA_DIR, LOCAL_DOMAIN, ShopifySettings
from merchant.api.local_store import LOCATION_ID, LocalStore
from merchant.api.merchant import create_merchant_portal
from merchant.api.queries import ADJUST_INVENTORY
from merchant.api.shopify_backend import ShopifyMerchantBackend

from .conftest import router_session_store

APRON = "canvas-tool-apron"
LADDER = "folding-step-stool"
BROOM = "sawdust-broom"


@pytest.fixture
def store() -> LocalStore:
    return LocalStore.from_seed(DATA_DIR / "seed.json")


@pytest.fixture
def local_settings(settings: ShopifySettings) -> ShopifySettings:
    return ShopifySettings(
        **{
            **settings.__dict__,
            "local_store": True,
            "admin_token": "",
            "shop_domain": LOCAL_DOMAIN,
        }
    )


@pytest.fixture
def local_backend(store: LocalStore, local_settings, config) -> ShopifyMerchantBackend:
    return ShopifyMerchantBackend(store, local_settings, config)


def listing_id(store: LocalStore, handle: str) -> str:
    return next(product["id"] for product in store.products if product["handle"] == handle)


def price_of(store: LocalStore, handle: str) -> list[str]:
    product = next(p for p in store.products if p["handle"] == handle)
    return [variant["price"] for variant in product["variants"]["nodes"]]


# -- The store answers what the backend asks -----------------------------------------


def test_the_seed_file_is_the_catalog(store: LocalStore) -> None:
    """One seed file, two consumers: this store builds its catalog from the same JSON
    ``scripts/seed_store.py`` sends to a real store, so a local demo and a seeded one tell
    the same story."""
    handles = [product["handle"] for product in store.products]
    assert handles[0] == APRON
    assert LADDER in handles and BROOM in handles
    assert "discontinued-mallet" in handles  # archived; the catalog read is what hides it
    ladder = next(p for p in store.products if p["handle"] == LADDER)
    assert [v["price"] for v in ladder["variants"]["nodes"]] == ["60.00", "90.00"]


async def test_every_read_the_backend_makes_is_answered(local_backend, session) -> None:
    """The claim that matters for a local run: no read falls through to "the local store has
    no answer for". Each of these is a separate Admin document."""
    await local_backend.warm()
    assert local_backend.store_name
    assert len(local_backend.all_listings()) == 7  # the archived one is not in the catalog

    snapshot = await local_backend.get_business_snapshot(session, None)
    assert snapshot.sales > 0 and snapshot.orders > 0
    assert snapshot.currency == "USD"

    series = await local_backend.query_metrics(session, "sales")
    assert len(series.points) > 1

    apron = listing_id(store_of(local_backend), APRON)
    assert await local_backend.search_listings(session, "apron")
    assert await local_backend.get_listing(session, apron)
    assert await local_backend.get_pricing_context(session, apron)
    assert await local_backend.get_inventory_alerts(session)
    assert await local_backend.get_order_issues(session)
    assert await local_backend.get_campaign_performance(session, None)
    assert isinstance(await local_backend.get_merchant_context(session), dict)


def store_of(backend: ShopifyMerchantBackend) -> LocalStore:
    """The store the backend was built over. The transport is private, and a test that
    asserts on the store's own state has to reach it the way the wiring did."""
    return backend._executor  # noqa: SLF001


async def test_a_prior_period_exists_to_compare_against(local_backend, session) -> None:
    """What the invented history behind the seed orders is for. A freshly seeded real store
    has nothing in the previous period, so its comparison reads as a rise from zero; a local
    store has both periods and the comparison means something."""
    snapshot = await local_backend.get_business_snapshot(session, None)
    assert snapshot.compare_to
    assert snapshot.sales_change_pct is not None


async def test_both_order_issue_kinds_are_reachable_locally(local_backend, session) -> None:
    """A refund cannot be seeded onto a real store, so the return-spike rule is unreachable
    there. Locally both kinds are, which is why the local store is where the alert rules get
    exercised as a set."""
    issues = await local_backend.get_order_issues(session)
    assert {issue.kind for issue in issues} == {"delayed", "return_spike"}


async def test_traffic_is_not_answered_rather_than_invented(local_backend, session) -> None:
    """The one figure the local store refuses. Inventing a session count would read as
    measured; the example reports it as unmeasured, and says so in the merchant context the
    model sees."""
    snapshot = await local_backend.get_business_snapshot(session, None)
    assert snapshot.traffic == 0
    context = await local_backend.get_merchant_context(session)
    assert "traffic and conversion read 0" in str(context)


async def test_the_sales_metrics_come_from_shopifyql_locally(local_backend, session) -> None:
    """The local store answers ``FROM sales``, so the primary metrics path is the one a local
    demo exercises. ``SHOPIFY_DISABLE_SHOPIFYQL=1`` is how a reader sees the fallback."""
    await local_backend.get_business_snapshot(session, None)
    context = await local_backend.get_merchant_context(session)
    assert "ShopifyQL" in str(context)


# -- The store applies what an approved change writes ---------------------------------


async def test_an_applied_price_move_changes_the_store(
    local_backend, session, store: LocalStore
) -> None:
    await local_backend.warm()
    before = price_of(store, LADDER)
    staged = await local_backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=listing_id(store, LADDER), new_price=66.0)]
    )

    assert price_of(store, LADDER) == before, "staging must not write"

    await local_backend.apply_change(session, staged.change_id)

    # The whole ladder moved by the same ratio, which is what `staging.py` promises.
    assert price_of(store, LADDER) == ["66.00", "99.00"]


async def test_an_applied_listing_update_changes_the_store(
    local_backend, session, store: LocalStore
) -> None:
    await local_backend.warm()
    staged = await local_backend.stage_listing_update(
        session,
        listing_id(store, "bench-dog-set"),
        {"seo_description": "Four machined bench dogs for a three-quarter-inch dog hole."},
    )
    await local_backend.apply_change(session, staged.change_id)

    product = next(p for p in store.products if p["handle"] == "bench-dog-set")
    assert product["seo"]["description"].startswith("Four machined bench dogs")


async def test_an_applied_restock_changes_the_store(
    local_backend, session, store: LocalStore
) -> None:
    await local_backend.warm()
    product = next(p for p in store.products if p["handle"] == "bench-dog-set")
    before = product["variants"]["nodes"][0]["inventoryQuantity"]

    staged = await local_backend.stage_inventory_action(
        session,
        [
            InventoryActionItem(
                listing_id=listing_id(store, "bench-dog-set"), action="restock", quantity=24
            )
        ],
    )
    await local_backend.apply_change(session, staged.change_id)

    assert product["variants"]["nodes"][0]["inventoryQuantity"] == before + 24
    assert product["totalInventory"] == before + 24


async def test_the_local_store_refuses_an_adjust_that_names_the_wrong_quantity(
    store: LocalStore,
) -> None:
    """``changeFromQuantity`` and the rule behind it. The Admin API requires an adjustment to
    name the quantity it believes it is changing from, and refuses one that disagrees with
    the store; the stand-in enforces both, so the restock path meets the same rule locally as
    it does against Shopify. ``staging.py`` satisfies it by reading the level immediately
    before the write rather than trusting the catalog cache."""
    product = next(p for p in store.products if p["handle"] == "bench-dog-set")
    item = product["variants"]["nodes"][0]["inventoryItem"]["id"]
    held = product["variants"]["nodes"][0]["inventoryQuantity"]

    def adjust(change: dict[str, object]) -> dict[str, object]:
        return {
            "idempotencyKey": "test",
            "input": {"reason": "correction", "name": "available", "changes": [change]},
        }

    base = {"delta": 5, "inventoryItemId": item, "locationId": LOCATION_ID}
    with pytest.raises(AdminUserError, match="must include the following argument"):
        await store.mutate(ADJUST_INVENTORY, adjust(base), root="inventoryAdjustQuantities")
    with pytest.raises(AdminUserError, match="the stock moved since it was read"):
        await store.mutate(
            ADJUST_INVENTORY,
            adjust({**base, "changeFromQuantity": held + 1}),
            root="inventoryAdjustQuantities",
        )

    assert product["variants"]["nodes"][0]["inventoryQuantity"] == held

    await store.mutate(
        ADJUST_INVENTORY,
        adjust({**base, "changeFromQuantity": held}),
        root="inventoryAdjustQuantities",
    )
    assert product["variants"]["nodes"][0]["inventoryQuantity"] == held + 5


async def test_the_untracked_product_still_refuses_a_restock(
    local_backend, session, store: LocalStore
) -> None:
    """The local store enforces what a real store enforces, so the refusal a reader sees on
    their laptop is the refusal Shopify would give."""
    await local_backend.warm()
    with pytest.raises(ChangeNotApplicable, match="does not track inventory"):
        await local_backend.stage_inventory_action(
            session,
            [
                InventoryActionItem(
                    listing_id=listing_id(store, BROOM), action="restock", quantity=5
                )
            ],
        )


# -- End to end, through the portal the operator actually uses -------------------------


@pytest.fixture
def portal(local_settings):
    return create_merchant_portal(local_settings, InMemoryMemoryStore())


@pytest.fixture
def app(portal) -> FastAPI:
    app = FastAPI()
    app.include_router(portal.router, prefix="/api/merchant")
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="http://localhost")


async def test_the_operator_can_price_a_product_end_to_end(portal, app, client) -> None:
    """The demo, as a test. Read the catalog, stage a move, press the button the card shows,
    and read the catalog again — over the local store, with nothing mocked between the route
    and the price."""
    await portal.backend.warm()
    headers = {SESSION_HEADER: client.post("/api/merchant/session").json()["session_id"]}

    listings = client.get("/api/merchant/listings", headers=headers).json()["listings"]
    apron = next(item for item in listings if item["title"] == "Canvas tool apron")
    assert apron["price"] == 40.0

    context = MerchantSessionContext(
        session_id=headers[SESSION_HEADER], merchant_id=LOCAL_DOMAIN, operator="Operator"
    )
    staged = await portal.backend.stage_price_update(
        context, [PriceUpdateItem(listing_id=apron["listing_id"], new_price=44.0)]
    )

    # Still 40 on the store: the change is staged, not applied.
    assert client.get("/api/merchant/listings", headers=headers).json()["listings"]
    assert price_of(store_of(portal.backend), APRON) == ["40.00"]

    # The portal session has to have seen the change, as the assistant's own staging would
    # have left it, before the approval gate will let the button through.
    record = router_session_store(app).require(headers[SESSION_HEADER])
    record.state.remember_change(staged)
    router_session_store(app).save(record)

    response = client.post(f"/api/merchant/changes/{staged.change_id}/apply", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["change"]["status"] == "applied"

    assert price_of(store_of(portal.backend), APRON) == ["44.00"]
    after = client.get("/api/merchant/listings", headers=headers).json()["listings"]
    assert next(item for item in after if item["title"] == "Canvas tool apron")["price"] == 44.0
