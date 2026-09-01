# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Catalog warm-up: browse-chrome infrastructure for the web app's grid. On a fresh
host the display cache holds nothing until a session's UCP calls fill it, so a new
``SHOP_DOMAIN`` renders an empty grid. This module fetches the shop's best-selling
products once at startup through the same tokenless Storefront GraphQL API as
``/api/brand`` (no buyer IP: startup is the host's own traffic, not a buyer's) and
maps them into the backend's display caches.

The boundary this module never crosses: it fills the backend-global display caches
only — ``backend.products``, ``backend.default_variants``, ``backend._variant_images``
— which serve the host's public ``/api/products`` reads and variant resolution. It
touches no session state: per-session provenance (``ShoppingSessionState.seen_products``
and the backend's ``_SessionState`` maps) comes only from that session's own tool
calls, so a warmed-but-unseen product still cannot enter a cart without a read first.

Any failure — GraphQL errors, a password-protected shop, a timeout — logs one line
and leaves the grid empty; the app is fully functional either way. ``CATALOG_WARMUP=0``
disables the warm-up entirely.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import httpx

from shopping_agent import Product, ProductDetails

from .brand import STOREFRONT_API_VERSION

if TYPE_CHECKING:
    from .shopify_backend import ShopifyStorefrontBackend

logger = logging.getLogger(__name__)

WARMUP_QUERY = """\
{
  products(first: 24, sortKey: BEST_SELLING) {
    nodes {
      id
      title
      handle
      description
      featuredImage { url }
      priceRange { minVariantPrice { amount currencyCode } }
      variants(first: 10) {
        nodes {
          id
          title
          price { amount currencyCode }
          selectedOptions { name value }
          availableForSale
          image { url }
        }
      }
    }
  }
}
"""

_TIMEOUT = httpx.Timeout(15.0)


def warmup_enabled() -> bool:
    return os.environ.get("CATALOG_WARMUP", "1") != "0"


async def warm_catalog(
    backend: ShopifyStorefrontBackend,
    shop_domain: str,
    http: httpx.AsyncClient | None = None,
) -> int:
    """Fill the backend's display caches from the shop's best sellers; returns how
    many products were cached. Failure-tolerant: any error logs one line and returns
    zero with the caches untouched."""
    if not warmup_enabled():
        return 0
    url = f"https://{shop_domain}/api/{STOREFRONT_API_VERSION}/graphql.json"
    client = http or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        response = await client.post(url, json={"query": WARMUP_QUERY})
        response.raise_for_status()
        body = response.json()
        records = ((body.get("data") or {}).get("products") or {}).get("nodes")
        if not records:  # errors-only body, or a password-protected shop's empty answer
            raise ValueError(str(body.get("errors") or "no products in the response"))
    except (httpx.HTTPError, ValueError) as failure:
        logger.warning("catalog warm-up skipped (%s); the grid fills from sessions", failure)
        return 0
    for record in records:
        # Display cache only — never any session's provenance or _SessionState.
        backend.warm_display_cache(_map_product(record))
    logger.info("catalog warm-up cached %d products from %s", len(records), shop_domain)
    return len(records)


def _decimal(money: dict[str, Any] | None) -> tuple[float, str]:
    """Storefront API money: decimal-string major units (``{"amount": "40.0"}``),
    unlike UCP's integer minor units."""
    money = money or {}
    return round(float(money.get("amount") or 0), 2), money.get("currencyCode") or "USD"


def _map_product(record: dict[str, Any]) -> ProductDetails:
    """The Storefront GraphQL product, in the repo's ``ProductDetails`` shape. The
    gids are identical to the UCP tools', so warmed entries and session reads land
    on the same keys."""
    title = record["title"]
    price, currency = _decimal((record.get("priceRange") or {}).get("minVariantPrice"))
    image_url = (record.get("featuredImage") or {}).get("url")
    variants = [
        _map_variant(title, image_url, variant)
        for variant in (record.get("variants") or {}).get("nodes") or []
    ]
    description = record.get("description") or None
    available = [v for v in variants if v.in_stock]
    return ProductDetails(
        product_id=record["id"],
        title=title,
        price=price,
        currency=currency,
        image_url=image_url or next((v.image_url for v in variants if v.image_url), None),
        in_stock=bool(available) if variants else True,
        short_description=description[:200] if description else None,
        long_description=description,
        variants=variants,
    )


def _map_variant(title: str, product_image: str | None, variant: dict[str, Any]) -> Product:
    price, currency = _decimal(variant.get("price"))
    options = {opt["name"]: opt["value"] for opt in variant.get("selectedOptions") or []}
    return Product(
        product_id=variant["id"],
        title=f"{title} — {variant['title']}",
        price=price,
        currency=currency,
        image_url=(variant.get("image") or {}).get("url") or product_image,
        attributes=options,
        in_stock=bool(variant.get("availableForSale")),
    )
