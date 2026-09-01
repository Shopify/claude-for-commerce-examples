# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The backend over the recorded shop: catalog mapping, the cart's session lifecycle,
and the executor's gates holding over live-shaped ids."""

from commerce_common.skills import SkillRegistry
from storefront.api.agent_config import build_shopping_config
from shopping_agent import SearchFilters, ShoppingSessionContext
from shopping_agent.executor import ShoppingToolExecutor

from .conftest import SEARCH_QUERY
from .replay import GONE_CART_ID

PRODUCT_ID = "gid://shopify/Product/7983592374294"
VARIANT_ID = "gid://shopify/ProductVariant/43696933273622"


async def search(backend, session):
    return await backend.search_products(session, SEARCH_QUERY, limit=3)


# -- catalog ---------------------------------------------------------------------------


async def test_search_maps_products_to_gid_ids_and_major_unit_prices(backend, session):
    products = await search(backend, session)
    assert [p.product_id for p in products][0] == PRODUCT_ID
    first = products[0]
    assert first.title == "Women's T-shirt"
    assert (first.price, first.currency) == (40.0, "CAD")  # 4000 minor units
    assert first.in_stock
    assert first.image_url and first.image_url.startswith("https://cdn.shopify.com/")
    assert first.short_description


async def test_details_list_variants_as_products_under_variant_gids(backend, session):
    details = await backend.get_product_details(session, PRODUCT_ID)
    assert details is not None
    assert details.variants, "variants are the purchasable options"
    variant = details.variants[0]
    assert variant.product_id == VARIANT_ID
    assert variant.attributes == {"Size": "Small", "Color": "Green"}
    assert variant.price == 40.0


async def test_details_keep_the_search_results_fuller_variant_list(backend, session):
    # search lists every variant; get_product resolves to the selected one. The
    # cached record must not lose the list to the narrower response.
    products = await search(backend, session)
    from_search = len(backend.products[PRODUCT_ID].variants)
    assert from_search > 1
    details = await backend.get_product_details(session, products[0].product_id)
    assert len(details.variants) == from_search


async def test_a_seen_variant_id_resolves_to_its_own_details_record(backend, session):
    await backend.get_product_details(session, PRODUCT_ID)
    details = await backend.get_product_details(session, VARIANT_ID)
    assert details is not None
    assert details.product_id == VARIANT_ID
    assert details.title == "Women's T-shirt — Small / Green"


async def test_an_unknown_product_id_is_none_not_an_error(backend, session):
    assert await backend.get_product_details(session, "gid://shopify/Product/1") is None
    assert await backend.get_product_details(session, "gid://shopify/ProductVariant/999") is None


async def test_price_filters_travel_as_minor_units(backend, session, client):
    sent = {}
    original = client.call_ucp

    async def spy(name, arguments):
        sent.update(arguments)
        return await original(name, arguments)

    client.call_ucp = spy
    filters = SearchFilters(min_price=10.0, max_price=50.0)
    await backend.search_products(session, SEARCH_QUERY, filters=filters, limit=3)
    assert sent["catalog"]["filters"] == {"price": {"min": 1000, "max": 5000}}


# -- cart ------------------------------------------------------------------------------


async def test_the_cart_lifecycle_keeps_one_shopify_cart_per_session(backend, session):
    assert (await backend.get_cart(session)).items == []  # no cart id: no network

    products = await search(backend, session)
    cart = await backend.add_to_cart(session, products[0].product_id, 1)
    assert [(i.product_id, i.quantity, i.price) for i in cart.items] == [(VARIANT_ID, 1, 40.0)]
    assert cart.items[0].title == "Women's T-shirt - Small / Green"
    assert cart.currency == "CAD"
    assert "/cart/c/" in (await backend.checkout_url_for(session.session_id))

    read_back = await backend.get_cart(session)
    assert read_back.item_count == 1

    updated = await backend.update_cart_item(session, VARIANT_ID, 2)
    assert updated.item_count == 2

    removed = await backend.remove_from_cart(session, VARIANT_ID)
    assert removed.items == []


async def test_adding_a_second_product_keeps_the_first_line(backend, session):
    # update_cart's line list replaces the cart's contents, so the add must send the
    # existing line beside the new one.
    products = await search(backend, session)
    await backend.add_to_cart(session, products[0].product_id, 1)
    cart = await backend.add_to_cart(session, products[1].product_id, 1)
    assert len(cart.items) == 2
    assert {i.quantity for i in cart.items} == {1}


async def test_adding_a_variant_id_directly_skips_default_resolution(backend, session):
    await search(backend, session)
    cart = await backend.add_to_cart(session, VARIANT_ID, 1)
    assert cart.items[0].product_id == VARIANT_ID


