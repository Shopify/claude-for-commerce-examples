# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Joining a cart that exists already: the session binds to the storefront's cart id,
reads it as its starting point, and every write re-reads first so a change the
storefront made in between is never overwritten."""

from __future__ import annotations

import json
from typing import Any

import httpx

from storefront.api.shopify_backend import ShopifyStorefrontBackend, cart_gid
from storefront.api.ucp_client import UcpClient

from .replay import GONE_CART_ID, fixture_payload

LIVE_CART_ID = fixture_payload("/api/ucp/mcp create_cart create")["id"]
A, B, C = (f"gid://shopify/ProductVariant/{n}" for n in (11, 22, 33))


def test_cart_gid_takes_the_storefront_cookie_value_or_the_gid():
    gid = "gid://shopify/Cart/abc123?key=0f0f0f0f"
    assert cart_gid(gid) == gid
    assert cart_gid("abc123?key=0f0f0f0f") == gid
    assert cart_gid("abc123%3Fkey%3D0f0f0f0f") == gid  # the cookie is percent-encoded
    assert cart_gid(" abc123?key=0f0f0f0f\n") == gid


async def test_attach_binds_the_session_to_the_storefronts_cart(backend, session):
    assert backend.cart_id_for(session.session_id) is None
    backend._state(session).checkout_id = "gid://shopify/Checkout/stale"

    cart = await backend.attach_cart(session.session_id, LIVE_CART_ID)

    assert cart is not None and cart.item_count == 1
    assert backend.cart_id_for(session.session_id) == LIVE_CART_ID
    assert (await backend.get_cart(session)).items[0].product_id == cart.items[0].product_id
    # The checkout staged for whatever cart came before does not describe this one.
    assert backend._state(session).checkout_id is None


async def test_attaching_a_cart_the_shop_does_not_know_leaves_the_session_alone(
    backend, session
):
    assert await backend.attach_cart(session.session_id, GONE_CART_ID) is None
    assert backend.cart_id_for(session.session_id) is None
    assert (await backend.get_cart(session)).items == []


class SharedShop:
    """A UCP cart endpoint whose cart another writer can change between our calls —
    what a storefront's own cart is once the agent joins it."""

    def __init__(self, cart_id: str, lines: dict[str, int]) -> None:
        self.cart_id = cart_id
        self.lines = dict(lines)
        self.updates: list[list[dict[str, Any]]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        params = body["params"]
        if params["name"] == "update_cart":
            sent = params["arguments"]["cart"]["line_items"]
            self.updates.append(sent)
            self.lines = {
                line["item"]["id"]: line["quantity"] for line in sent if line["quantity"] > 0
            }
        elif params["name"] != "get_cart":
            raise AssertionError(f"unexpected tool {params['name']}")
        document = {
            "ucp": {"version": "2026-04-08"},
            "id": self.cart_id,
            "currency": "CAD",
            "continue_url": "https://shop.example/cart/c/shared",
            "line_items": [
                {
                    "id": f"line-{variant.rsplit('/', 1)[1]}",
                    "item": {"id": variant, "title": variant, "price": 1000},
                    "quantity": quantity,
                }
                for variant, quantity in self.lines.items()
            ],
        }
        result = {
            "content": [{"type": "text", "text": json.dumps(document)}],
            "isError": False,
            "structuredContent": document,
        }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})


def lines_of(cart) -> set[tuple[str, int]]:
    return {(item.product_id, item.quantity) for item in cart.items}


async def test_writes_reread_the_cart_so_another_writers_lines_survive(session):
    shop = SharedShop("gid://shopify/Cart/shared?key=0f0f0f0f", {A: 1})
    backend = ShopifyStorefrontBackend(
        UcpClient(http=httpx.AsyncClient(transport=shop.transport()))
    )
    assert lines_of(await backend.attach_cart(session.session_id, shop.cart_id)) == {(A, 1)}

    shop.lines[B] = 2  # the storefront adds B while the agent is thinking
    cart = await backend.update_cart_item(session, A, 3)
    assert lines_of(cart) == {(A, 3), (B, 2)}
    assert {(line["item"]["id"], line["quantity"]) for line in shop.updates[-1]} == {
        (A, 3),
        (B, 2),
    }

    shop.lines[C] = 1
    assert lines_of(await backend.remove_from_cart(session, A)) == {(B, 2), (C, 1)}

    del shop.lines[C]
    assert lines_of(await backend.add_to_cart(session, B, 1)) == {(B, 3)}
