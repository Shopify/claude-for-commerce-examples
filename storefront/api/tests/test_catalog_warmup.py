# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The catalog warm-up: the tokenless Storefront GraphQL request it sends, the mapping
into the backend's display caches, the boundary it never crosses (display cache is not
provenance), the one-line degrade, and the ``CATALOG_WARMUP=0`` switch. All over
``MockTransport`` — never the live shop."""

import json
import logging

import httpx
import pytest

from commerce_common.skills import SkillRegistry
from storefront.api.agent_config import build_shopping_config
from storefront.api.catalog_warmup import WARMUP_QUERY, warm_catalog
from shopping_agent import Product
from shopping_agent.executor import ShoppingToolExecutor

DOMAIN = "demostore.mock.shop"
PRODUCT_ID = "gid://shopify/Product/7983592374294"
VARIANT_ID = "gid://shopify/ProductVariant/43696933273622"
SECOND_PRODUCT_ID = "gid://shopify/Product/222"

WARMUP_RESPONSE = {
    "data": {
        "products": {
            "nodes": [
                {
                    "id": PRODUCT_ID,
                    "title": "Women's T-shirt",
                    "handle": "womens-t-shirt",
                    "description": "A plain tee.",
                    "featuredImage": {"url": "https://cdn.example/tee.png"},
                    "priceRange": {"minVariantPrice": {"amount": "40.0", "currencyCode": "CAD"}},
                    "variants": {
                        "nodes": [
                            {
                                "id": VARIANT_ID,
                                "title": "Small / Green",
                                "price": {"amount": "40.0", "currencyCode": "CAD"},
                                "selectedOptions": [
                                    {"name": "Size", "value": "Small"},
                                    {"name": "Color", "value": "Green"},
                                ],
                                "availableForSale": True,
                                "image": {"url": "https://cdn.example/tee-green.png"},
                            }
                        ]
                    },
                },
                {
                    "id": SECOND_PRODUCT_ID,
                    "title": "Mug",
                    "handle": "mug",
                    "description": None,
                    "featuredImage": None,
                    "priceRange": {"minVariantPrice": {"amount": "12.5", "currencyCode": "CAD"}},
                    "variants": {
                        "nodes": [
                            {
                                "id": "gid://shopify/ProductVariant/2221",
                                "title": "Chipped",
                                "price": {"amount": "12.5", "currencyCode": "CAD"},
                                "selectedOptions": [{"name": "Finish", "value": "Chipped"}],
                                "availableForSale": False,
                                "image": None,
                            },
                            {
                                "id": "gid://shopify/ProductVariant/2222",
                                "title": "Glazed",
                                "price": {"amount": "12.5", "currencyCode": "CAD"},
                                "selectedOptions": [{"name": "Finish", "value": "Glazed"}],
                                "availableForSale": True,
                                "image": {"url": "https://cdn.example/mug.png"},
                            },
                        ]
                    },
                },
            ]
        }
    }
}


@pytest.fixture(autouse=True)
def warmup_env(monkeypatch):
    monkeypatch.delenv("CATALOG_WARMUP", raising=False)


def graphql_client(*responses: httpx.Response):
    requests: list[httpx.Request] = []
    queue = list(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return httpx.AsyncClient(transport=httpx.MockTransport(handle)), requests


async def warm(backend, *responses: httpx.Response) -> int:
    http, _ = graphql_client(*(responses or (httpx.Response(200, json=WARMUP_RESPONSE),)))
    return await warm_catalog(backend, DOMAIN, http=http)


def make_executor(backend, session, state) -> ShoppingToolExecutor:
    return ShoppingToolExecutor(
        backend=backend,
        config=build_shopping_config(DOMAIN),
        skills=SkillRegistry([]),
        session=session,
        state=state,
    )


async def test_the_warmup_request_is_a_tokenless_graphql_post_without_a_buyer_ip(backend):
    http, requests = graphql_client(httpx.Response(200, json=WARMUP_RESPONSE))
    await warm_catalog(backend, DOMAIN, http=http)
    request = requests[0]
    assert request.url == f"https://{DOMAIN}/api/2026-07/graphql.json"
    assert json.loads(request.content) == {"query": WARMUP_QUERY}
    assert "authorization" not in request.headers
    assert "x-shopify-storefront-access-token" not in request.headers
    # Startup is the host's own traffic, not a buyer's — no buyer IP to forward.
    assert "shopify-storefront-buyer-ip" not in request.headers


async def test_warmed_records_fill_the_display_caches_in_the_repo_shape(backend):
    assert await warm(backend) == 2

    tee = backend.products[PRODUCT_ID]
    assert tee.title == "Women's T-shirt"
    assert (tee.price, tee.currency) == (40.0, "CAD")  # decimal string, not minor units
    assert tee.image_url == "https://cdn.example/tee.png"
    assert tee.in_stock
    assert tee.short_description == "A plain tee."
    variant = tee.variants[0]
    assert variant.product_id == VARIANT_ID
    assert variant.title == "Women's T-shirt — Small / Green"
    assert variant.attributes == {"Size": "Small", "Color": "Green"}
    assert backend.default_variants[PRODUCT_ID] == VARIANT_ID
    assert backend._variant_images[VARIANT_ID] == "https://cdn.example/tee-green.png"

    # The mug: no featured image, so the variant's stands in; the default variant is
    # the first *available* one, not the first listed.
    mug = backend.products[SECOND_PRODUCT_ID]
    assert mug.image_url == "https://cdn.example/mug.png"
    assert mug.in_stock
    assert backend.default_variants[SECOND_PRODUCT_ID] == "gid://shopify/ProductVariant/2222"


async def test_warming_touches_no_session_state(backend):
    await warm(backend)
    assert backend._sessions == {}


async def test_a_warmed_but_unseen_product_still_needs_a_read_before_a_cart_write(
    backend, session, state
):
    """The provenance gate holds over warmed entries: the display cache is not
    provenance, so a fresh session's add is refused until its own tool call reads
    the product."""
    await warm(backend)
    executor = make_executor(backend, session, state)

    held = await executor.execute("add_to_cart", {"product_id": PRODUCT_ID, "quantity": 1})
    assert held.blocked
    assert PRODUCT_ID not in state.seen_products

    await executor.execute("get_product_details", {"product_id": PRODUCT_ID})
    assert PRODUCT_ID in state.seen_products
    added = await executor.execute("add_to_cart", {"product_id": PRODUCT_ID, "quantity": 1})
    assert not added.refused


async def test_a_cart_write_resolves_the_warmed_default_variant_without_a_live_read(
    backend, session, state, monkeypatch
):
    """Once provenance passes, variant resolution answers from the warmed display
    cache instead of a live catalog call (order-item provenance carries no backend
    session maps, so the warmed record is what makes the add work)."""
    await warm(backend)
    state.remember_products([Product(product_id=PRODUCT_ID, title="Women's T-shirt", price=40.0)])

    async def no_live_reads(*args, **kwargs):
        raise AssertionError("variant resolution should not need a live catalog call")

    monkeypatch.setattr(backend, "get_product_details", no_live_reads)
    executor = make_executor(backend, session, state)
    added = await executor.execute("add_to_cart", {"product_id": PRODUCT_ID, "quantity": 1})
    assert not added.refused
    cart = await backend.get_cart(session)
    assert [item.product_id for item in cart.items] == [VARIANT_ID]


async def test_a_failed_warmup_logs_one_line_and_leaves_the_caches_empty(backend, caplog):
    with caplog.at_level(logging.WARNING, logger="storefront.api.catalog_warmup"):
        assert await warm(backend, httpx.Response(500)) == 0
    assert backend.products == {}
    assert backend.default_variants == {}
    assert len(caplog.records) == 1

    caplog.clear()
    errors_only = httpx.Response(200, json={"errors": [{"message": "password protected"}]})
    with caplog.at_level(logging.WARNING, logger="storefront.api.catalog_warmup"):
        assert await warm(backend, errors_only) == 0
    assert backend.products == {}
    assert len(caplog.records) == 1


async def test_catalog_warmup_0_disables_the_warmup_without_a_request(backend, monkeypatch):
    monkeypatch.setenv("CATALOG_WARMUP", "0")
    http, requests = graphql_client(httpx.Response(200, json=WARMUP_RESPONSE))
    assert await warm_catalog(backend, DOMAIN, http=http) == 0
    assert requests == []
    assert backend.products == {}