async def test_updating_a_line_the_cart_does_not_hold_leaves_it_as_it_is(backend, session):
    await search(backend, session)
    await backend.add_to_cart(session, PRODUCT_ID, 1)
    cart = await backend.update_cart_item(session, "gid://shopify/ProductVariant/999", 3)
    assert cart.item_count == 1


async def test_reset_session_drops_the_cart_binding(backend, session):
    await search(backend, session)
    await backend.add_to_cart(session, PRODUCT_ID, 1)
    backend.reset_session(session.session_id)
    assert (await backend.get_cart(session)).items == []
    assert await backend.checkout_url_for(session.session_id) is None


async def test_sessions_do_not_share_carts_or_provenance_maps(backend, session):
    await search(backend, session)
    await backend.add_to_cart(session, PRODUCT_ID, 1)
    other = ShoppingSessionContext(session_id="s-2", user_id="guest")
    assert (await backend.get_cart(other)).items == []


# -- a cart id the shop no longer accepts ------------------------------------------------


async def seed_gone_cart(backend, session) -> None:
    """A session whose cart the shop has since dropped (replayed ``cart_not_found``)."""
    await search(backend, session)
    await backend.add_to_cart(session, PRODUCT_ID, 1)
    backend._sessions[session.session_id].cart_id = GONE_CART_ID


async def test_a_dropped_cart_reads_as_empty_and_unbinds(backend, session):
    await seed_gone_cart(backend, session)
    cart = await backend.get_cart(session)
    assert cart.items == []
    # The binding is gone too: the next read stays empty without touching the shop.
    assert backend._sessions[session.session_id].cart_id is None
    assert (await backend.get_cart(session)).items == []


async def test_adding_into_a_dropped_cart_starts_a_fresh_one(backend, session):
    await seed_gone_cart(backend, session)
    cart = await backend.add_to_cart(session, PRODUCT_ID, 1)
    assert [(i.product_id, i.quantity) for i in cart.items] == [(VARIANT_ID, 1)]
    assert backend._sessions[session.session_id].cart_id != GONE_CART_ID


async def test_updating_a_dropped_cart_returns_the_empty_cart(backend, session):
    await seed_gone_cart(backend, session)
    cart = await backend.update_cart_item(session, VARIANT_ID, 2)
    assert cart.items == []
    assert backend._sessions[session.session_id].cart_id is None


# -- the anonymous surfaces ------------------------------------------------------------


async def test_the_anonymous_surfaces_are_static(backend, session):
    preferences = await backend.get_preferences(session)
    assert (preferences.user_id, preferences.display_name) == ("guest", "Guest")
    assert await backend.get_orders(session) == []
    assert await backend.get_order(session, "any") is None
    assert await backend.get_fulfillment_options(session, [PRODUCT_ID]) == []
    assert backend.recent_orders() == []


async def test_policies_map_the_shop_answers(backend, session):
    # The demo shop publishes none; the mapping returns the empty list, not an error.
    assert await backend.search_policies(session, "return policy") == []


# -- the executor's gates over live-shaped ids ------------------------------------------


def make_executor(backend, session, state) -> ShoppingToolExecutor:
    return ShoppingToolExecutor(
        backend=backend,
        config=build_shopping_config("demostore.mock.shop"),
        skills=SkillRegistry([]),
        session=session,
        state=state,
    )


async def test_cart_writes_hold_without_provenance_and_pass_with_it(backend, session, state):
    executor = make_executor(backend, session, state)
    held = await executor.execute("add_to_cart", {"product_id": PRODUCT_ID, "quantity": 1})
    assert held.blocked

    await executor.execute("search_products", {"query": SEARCH_QUERY})
    assert PRODUCT_ID in state.seen_products
    added = await executor.execute("add_to_cart", {"product_id": PRODUCT_ID, "quantity": 1})
    assert not added.refused

    # The line is the variant; membership in the cart is what lets writes touch it.
    updated = await executor.execute("update_cart_item", {"product_id": VARIANT_ID, "quantity": 2})
    assert not updated.refused
    removed = await executor.execute("remove_from_cart", {"product_id": VARIANT_ID})
    assert not removed.refused


async def test_variant_ids_enter_provenance_through_their_details_record(backend, session, state):
    executor = make_executor(backend, session, state)
    await executor.execute("get_product_details", {"product_id": PRODUCT_ID})
    held = await executor.execute("get_product_details", {"product_id": VARIANT_ID})
    assert not held.refused
    assert VARIANT_ID in state.seen_products
    added = await executor.execute("add_to_cart", {"product_id": VARIANT_ID, "quantity": 1})
    assert not added.refused
