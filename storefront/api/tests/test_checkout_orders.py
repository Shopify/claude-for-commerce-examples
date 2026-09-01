# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Checkout and orders over the recorded shop: the host path's lazy checkout staging (never
completion), the checkout-to-order ledger, the degrade path while the key's orders
scope is pending (the order tools return nothing), and the order envelope's
rendering."""

from datetime import UTC, datetime

import httpx
import pytest

from storefront.api.agent_config import build_shopping_config
from storefront.api.identity import ORDERS_TOKEN_SCOPE, AgentToken, ShopSignIn
from storefront.api.shopify_backend import ShopifyStorefrontBackend
from storefront.api.ucp_client import UcpError
from shopping_agent import OrderStatus

from .conftest import SEARCH_QUERY
from .replay import COMPLETED_CHECKOUT_ID, DEGRADE_ORDER_ID, FIXTURE_ORDER_ID

PRODUCT_ID = "gid://shopify/Product/7983592374294"
VARIANT_ID = "gid://shopify/ProductVariant/43696933273622"


@pytest.fixture
def linked_backend(client, signin, session) -> ShopifyStorefrontBackend:
    """A backend with both identity planes: Sign in with Shop (buyer IP noted, as the
    host does per request) and the deployment's agent token, both over fakes."""
    signin.note_buyer_ip(session.session_id, "203.0.113.7")
    return ShopifyStorefrontBackend(client, identity=signin, agent_token=AgentToken(signin))


async def add_one(backend, session) -> None:
    products = await backend.search_products(session, SEARCH_QUERY, limit=3)
    await backend.add_to_cart(session, products[0].product_id, 1)


def spy_calls(backend) -> list[str]:
    """Record every UCP tool name the backend sends from here on."""
    names: list[str] = []
    original = backend.client.call_ucp

    async def spy(name, arguments, **kwargs):
        names.append(name)
        return await original(name, arguments, **kwargs)

    backend.client.call_ucp = spy
    return names


# -- checkout staging on the host path ----------------------------------------------------


async def test_checkout_url_for_stages_a_nonempty_cart_and_returns_its_handoff_url(
    backend, session
):
    await add_one(backend, session)
    url = await backend.checkout_url_for(session.session_id)
    state = backend._sessions[session.session_id]
    assert state.checkout_id and state.checkout_id.startswith("gid://shopify/Checkout/")
    assert url == state.checkout_handoff_url
    assert "/cart/c/" in url


async def test_an_unknown_session_or_empty_cart_stages_nothing(backend, session):
    assert await backend.checkout_url_for("s-unknown") is None
    backend._state(session)  # a session that has not touched the cart
    names = spy_calls(backend)
    assert await backend.checkout_url_for(session.session_id) is None
    assert names == []  # nothing crossed the wire


async def test_a_current_handoff_is_reused_without_another_staging_call(backend, session):
    await add_one(backend, session)
    first = await backend.checkout_url_for(session.session_id)
    names = spy_calls(backend)
    assert await backend.checkout_url_for(session.session_id) == first
    assert names == []


async def test_a_cart_write_invalidates_the_handoff_until_the_next_ask_restages(backend, session):
    await add_one(backend, session)
    await backend.checkout_url_for(session.session_id)
    state = backend._sessions[session.session_id]
    staged = state.checkout_id

    await backend.update_cart_item(session, VARIANT_ID, 2)
    assert state.checkout_handoff_url is None

    url = await backend.checkout_url_for(session.session_id)  # update_checkout re-syncs
    assert state.checkout_id == staged
    assert url == state.checkout_handoff_url


async def test_staging_failure_falls_back_to_the_carts_own_link(backend, session):
    await add_one(backend, session)
    cart_url = backend._sessions[session.session_id].checkout_url

    async def down(name, arguments, **kwargs):
        raise UcpError(f"{name}: unavailable")

    backend.client.call_ucp = down
    assert await backend.checkout_url_for(session.session_id) == cart_url


async def test_complete_checkout_is_never_called(linked_backend, session):
    """The hard line: staging, ledger sync, and order reads never place an order or
    take payment — ``complete_checkout`` never crosses the wire."""
    names = spy_calls(linked_backend)
    await add_one(linked_backend, session)
    await linked_backend.checkout_url_for(session.session_id)
    state = linked_backend._sessions[session.session_id]
    state.checkout_id = COMPLETED_CHECKOUT_ID  # the buyer finished on Shopify's page
    await linked_backend.get_orders(session)
    await linked_backend.get_order(session, FIXTURE_ORDER_ID)
    assert "create_checkout" in names and "get_order" in names
    assert "complete_checkout" not in names


