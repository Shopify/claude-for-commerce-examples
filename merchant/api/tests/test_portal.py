# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The portal routes, which are the shared merchant routes.

Almost nothing here is this example's own code: ``build_merchant_router`` is used unchanged,
and the point of these tests is that it works over a live-store backend with no storefront
half behind it. What the example supplies is ``ShopifyStoreView``, which answers the three
things the merchant routes read from a storefront, and the home-page keys the overview
renders.

The approval routes get the most attention, because they are the surface an operator
actually approves a change on.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from commerce_common.memory import InMemoryMemoryStore
from demo_common import SESSION_HEADER
from merchant_agent import ChangeStatus, PriceUpdateItem
from merchant.api.agent_config import ShopifySettings
from merchant.api.merchant import create_merchant_portal

from .conftest import router_session_store
from .fake_admin import FakeAdmin

APRON = "gid://shopify/Product/1"
LADDER = "gid://shopify/Product/2"


@pytest.fixture
def portal(admin: FakeAdmin, settings):
    return create_merchant_portal(settings, InMemoryMemoryStore(), executor=admin)


@pytest.fixture
def app(portal) -> FastAPI:
    scope = FastAPI()
    scope.include_router(portal.router, prefix="/api/merchant")
    return scope


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="http://localhost")


@pytest.fixture
def operator(client: TestClient) -> dict[str, str]:
    return {SESSION_HEADER: client.post("/api/merchant/session").json()["session_id"]}


@pytest.fixture
def sessions(app: FastAPI):
    return router_session_store(app)


@pytest.fixture
async def warm(portal):
    """The overview's synchronous reads serve the caches its awaited reads fill; at runtime
    that happens in the app's lifespan."""
    await portal.backend.warm()
    return portal


def seen(sessions, headers: dict[str, str], change) -> str:
    """Give the portal session the provenance the assistant's own staging would have left,
    which is what the approval gate checks before the host's mark."""
    record = sessions.require(headers[SESSION_HEADER])
    record.state.remember_change(change)
    sessions.save(record)
    return change.change_id


# -- Reads ---------------------------------------------------------------------------


def test_health_names_the_store_and_the_skills(client: TestClient) -> None:
    body = client.get("/api/merchant/health").json()

    assert body["ok"] is True
    assert body["role"] == "merchant"
    assert body["skills"]
    # Before the first read the display name falls back to the configured store, so this is
    # the domain rather than the shop's own name.
    assert body["store"] == "acme-supply.myshopify.com"


def test_a_session_is_bound_to_the_one_store_the_process_serves(client: TestClient) -> None:
    body = client.post("/api/merchant/session").json()

    assert body["merchant_id"] == "acme-supply.myshopify.com"
    assert body["operator"] == "Dana"
    assert body["session_id"]


def test_the_portal_reads_need_a_session(client: TestClient) -> None:
    assert client.get("/api/merchant/overview").status_code == 401
    assert client.get("/api/merchant/listings").status_code == 401


async def test_the_overview_carries_the_shopify_home_page_keys(
    warm, client: TestClient, operator
) -> None:
    data = client.get("/api/merchant/overview", headers=operator).json()

    assert data["snapshot"]["currency"] == "USD"
    assert data["shop_domain"] == "acme-supply.myshopify.com"
    # The conversion card renders without a sparkline: orders carry no session data, and a
    # flat zero would read as measured.
    assert set(data["trends"]) == {"sales", "orders", "average_order_value"}
    assert all(len(points) == 30 for points in data["trends"].values())
    assert data["insights"]
    assert all(
        entry["insight_id"] and entry["headline"] and entry["prompt"] for entry in data["insights"]
    )


async def test_the_overview_names_which_store_answered(warm, client: TestClient, operator) -> None:
    """The overview line says which store the figures came from, so the payload has to carry
    it: the same page over the local store would otherwise read as a claim about a Shopify
    store. These settings describe a Shopify store, so this is the Shopify half."""
    body = client.get("/api/merchant/overview", headers=operator).json()

    assert body["store_kind"] == "shopify"


async def test_the_overview_says_so_when_the_local_store_answered(
    admin: FakeAdmin, settings: ShopifySettings
) -> None:
    """And the other half. Built here rather than from the fixtures, because the store a
    portal reads is fixed when it is created."""
    local = create_merchant_portal(
        ShopifySettings(**{**settings.__dict__, "local_store": True}),
        InMemoryMemoryStore(),
        executor=admin,
    )
    await local.backend.warm()
    scope = FastAPI()
    scope.include_router(local.router, prefix="/api/merchant")
    reader = TestClient(scope, base_url="http://localhost")
    headers = {SESSION_HEADER: reader.post("/api/merchant/session").json()["session_id"]}

    body = reader.get("/api/merchant/overview", headers=headers).json()

    assert body["store_kind"] == "local"


