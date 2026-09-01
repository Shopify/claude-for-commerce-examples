# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Manual live check of the whole backend path against the real shop (SHOP_DOMAIN,
default demostore.mock.shop): discovery, a search, a product's details, a cart's
create/read/update/remove, and a policies search. Prints one line per step; exits
non-zero on the first failure. No Anthropic key needed — this exercises the UCP
surface, not the model.

    python storefront/scripts/smoke.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXAMPLES_DIR))

from storefront.api.shopify_backend import ShopifyStorefrontBackend  # noqa: E402
from storefront.api.ucp_client import UcpClient, shop_domain_from_env  # noqa: E402
from shopping_agent import ShoppingSessionContext  # noqa: E402


async def main() -> None:
    shop = shop_domain_from_env()
    backend = ShopifyStorefrontBackend(UcpClient(shop), store_name=shop)
    session = ShoppingSessionContext(session_id="smoke", user_id="guest")

    discovery = await backend.client.discover()
    print(f"discovery: ucp version {discovery.get('ucp', {}).get('version', '?')}")

    products = await backend.search_products(session, "shirt", limit=3)
    assert products, "search returned nothing"
    first = products[0]
    print(f"search: {len(products)} products, first {first.title!r} {first.price} {first.currency}")

    details = await backend.get_product_details(session, first.product_id)
    assert details and details.variants, "details missing variants"
    print(f"details: {len(details.variants)} variants, default {details.variants[0].title!r}")

    cart = await backend.add_to_cart(session, first.product_id, 1)
    assert cart.item_count == 1, f"expected 1 item, got {cart.item_count}"
    checkout_url = await backend.checkout_url_for(session.session_id)
    assert checkout_url, "cart carried no checkout_url"
    print(f"cart: added {cart.items[0].title!r}, checkout_url present")

    cart = await backend.update_cart_item(session, cart.items[0].product_id, 2)
    assert cart.item_count == 2, f"expected quantity 2, got {cart.item_count}"
    cart = await backend.remove_from_cart(session, cart.items[0].product_id)
    assert cart.items == [], "cart not empty after remove"
    print("cart: update and remove ok")

    policies = await backend.search_policies(session, "return policy")
    print(f"policies: {len(policies)} matches")

    await backend.client.aclose()
    print(f"smoke ok against {shop}")


if __name__ == "__main__":
    asyncio.run(main())
