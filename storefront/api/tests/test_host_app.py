# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The wired app, on the routes that reach no network: a fresh session's cart is empty
with no checkout link, the public catalog read serves the (lazily filled) cache, and
the Sign in with Shop routes run against the fake OAuth surface."""

from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from demo_common import SESSION_HEADER
from storefront.api import main as main_module
from storefront.api.ucp_client import UcpClient
from shopping_agent.types import ProductDetails

from .oauth_stub import FakeShopOAuth
from .replay import GONE_CART_ID, fixture_payload, replay_transport


@pytest.fixture(scope="module")
def client():
    return TestClient(main_module.app, base_url="http://localhost")


def start(client) -> dict[str, str]:
    token = client.post("/api/session", json={"user_id": "guest"}).json()["session_id"]
    return {SESSION_HEADER: token}


def test_health_and_session(client):
    health = client.get("/api/health").json()
    assert health["ok"] is True
    assert health["store"] == "demostore.mock.shop"
    assert start(client)


def test_the_catalog_detail_route_takes_a_gid(client):
    # Shopify ids are gids with slashes; the web app sends them percent-encoded.
    gid = "gid://shopify/Product/7983592374294"
    product = ProductDetails(product_id=gid, title="Fake Anorak", price=59.0, currency="USD")
    main_module.backend.products[gid] = product
    try:
        response = client.get(f"/api/products/{quote(gid, safe='')}")
        assert response.status_code == 200
        assert response.json()["product_id"] == gid
        assert client.get("/api/products/gid%3A%2F%2Fshopify%2FProduct%2F404").status_code == 404
    finally:
        main_module.backend.products.pop(gid, None)


def test_a_fresh_session_has_an_empty_cart_and_no_checkout_url(client):
    payload = client.get("/api/cart", headers=start(client)).json()
    assert payload["items"] == []
    assert payload["checkout_url"] is None


def test_the_cart_payload_carries_the_backends_staged_handoff_url(client, monkeypatch):
    # checkout_url_for stages lazily and is async; the host awaits it per cart payload.
    headers = start(client)
    sid = headers[SESSION_HEADER]

    async def staged(session_id):
        assert session_id == sid
        return "https://shop.example/checkouts/cn/123"

    monkeypatch.setattr(main_module.backend, "checkout_url_for", staged)
    payload = client.get("/api/cart", headers=headers).json()
    assert payload["checkout_url"] == "https://shop.example/checkouts/cn/123"


def test_the_direct_add_button_holds_without_provenance(client, monkeypatch):
    # Replay transport instead of the live shop, in case the gate ever lets the call through.
    monkeypatch.setattr(
        main_module.backend,
        "client",
        UcpClient(http=httpx.AsyncClient(transport=replay_transport())),
    )
    response = client.post(
        "/api/cart/add",
        json={"product_id": "gid://shopify/Product/7983592374294", "quantity": 1},
        headers=start(client),
    )
    assert response.status_code == 400


def test_attach_binds_the_session_to_a_storefront_cart(client, monkeypatch):
    monkeypatch.setattr(
        main_module.backend,
        "client",
        UcpClient(http=httpx.AsyncClient(transport=replay_transport())),
    )
    headers = start(client)
    assert client.get("/api/cart", headers=headers).json()["cart_id"] is None

    cart_id = fixture_payload("/api/ucp/mcp create_cart create")["id"]
    # The value a storefront's `cart` cookie holds: the token and key, percent-encoded.
    cookie_value = quote(cart_id.removeprefix("gid://shopify/Cart/"), safe="")
    attached = client.post("/api/cart/attach", json={"cart_id": cookie_value}, headers=headers)
    assert attached.status_code == 200
    assert attached.json()["item_count"] == 1
    assert attached.json()["cart_id"] == cart_id
    assert client.get("/api/cart", headers=headers).json()["cart_id"] == cart_id

    unknown = client.post("/api/cart/attach", json={"cart_id": GONE_CART_ID}, headers=headers)
    assert unknown.status_code == 404
    assert client.get("/api/cart", headers=headers).json()["cart_id"] == cart_id


# -- Sign in with Shop -------------------------------------------------------------


def test_a_fresh_session_is_signed_out(client):
    assert client.get("/api/auth/status", headers=start(client)).json() == {"signed_in": False}


def test_signin_start_requires_a_session(client):
    assert client.get("/api/auth/shop/start", follow_redirects=False).status_code == 401


def test_signin_start_without_credentials_says_whats_missing(client, monkeypatch):
    monkeypatch.delenv("SHOPIFY_UCP_CLIENT_ID", raising=False)
    monkeypatch.delenv("SHOPIFY_UCP_CLIENT_SECRET", raising=False)
    response = client.get(
        "/api/auth/shop/start",
        params={"session_id": start(client)[SESSION_HEADER]},
        follow_redirects=False,
    )
    assert response.status_code == 503
    assert "SHOPIFY_UCP_CLIENT_ID" in response.json()["detail"]


def test_the_callback_bounces_an_unknown_state_to_the_web_app_with_the_error_flag(client):
    response = client.get(
        "/api/auth/shop/callback",
        params={"state": "forged", "code": "x"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "http://localhost:3005?shop_signin=error"


def test_the_callback_redirect_honors_web_app_url(client, monkeypatch):
    monkeypatch.setenv("WEB_APP_URL", "https://demo.example/shop")
    response = client.get(
        "/api/auth/shop/callback",
        params={"state": "forged", "code": "x"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "https://demo.example/shop?shop_signin=error"


def test_the_signin_round_trip_marks_the_session_signed_in(client, monkeypatch):
    oauth = FakeShopOAuth()
    monkeypatch.setenv("SHOPIFY_UCP_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("SHOPIFY_UCP_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(main_module.signin, "_http", httpx.AsyncClient(transport=oauth.transport()))
    monkeypatch.setattr(main_module.signin, "_endpoints", None)  # rediscover over the fake

    headers = start(client)
    started = client.get(
        "/api/auth/shop/start",
        params={"session_id": headers[SESSION_HEADER]},
        follow_redirects=False,
    )
    assert started.status_code == 302
    location = httpx.URL(started.headers["location"])
    assert location.host == "accounts.shop.app"
    state = dict(location.params)["state"]

    done = client.get(
        "/api/auth/shop/callback",
        params={"state": state, "code": "code-1"},
        follow_redirects=False,
    )
    assert done.status_code == 302
    assert done.headers["location"] == "http://localhost:3005?shop_signin=ok"
    assert client.get("/api/auth/status", headers=headers).json() == {"signed_in": True}

    replayed = client.get(
        "/api/auth/shop/callback",
        params={"state": state, "code": "code-1"},
        follow_redirects=False,
    )
    assert replayed.headers["location"].endswith("shop_signin=error")  # single-use state

    out = client.post("/api/auth/signout", headers=headers)
    assert out.json() == {"signed_in": False}
    assert client.get("/api/auth/status", headers=headers).json() == {"signed_in": False}


def test_signout_needs_a_session(client):
    assert client.post("/api/auth/signout").status_code in (400, 401, 422)


def test_the_brand_route_serves_the_mapped_payload(client, monkeypatch):
    from .test_brand import FULL_RESPONSE

    monkeypatch.setattr(
        main_module.brand_source,
        "_http",
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=FULL_RESPONSE))
        ),
    )
    monkeypatch.setattr(main_module.brand_source, "_cached", None)
    payload = client.get("/api/brand").json()
    assert payload["name"] == "Fake Store"
    assert payload["colors"] == {"background": "#112233", "foreground": "#ffffff"}
