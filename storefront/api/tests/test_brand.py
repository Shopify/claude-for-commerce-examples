# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The brand source: what the Storefront API request carries, how the response maps,
the static fallbacks for a shop without brand settings or an unreachable shop, the
contrast guard on the color pair, and the in-process cache."""

import json

import httpx

from storefront.api.brand import BRAND_QUERY, DEFAULT_TAGLINE, BrandSource

DOMAIN = "demostore.mock.shop"

FULL_RESPONSE = {
    "data": {
        "shop": {
            "name": "Fake Store",
            "brand": {
                "slogan": "Everything here is fake",
                "shortDescription": "A demo shop.",
                "colors": {"primary": [{"background": "#112233", "foreground": "#ffffff"}]},
                "logo": {"image": {"url": "https://cdn.example/logo.png"}},
                "coverImage": {"image": {"url": "https://cdn.example/cover.png"}},
            },
        }
    }
}


def make_source(*responses: httpx.Response, ttl: float = 600.0):
    requests: list[httpx.Request] = []
    queue = list(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    transport = httpx.MockTransport(handle)
    return BrandSource(DOMAIN, http=httpx.AsyncClient(transport=transport), ttl=ttl), requests


async def test_the_request_is_a_tokenless_graphql_post_with_the_buyer_ip_forwarded():
    source, requests = make_source(httpx.Response(200, json=FULL_RESPONSE))
    await source.brand("203.0.113.7")
    request = requests[0]
    assert request.url == f"https://{DOMAIN}/api/2026-07/graphql.json"
    assert json.loads(request.content) == {"query": BRAND_QUERY}
    assert request.headers["shopify-storefront-buyer-ip"] == "203.0.113.7"
    assert "authorization" not in request.headers
    assert "x-shopify-storefront-access-token" not in request.headers


async def test_a_full_brand_maps_every_field():
    source, _ = make_source(httpx.Response(200, json=FULL_RESPONSE))
    assert await source.brand() == {
        "name": "Fake Store",
        "slogan": "Everything here is fake",
        "tagline": "Everything here is fake",  # the slogan, when the shop has one
        "short_description": "A demo shop.",
        "colors": {"background": "#112233", "foreground": "#ffffff"},
        "logo_url": "https://cdn.example/logo.png",
        "cover_image_url": "https://cdn.example/cover.png",
    }


async def test_a_shop_without_brand_settings_gets_the_static_fallbacks(monkeypatch):
    monkeypatch.delenv("BRAND_TAGLINE", raising=False)
    source, _ = make_source(
        httpx.Response(200, json={"data": {"shop": {"name": "Fake Store", "brand": None}}})
    )
    payload = await source.brand()
    assert payload["name"] == "Fake Store"
    assert "colors" not in payload  # the web app's CSS defaults hold
    assert payload["slogan"] is None
    assert payload["tagline"] == DEFAULT_TAGLINE
    assert payload["logo_url"] is None


async def test_brand_tagline_env_overrides_the_default_but_never_the_slogan(monkeypatch):
    monkeypatch.setenv("BRAND_TAGLINE", "Set by the host")
    source, _ = make_source(
        httpx.Response(200, json={"data": {"shop": {"name": "Fake Store", "brand": None}}})
    )
    assert (await source.brand())["tagline"] == "Set by the host"

    with_slogan, _ = make_source(httpx.Response(200, json=FULL_RESPONSE))
    assert (await with_slogan.brand())["tagline"] == "Everything here is fake"


def colors_response(background: str | None, foreground: str | None) -> httpx.Response:
    primary: dict[str, str] = {}
    if background is not None:
        primary["background"] = background
    if foreground is not None:
        primary["foreground"] = foreground
    brand = {"colors": {"primary": [primary]}}
    return httpx.Response(200, json={"data": {"shop": {"name": "Fake Store", "brand": brand}}})


async def test_the_contrast_guard_drops_unreadable_color_pairs():
    """A live shop can publish an unusable pair (white on white renders the chat
    bubbles invisible); anything under the 3:1 UI minimum is omitted so the web
    app's CSS defaults hold."""
    healthy, _ = make_source(colors_response("#112233", "#ffffff"))
    assert (await healthy.brand())["colors"] == {
        "background": "#112233",
        "foreground": "#ffffff",
    }

    for background, foreground in [
        ("#ffffff", "#ffffff"),  # the live white-on-white case
        ("#3355ff", "#3355ff"),  # any equal pair
        ("#112233", None),  # missing foreground
        (None, "#ffffff"),  # missing background
        ("#888888", "#999999"),  # present but far below 3:1
        ("not-a-color", "#ffffff"),  # unparseable
    ]:
        source, _ = make_source(colors_response(background, foreground))
        payload = await source.brand()
        assert "colors" not in payload, (background, foreground)


async def test_an_unreachable_shop_answers_fallbacks_and_is_not_cached():
    source, requests = make_source(httpx.Response(500), httpx.Response(200, json=FULL_RESPONSE))
    first = await source.brand()
    assert first["name"] == DOMAIN
    assert "colors" not in first
    second = await source.brand()  # the failure was not cached, so this refetches
    assert second["name"] == "Fake Store"
    assert len(requests) == 2


async def test_a_successful_read_is_cached_until_the_ttl_lapses():
    source, requests = make_source(httpx.Response(200, json=FULL_RESPONSE))
    await source.brand()
    await source.brand()
    assert len(requests) == 1
    source._fetched_at -= 601.0  # age the cache past the TTL
    await source.brand()
    assert len(requests) == 2
