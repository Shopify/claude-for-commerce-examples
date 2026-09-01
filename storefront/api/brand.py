# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The shop's brand settings for the web surface's theming: name, slogan, colors,
logo, and cover image, read from the shop's tokenless Storefront GraphQL API. The
web app never talks to Shopify directly — it asks the host's ``/api/brand`` route,
which asks here. Successful reads are cached in-process (brand settings change
rarely); a failed read answers with the static fallbacks and is not cached, so the
next request tries again. The caller's IP is forwarded as
``Shopify-Storefront-Buyer-IP`` per Storefront API guidance for server-side proxies.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

STOREFRONT_API_VERSION = "2026-07"
CACHE_TTL = 600.0  # seconds

BRAND_QUERY = """\
{
  shop {
    name
    brand {
      slogan
      shortDescription
      colors { primary { background foreground } }
      logo { image { url } }
      coverImage { image { url } }
    }
  }
}
"""

# A shop can publish an unusable pair (a live one answers background #ffffff AND
# foreground #ffffff, rendering chat bubbles white-on-white). Below this WCAG
# contrast ratio — 3:1, the non-text UI-component minimum — the pair is omitted from
# the payload so the web app's CSS defaults hold; deriving a readable foreground here
# would be less predictable for a demo than dropping to the defaults.
MIN_CONTRAST = 3.0
# The hero's line when the shop has no slogan (``BRAND_TAGLINE`` overrides). The
# shop's ``short_description`` stays in the payload but the hero never renders it —
# on demo shops it is often boilerplate that reads wrong as a storefront tagline.
DEFAULT_TAGLINE = "Shop with an assistant that knows the store."

_TIMEOUT = httpx.Timeout(15.0)


class BrandSource:
    """One shop's brand payload, fetched tokenless and cached for ``ttl`` seconds."""

    def __init__(
        self,
        shop_domain: str,
        http: httpx.AsyncClient | None = None,
        ttl: float = CACHE_TTL,
    ) -> None:
        self._url = f"https://{shop_domain}/api/{STOREFRONT_API_VERSION}/graphql.json"
        self._fallback_name = shop_domain
        self._http = http or httpx.AsyncClient(timeout=_TIMEOUT)
        self._ttl = ttl
        self._cached: dict[str, Any] | None = None
        self._fetched_at = 0.0

    async def brand(self, buyer_ip: str | None = None) -> dict[str, Any]:
        if self._cached is not None and time.monotonic() - self._fetched_at < self._ttl:
            return self._cached
        data = await self._fetch(buyer_ip)
        payload = self._payload(data)
        if data:  # fallbacks for a failed read are answered but never cached
            self._cached = payload
            self._fetched_at = time.monotonic()
        return payload

    async def _fetch(self, buyer_ip: str | None) -> dict[str, Any]:
        headers = {"Shopify-Storefront-Buyer-IP": buyer_ip} if buyer_ip else {}
        try:
            response = await self._http.post(
                self._url, json={"query": BRAND_QUERY}, headers=headers
            )
            response.raise_for_status()
            return response.json().get("data") or {}
        except (httpx.HTTPError, ValueError):
            return {}

    def _payload(self, data: dict[str, Any]) -> dict[str, Any]:
        shop = data.get("shop") or {}
        brand = shop.get("brand") or {}
        primary = (brand.get("colors") or {}).get("primary") or [{}]
        payload = {
            "name": shop.get("name") or self._fallback_name,
            "slogan": brand.get("slogan"),
            "tagline": brand.get("slogan") or os.environ.get("BRAND_TAGLINE") or DEFAULT_TAGLINE,
            "short_description": brand.get("shortDescription"),
            "logo_url": _image_url(brand.get("logo")),
            "cover_image_url": _image_url(brand.get("coverImage")),
        }
        background = primary[0].get("background")
        foreground = primary[0].get("foreground")
        if _usable_pair(background, foreground):
            payload["colors"] = {"background": background, "foreground": foreground}
        return payload


def _usable_pair(background: str | None, foreground: str | None) -> bool:
    """Both colors present, parseable, and at least ``MIN_CONTRAST`` apart —
    otherwise the payload omits colors and the web app's CSS defaults hold."""
    if not background or not foreground:
        return False
    first = _luminance(background)
    second = _luminance(foreground)
    if first is None or second is None:
        return False
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05) >= MIN_CONTRAST


def _luminance(color: str) -> float | None:
    """WCAG relative luminance of a ``#rgb``/``#rrggbb`` color, or None if unparseable."""
    digits = color.strip().lstrip("#")
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    if len(digits) != 6:
        return None
    try:
        channels = [int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    except ValueError:
        return None
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _image_url(node: dict[str, Any] | None) -> str | None:
    return ((node or {}).get("image") or {}).get("url")