async def test_the_overview_shows_what_needs_attention(warm, client: TestClient, operator) -> None:
    attention = client.get("/api/merchant/overview", headers=operator).json()["needs_attention"]

    assert [alert["kind"] for alert in attention["inventory"]][0] == "low_stock"
    assert {issue["kind"] for issue in attention["order_issues"]} == {"delayed", "return_spike"}
    assert attention["pending_changes"] == []


async def test_the_overview_shows_the_store_s_own_recent_orders(
    warm, client: TestClient, operator
) -> None:
    """``ShopifyStoreView`` exists for this: the merchant routes want a recent order feed,
    and this example's orders are the store's real ones."""
    orders = client.get("/api/merchant/overview", headers=operator).json()["recent_orders"]

    assert len(orders) == 6
    assert orders[0]["order_id"] == "#1001"
    assert orders[0]["total"] == 120.0
    assert "delivered" not in {order["status"] for order in orders}


async def test_listings_serve_the_cached_catalog(warm, client: TestClient, operator) -> None:
    data = client.get("/api/merchant/listings", headers=operator).json()

    assert data["total"] == 7
    assert all(entry["currency"] == "USD" for entry in data["listings"])
    assert "gid://shopify/Product/8" not in {entry["listing_id"] for entry in data["listings"]}


async def test_a_listing_query_goes_through_the_backend_search(
    warm, client: TestClient, operator
) -> None:
    data = client.get("/api/merchant/listings", params={"query": "apron"}, headers=operator).json()

    assert [entry["listing_id"] for entry in data["listings"]] == [APRON]


def test_a_listing_detail_carries_its_pricing_context(client: TestClient, operator) -> None:
    """A Shopify global id in a URL path is the reason the shared detail route declares
    ``{listing_id:path}``: ``gid://shopify/Product/1`` has slashes, and percent-encoding
    them does not help, because the server decodes the path before the router matches it.
    Both forms are tested because the web client sends the encoded one."""
    for form in (APRON, quote(APRON, safe="")):
        data = client.get(f"/api/merchant/listings/{form}", headers=operator).json()

        assert data["listing"]["listing_id"] == APRON
        assert data["listing"]["title"] == "Canvas tool apron"
        assert data["pricing"]["current_price"] == 40.0
        assert data["pricing"]["max_price_delta_pct"] == 20.0


def test_an_unknown_listing_is_a_404(client: TestClient, operator) -> None:
    response = client.get("/api/merchant/listings/gid://shopify/Product/999", headers=operator)

    assert response.status_code == 404


def test_alerts_are_the_same_ones_the_agent_reads(client: TestClient, operator) -> None:
    data = client.get("/api/merchant/alerts", headers=operator).json()

    low = [alert for alert in data["inventory"] if alert["kind"] == "low_stock"]
    assert [alert["listing_id"] for alert in low] == ["gid://shopify/Product/3"]
    assert len(data["order_issues"]) == 2


# -- Approval ------------------------------------------------------------------------


async def test_the_preview_card_s_apply_button_writes_to_the_store(
    portal, client: TestClient, operator, sessions, session, admin: FakeAdmin
) -> None:
    """The full path: the assistant stages, the operator clicks Approve, and only then does
    anything reach Shopify."""
    staged = await portal.backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=APRON, new_price=44.0)]
    )
    change_id = seen(sessions, operator, staged)
    assert admin.calls == []

    body = client.post(f"/api/merchant/changes/{change_id}/apply", headers=operator).json()

    assert body["ok"] is True
    assert body["change"]["status"] == "applied"
    assert admin.mutation_names() == ["SetVariantPrices"]
    record = sessions.require(operator[SESSION_HEADER])
    assert record.pending_app_events == [
        f"Operator approved and applied change {change_id} from the preview card."
    ]


async def test_the_approval_mark_does_not_outlive_the_click(
    portal, client: TestClient, operator, sessions, session
) -> None:
    """The route marks the id, runs the executor, and unmarks it whatever happened, so a
    later chat turn cannot spend the same approval on another change."""
    staged = await portal.backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=APRON, new_price=44.0)]
    )
    change_id = seen(sessions, operator, staged)

    client.post(f"/api/merchant/changes/{change_id}/apply", headers=operator)

    record = sessions.require(operator[SESSION_HEADER])
    assert record.state.approved_change_ids == set()
    assert record.state.host_action_change_ids == set()


