# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Manual live check of Sign in with Shop's token plumbing, gated on SHOP_ACCESS_TOKEN
(the headless shortcut — the browser leg needs the running host instead): endpoint
discovery, the two-step mint, and a personalized catalog search through the backend.
Reads SHOPIFY_UCP_CLIENT_ID / SHOPIFY_UCP_CLIENT_SECRET from the environment or the
repo-root .env. Prints one line per step and never a token value.

    SHOP_ACCESS_TOKEN=... python storefront/scripts/smoke_signin.py

The buyer IP sent with the bearer defaults to a documentation address; set
SHOP_BUYER_IP to your public IP if the catalog rejects it.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXAMPLES_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(EXAMPLES_DIR.parent / ".env", override=False)

from storefront.api.identity import ShopSignIn  # noqa: E402
from storefront.api.shopify_backend import ShopifyStorefrontBackend  # noqa: E402
from storefront.api.ucp_client import UcpClient, shop_domain_from_env  # noqa: E402
from shopping_agent import ShoppingSessionContext  # noqa: E402


async def main() -> None:
    if not os.environ.get("SHOP_ACCESS_TOKEN"):
        print("SHOP_ACCESS_TOKEN not set; nothing to smoke.")
        return

    signin = ShopSignIn()
    endpoints = await signin.endpoints()
    print(f"discovery: authorize {endpoints.authorize_url}, redeem {endpoints.redeem_url}")

    session_id = "smoke-signin"
    signin.note_buyer_ip(session_id, os.environ.get("SHOP_BUYER_IP", "203.0.113.7"))
    auth = await signin.credentials_for(session_id)
    assert auth is not None, "buyer-linked token mint failed"
    print("mint: buyer-linked token issued")

    shop = shop_domain_from_env()
    backend = ShopifyStorefrontBackend(UcpClient(shop), store_name=shop, identity=signin)
    session = ShoppingSessionContext(session_id=session_id, user_id="guest")
    products = await backend.search_products(session, "shirt", limit=3)
    assert products, "personalized search returned nothing"
    print(f"personalized search: {len(products)} products, first {products[0].title!r}")

    await backend.client.aclose()
    await signin.aclose()
    print(f"signin smoke ok against {shop}")


if __name__ == "__main__":
    asyncio.run(main())
