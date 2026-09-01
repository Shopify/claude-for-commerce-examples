# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The backend with sign-in wired: catalog calls carry the buyer token and buyer IP
together, cart and policy calls never do, a 401 re-mints once and retries, a failed
re-mint falls back to anonymous, and the guest wire traffic stays byte-identical to a
backend with no identity at all."""

import json

import httpx
import pytest

from storefront.api.shopify_backend import ShopifyStorefrontBackend
from storefront.api.ucp_client import UcpClient

from .conftest import SEARCH_QUERY
from .oauth_stub import TOKEN_EXCHANGE_GRANT
from .replay import replay_transport

PRODUCT_ID = "gid://shopify/Product/7983592374294"
REDIRECT = "http://localhost:8004/api/auth/shop/callback"
IP = "203.0.113.7"


class RecordingShop:
    """The replay transport, recording every request; bearer calls whose token is in
    ``reject_tokens`` get a 401 instead of their fixture."""

    def __init__(self) -> None:
        self._inner = replay_transport()
        self.requests: list[httpx.Request] = []
        self.reject_tokens: set[str] = set()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token in self.reject_tokens:
            return httpx.Response(401)
        return self._inner.handler(request)

    def catalog_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/api/ucp/mcp"]


@pytest.fixture
def shop() -> RecordingShop:
    return RecordingShop()


def make_backend(shop: RecordingShop, signin=None) -> ShopifyStorefrontBackend:
    client = UcpClient(http=httpx.AsyncClient(transport=shop.transport()))
    return ShopifyStorefrontBackend(client, identity=signin)


async def sign_in(signin, session_id: str = "s-1") -> None:
    await signin.complete(session_id, "code-1", REDIRECT)
    signin.note_buyer_ip(session_id, IP)


async def test_signed_in_catalog_calls_carry_the_bearer_and_buyer_ip_pair(shop, signin, session):
    backend = make_backend(shop, signin)
    await sign_in(signin)
    await backend.search_products(session, SEARCH_QUERY, limit=3)
    request = shop.catalog_requests()[-1]
    assert request.headers["authorization"] == "Bearer buyer-token-1"
    assert request.headers["shopify-buyer-ip"] == IP


async def test_cart_and_policy_calls_stay_guest_even_when_signed_in(shop, signin, session):
    backend = make_backend(shop, signin)
    await sign_in(signin)
    await backend.search_products(session, SEARCH_QUERY, limit=3)
    await backend.add_to_cart(session, PRODUCT_ID, 1)
    await backend.get_cart(session)
    await backend.search_policies(session, "return policy")

    def tool(request: httpx.Request) -> str:
        return json.loads(request.content)["params"]["name"]

    guest_calls = [
        r
        for r in shop.requests
        if r.url.path == "/api/mcp" or tool(r) in {"create_cart", "update_cart", "get_cart"}
    ]
    assert len(guest_calls) >= 3  # the cart pair and the policies read
    for request in guest_calls:
        assert "authorization" not in request.headers
        assert "shopify-buyer-ip" not in request.headers


async def test_a_401_re_mints_once_and_retries(shop, signin, oauth, session):
    backend = make_backend(shop, signin)
    await sign_in(signin)
    shop.reject_tokens.add("buyer-token-1")
    products = await backend.search_products(session, SEARCH_QUERY, limit=3)
    assert products
    assert oauth.minted == 2
    assert shop.catalog_requests()[-1].headers["authorization"] == "Bearer buyer-token-2"
    assert signin.signed_in(session.session_id)


async def test_a_failed_re_mint_signs_out_and_continues_anonymous(shop, signin, oauth, session):
    backend = make_backend(shop, signin)
    await sign_in(signin)
    shop.reject_tokens.add("buyer-token-1")
    oauth.fail_grants.add(TOKEN_EXCHANGE_GRANT)
    products = await backend.search_products(session, SEARCH_QUERY, limit=3)
    assert products
    last = shop.catalog_requests()[-1]
    assert "authorization" not in last.headers
    assert "shopify-buyer-ip" not in last.headers
    assert not signin.signed_in(session.session_id)


async def test_guest_requests_are_byte_identical_with_identity_wired(signin, session):
    guest_shop, wired_shop = RecordingShop(), RecordingShop()
    guest = make_backend(guest_shop)  # guest construction: no identity at all
    wired = make_backend(wired_shop, signin)  # identity wired, session never signed in
    await guest.search_products(session, SEARCH_QUERY, limit=3)
    await wired.search_products(session, SEARCH_QUERY, limit=3)
    before, after = guest_shop.catalog_requests()[-1], wired_shop.catalog_requests()[-1]
    assert after.content == before.content
    assert dict(after.headers) == dict(before.headers)
    assert "authorization" not in after.headers


async def test_reset_session_drops_the_shop_identity(shop, signin, session):
    backend = make_backend(shop, signin)
    await sign_in(signin)
    backend.reset_session(session.session_id)
    assert not signin.signed_in(session.session_id)