async def test_a_change_this_session_never_saw_is_held_rather_than_applied(
    portal, client: TestClient, operator, session, admin: FakeAdmin
) -> None:
    """Staged out of band — by another session, or before a restart — the change has no
    provenance in this one, so the button reports a hold instead of writing."""
    staged = await portal.backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=APRON, new_price=44.0)]
    )

    body = client.post(f"/api/merchant/changes/{staged.change_id}/apply", headers=operator).json()

    assert body["ok"] is False
    assert body["change"] is None
    assert "was not staged or listed in this session" in body["reason"]
    assert admin.calls == []
    assert portal.backend.ledger.get(staged.change_id).status is ChangeStatus.STAGED


def test_an_unknown_change_id_is_a_hold_not_a_failure(client: TestClient, operator) -> None:
    held = client.post("/api/merchant/changes/chg-none/apply", headers=operator)
    dismissed = client.post("/api/merchant/changes/chg-none/discard", headers=operator)

    assert held.status_code == 200
    assert held.json()["ok"] is False
    assert dismissed.json()["ok"] is False
    assert "nothing to discard" in dismissed.json()["reason"]


async def test_a_write_the_store_refuses_is_a_400_and_leaves_the_change_staged(
    portal, client: TestClient, operator, sessions, session, admin: FakeAdmin
) -> None:
    admin.user_errors["SetVariantPrices"] = [{"field": ["price"], "message": "Price is invalid"}]
    staged = await portal.backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=APRON, new_price=44.0)]
    )
    change_id = seen(sessions, operator, staged)

    response = client.post(f"/api/merchant/changes/{change_id}/apply", headers=operator)

    assert response.status_code == 400
    assert portal.backend.ledger.get(change_id).status is ChangeStatus.STAGED
    assert sessions.require(operator[SESSION_HEADER]).pending_app_events == []


async def test_a_change_cannot_be_approved_twice_from_the_card(
    portal, client: TestClient, operator, sessions, session, admin: FakeAdmin
) -> None:
    staged = await portal.backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=APRON, new_price=44.0)]
    )
    change_id = seen(sessions, operator, staged)

    client.post(f"/api/merchant/changes/{change_id}/apply", headers=operator)
    again = client.post(f"/api/merchant/changes/{change_id}/apply", headers=operator)

    assert again.status_code == 400
    assert len(admin.calls) == 1


async def test_the_dismiss_button_records_the_operator_as_the_actor(
    portal, client: TestClient, operator, sessions, session, admin: FakeAdmin
) -> None:
    staged = await portal.backend.stage_price_update(
        session, [PriceUpdateItem(listing_id=LADDER, new_price=66.0)]
    )
    change_id = seen(sessions, operator, staged)

    body = client.post(f"/api/merchant/changes/{change_id}/discard", headers=operator).json()

    assert body["ok"] is True
    assert body["change"]["status"] == "discarded"
    assert body["change"]["discarded_by_kind"] == "operator"
    assert admin.calls == []


# -- The process without a store ----------------------------------------------------


def test_the_app_starts_without_credentials_and_says_what_is_missing(monkeypatch) -> None:
    """The first thing anyone running this example hits. A missing credential must not fail
    at import: the process starts, the health route names every variable that would do and
    the file they go in, and the operator has something to act on instead of a stack
    trace."""
    # Imported before the variables are cleared: the module reads the example's ``.env`` at
    # import time, and on a machine that has one that would put the credentials back.
    from merchant.api.main import create_app  # noqa: PLC0415

    # ``SHOPIFY_LOCAL_STORE`` goes too: this is the third state, where neither store is
    # configured, and it is the one a reader reaches by half-filling the file.
    for name in (
        "SHOPIFY_SHOP_DOMAIN",
        "SHOPIFY_CLIENT_ID",
        "SHOPIFY_CLIENT_SECRET",
        "SHOPIFY_ADMIN_TOKEN",
        "SHOPIFY_LOCAL_STORE",
    ):
        monkeypatch.delenv(name, raising=False)

    app = create_app()
    # Only the health route: every other route needs a store to talk to.
    assert [route.path for route in app.routes if isinstance(route, APIRoute)] == [
        "/api/merchant/health"
    ]

    with TestClient(app, base_url="http://localhost") as client:
        body = client.get("/api/merchant/health").json()

    assert body["ok"] is False
    assert "SHOPIFY_SHOP_DOMAIN" in body["error"]
    assert "SHOPIFY_CLIENT_ID" in body["error"]
    assert "SHOPIFY_CLIENT_SECRET" in body["error"]
    assert "SHOPIFY_ADMIN_TOKEN" in body["error"]
    assert ".env" in body["error"]