# -- the checkout-to-order ledger --------------------------------------------------------


async def test_an_incomplete_checkout_maps_no_order(backend, session):
    await add_one(backend, session)
    await backend.checkout_url_for(session.session_id)
    assert await backend.get_orders(session) == []  # recorded checkout: order is null
    assert backend._sessions[session.session_id].order_of_checkout == {}


async def test_a_completed_checkout_maps_and_renders_its_order(linked_backend, session):
    state = linked_backend._state(session)
    state.checkout_id = COMPLETED_CHECKOUT_ID
    orders = await linked_backend.get_orders(session)
    assert state.order_of_checkout == {COMPLETED_CHECKOUT_ID: FIXTURE_ORDER_ID}
    assert FIXTURE_ORDER_ID in state.order_seen_at

    [order] = orders
    assert order.order_id == FIXTURE_ORDER_ID
    assert order.status is OrderStatus.SHIPPED  # last event: in_transit
    assert (order.total, order.currency) == (96.05, "CAD")  # 9605 minor units
    assert [(i.product_id, i.quantity, i.price) for i in order.items] == [(VARIANT_ID, 2, 40.0)]
    assert order.items[0].title == "Women's T-shirt"
    assert order.tracking_url == "https://track.acme.example/ACME0001042"
    # No placed timestamp in the envelope: the earliest fulfillment event stands in.
    assert order.placed_at == datetime(2026, 8, 20, 14, 5, tzinfo=UTC)


async def test_get_order_serves_only_the_sessions_own_orders(linked_backend, session):
    state = linked_backend._state(session)
    state.checkout_id = COMPLETED_CHECKOUT_ID
    found = await linked_backend.get_order(session, FIXTURE_ORDER_ID)
    assert found is not None and found.order_id == FIXTURE_ORDER_ID
    assert await linked_backend.get_order(session, "gid://shopify/Order/9999") is None


# -- the degrade path while the orders scope is pending ----------------------------------


async def test_a_pending_orders_scope_degrades_to_empty_orders(linked_backend, session):
    # The recorded live answer: orders_not_allowed, regardless of the order id.
    state = linked_backend._state(session)
    state.order_of_checkout["gid://shopify/Checkout/x"] = DEGRADE_ORDER_ID
    assert await linked_backend.get_orders(session) == []
    assert linked_backend.orders_enabled is False
    assert await linked_backend.get_order(session, DEGRADE_ORDER_ID) is None


async def test_a_flipped_orders_flag_stops_further_order_calls(linked_backend, session):
    state = linked_backend._state(session)
    state.order_of_checkout["gid://shopify/Checkout/x"] = DEGRADE_ORDER_ID
    await linked_backend.get_orders(session)  # orders_not_allowed flips the flag

    names = spy_calls(linked_backend)
    assert await linked_backend.get_orders(session) == []
    assert "get_order" not in names


async def test_orders_without_an_agent_token_return_nothing(backend, session):
    state = backend._state(session)
    state.order_of_checkout["gid://shopify/Checkout/x"] = FIXTURE_ORDER_ID
    assert await backend.get_orders(session) == []
    assert backend.orders_enabled is True  # missing config, not the scope flag


def test_the_config_tells_the_model_how_to_handle_unavailable_order_tracking():
    notes = build_shopping_config("demostore.mock.shop").domain_search_notes
    assert "when the order tools return nothing" in notes
    assert "confirmation email" in notes


# -- the agent token ---------------------------------------------------------------------


async def test_the_agent_token_is_minted_once_and_cached(oauth, signin):
    token = AgentToken(signin)
    first = await token.bearer()
    assert first == await token.bearer()
    mints = [form for _, form in oauth.posts if form["grant_type"] == "client_credentials"]
    assert len(mints) == 1
    assert mints[0]["scope"] == ORDERS_TOKEN_SCOPE


async def test_the_agent_token_is_none_without_credentials(oauth, monkeypatch):
    monkeypatch.delenv("SHOPIFY_UCP_CLIENT_ID", raising=False)
    monkeypatch.delenv("SHOPIFY_UCP_CLIENT_SECRET", raising=False)
    signin = ShopSignIn(http=httpx.AsyncClient(transport=oauth.transport()))
    assert await AgentToken(signin).bearer() is None
    assert oauth.posts == []  # no credentials: nothing crosses the wire


async def test_a_failed_mint_degrades_to_none(oauth, signin):
    oauth.fail_grants.add("client_credentials")
    assert await AgentToken(signin).bearer() is None
