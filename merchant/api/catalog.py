# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Products as this example holds them: the records the Admin API returns, the cache the
portal reads and the alerts are computed from, and the mapping onto ``Listing`` and
``ListingDetails``.

Product GIDs are the listing ids the agent sees and the only ids writes accept later, so
they travel through this module verbatim. One Shopify state carries two of the interface's:
an unpublished product reports as ``paused``, because that is what the portal's pause
action produces; archived products are left out of the catalog entirely.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from merchant_agent import Listing, ListingDetails

from .admin_client import AdminAPIError, AdminExecutor
from .queries import CATALOG_PAGE, PRODUCT_RECORD, PRODUCT_SEARCH

logger = logging.getLogger(__name__)

PRODUCT_GID_PREFIX = "gid://shopify/Product/"

_SHORT_DESCRIPTION_CHARS = 160
_THIN_DESCRIPTION_CHARS = 80
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "any",
        "are",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "show",
        "that",
        "the",
        "this",
        "to",
        "with",
        "what",
        "listing",
        "listings",
        "product",
        "products",
        "item",
        "items",
    }
)
_BROWSE_TERMS = frozenset({"all", "everything", "catalog", "catalogue", "store", "inventory"})
_WORD = re.compile(r"[A-Za-z0-9']+")


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class VariantRecord:
    variant_id: str
    title: str
    sku: str | None
    price: float
    compare_at_price: float | None
    inventory_quantity: int
    inventory_item_id: str | None
    tracked: bool
    unit_cost: float | None

    @classmethod
    def from_node(cls, node: dict[str, Any]) -> VariantRecord:
        item = node.get("inventoryItem") or {}
        cost = (item.get("unitCost") or {}).get("amount")
        return cls(
            variant_id=node["id"],
            title=node.get("title") or "",
            sku=node.get("sku") or None,
            price=_as_float(node.get("price")) or 0.0,
            compare_at_price=_as_float(node.get("compareAtPrice")),
            inventory_quantity=int(node.get("inventoryQuantity") or 0),
            inventory_item_id=item.get("id"),
            tracked=bool(item.get("tracked", True)),
            unit_cost=_as_float(cost),
        )


@dataclass(frozen=True)
class ProductRecord:
    """One product as both the portal and the agent see it. ``product_id`` is the GID."""

    product_id: str
    title: str
    handle: str
    shopify_status: str
    product_type: str | None
    vendor: str | None
    description: str | None
    description_html: str | None
    seo_title: str | None
    seo_description: str | None
    image_url: str | None
    media_count: int
    option_names: tuple[str, ...]
    total_inventory: int
    tracks_inventory: bool
    updated_at: str | None
    variants: tuple[VariantRecord, ...] = field(default=())

    @classmethod
    def from_node(cls, node: dict[str, Any]) -> ProductRecord:
        seo = node.get("seo") or {}
        variants = tuple(
            VariantRecord.from_node(entry)
            for entry in ((node.get("variants") or {}).get("nodes") or [])
        )
        return cls(
            product_id=node["id"],
            title=node.get("title") or "",
            handle=node.get("handle") or "",
            shopify_status=(node.get("status") or "ACTIVE").upper(),
            product_type=node.get("productType") or None,
            vendor=node.get("vendor") or None,
            description=node.get("description") or None,
            description_html=node.get("descriptionHtml") or None,
            seo_title=seo.get("title") or None,
            seo_description=seo.get("description") or None,
            image_url=(node.get("featuredImage") or {}).get("url"),
            media_count=len((node.get("media") or {}).get("nodes") or []),
            option_names=tuple(
                option.get("name") for option in (node.get("options") or []) if option.get("name")
            ),
            total_inventory=int(node.get("totalInventory") or 0),
            tracks_inventory=bool(node.get("tracksInventory", True)),
            updated_at=node.get("updatedAt"),
            variants=variants,
        )

    @property
    def price(self) -> float:
        return self.variants[0].price if self.variants else 0.0

    @property
    def currency_hint(self) -> str | None:
        return None

    @property
    def stock(self) -> int:
        if any(variant.tracked for variant in self.variants):
            return sum(variant.inventory_quantity for variant in self.variants)
        return self.total_inventory

    @property
    def unit_cost(self) -> float | None:
        costs = [v.unit_cost for v in self.variants if v.unit_cost]
        return costs[0] if costs else None

    @property
    def price_range(self) -> tuple[float, float]:
        prices = [variant.price for variant in self.variants] or [0.0]
        return min(prices), max(prices)

    @property
    def status(self) -> str:
        """The interface's status. ``ARCHIVED`` never reaches here: the cache drops it."""
        if self.shopify_status == "DRAFT":
            return "paused"
        if self.tracks_inventory and self.stock <= 0:
            return "out_of_stock"
        return "active"

    @property
    def missing_content(self) -> tuple[str, ...]:
        gaps: list[str] = []
        if not self.description or len(self.description) < _THIN_DESCRIPTION_CHARS:
            gaps.append("long_description")
        if self.media_count == 0:
            gaps.append("images")
        if not self.seo_description:
            gaps.append("seo_description")
        if not self.product_type:
            gaps.append("product_type")
        return tuple(gaps)

    @property
    def content_quality(self) -> str:
        gaps = len(self.missing_content)
        return "good" if gaps == 0 else "needs_work" if gaps == 1 else "poor"

    @property
    def short_description(self) -> str | None:
        if not self.description:
            return None
        text = " ".join(self.description.split())
        if len(text) <= _SHORT_DESCRIPTION_CHARS:
            return text
        return text[: _SHORT_DESCRIPTION_CHARS - 1].rstrip() + "…"

    def attributes(self) -> dict[str, str]:
        """Catalog facts worth carrying into the model's fenced view; the variant count is
        here because the interface has no variant dimension and a price move fans out."""
        values: dict[str, str] = {"handle": self.handle}
        if self.vendor:
            values["vendor"] = self.vendor
        if self.product_type:
            values["product_type"] = self.product_type
        if len(self.variants) > 1:
            low, high = self.price_range
            values["variants"] = str(len(self.variants))
            if low != high:
                values["price_range"] = f"{low:g}–{high:g}"
        elif self.variants and self.variants[0].sku:
            values["sku"] = self.variants[0].sku or ""
        return values

    def to_listing(self, currency: str) -> Listing:
        return Listing(
            listing_id=self.product_id,
            title=self.title,
            status=self.status,
            price=self.price,
            currency=currency,
            stock=self.stock,
            category=self.product_type,
            content_quality=self.content_quality,
            attributes=self.attributes(),
            image_url=self.image_url,
            short_description=self.short_description,
        )

    def to_details(
        self, currency: str, *, sales_last_30d: int | None, return_rate_pct: float | None
    ) -> ListingDetails:
        return ListingDetails(
            **self.to_listing(currency).model_dump(),
            long_description=self.description,
            review_snippets=[],
            sales_last_30d=sales_last_30d,
            return_rate_pct=return_rate_pct,
            missing_attributes=list(self.missing_content),
        )


def normalize_product_id(raw: str) -> str:
    """A Product GID for ``raw``, which is already one or is its numeric suffix. Anything
    else comes back unchanged, for the caller's own lookup to resolve or refuse."""
    text = raw.strip()
    if text.startswith(PRODUCT_GID_PREFIX):
        return text
    if text.isdigit():
        return f"{PRODUCT_GID_PREFIX}{text}"
    return text


def significant_terms(query: str) -> list[str]:
    words = [word.lower() for word in _WORD.findall(query)]
    return [word for word in words if word not in _STOP_WORDS and len(word) > 1]


def is_browse(query: str) -> bool:
    """True when the query asks for the catalog rather than for a match in it."""
    terms = significant_terms(query)
    return not terms or all(term in _BROWSE_TERMS for term in terms)


def score(record: ProductRecord, terms: list[str]) -> int:
    """Term overlap across the fields an operator would search by. Used to rank the Admin
    API's matches and, when that returns nothing, to scan the cache the same way."""
    haystacks = (
        (record.title.lower(), 4),
        ((record.product_type or "").lower(), 2),
        ((record.vendor or "").lower(), 2),
        (record.handle.lower(), 1),
        (" ".join((v.sku or "").lower() for v in record.variants), 3),
        ((record.description or "").lower(), 1),
    )
    total = 0
    for term in terms:
        for text, weight in haystacks:
            if term in text:
                total += weight
                break
    return total


def admin_search_query(query: str) -> str:
    """Shopify's product search string for ``query``. Terms are OR-ed so a descriptive
    phrase still matches; the caller re-ranks what comes back."""
    terms = significant_terms(query)[:6]
    if not terms:
        return ""
    return " OR ".join(f"title:*{term}* OR product_type:*{term}* OR sku:*{term}*" for term in terms)


class CatalogCache:
    """The store's products, read in pages and held for ``ttl_s``. The portal's listing
    views, the inventory alerts, and the search fallback all read from one copy; applying
    a change invalidates it."""

    def __init__(
        self,
        executor: AdminExecutor,
        *,
        page_size: int = 100,
        max_products: int = 250,
        ttl_s: float = 45.0,
    ) -> None:
        self._executor = executor
        self._page_size = page_size
        self._max_products = max_products
        self._ttl_s = ttl_s
        self._records: dict[str, ProductRecord] = {}
        self._loaded_at: float | None = None

    def invalidate(self) -> None:
        self._loaded_at = None

    async def refresh(self) -> list[ProductRecord]:
        """Re-read now, rather than marking the copy stale and waiting for someone to await
        it. ``cached()`` is what the portal's synchronous reads serve, and it cannot trigger
        a fetch of its own — so after a write, a cache that was only invalidated would keep
        serving the old price until some unrelated awaited read happened to reload it."""
        self.invalidate()
        return await self.all()

    def cached(self) -> list[ProductRecord]:
        """What is already loaded, without a fetch; the portal's synchronous reads use it."""
        return sorted(self._records.values(), key=lambda record: record.title.lower())

    async def all(self) -> list[ProductRecord]:
        if self._loaded_at is None or time.monotonic() - self._loaded_at > self._ttl_s:
            await self._reload()
        return self.cached()

    async def _reload(self) -> None:
        records: dict[str, ProductRecord] = {}
        cursor: str | None = None
        while len(records) < self._max_products:
            data = await self._executor.execute(
                CATALOG_PAGE, {"first": self._page_size, "after": cursor}
            )
            page = data.get("products") or {}
            for node in page.get("nodes") or []:
                record = ProductRecord.from_node(node)
                if record.shopify_status != "ARCHIVED":
                    records[record.product_id] = record
            info = page.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            cursor = info.get("endCursor")
            if not cursor:
                break
        self._records = records
        self._loaded_at = time.monotonic()

    async def get(self, listing_id: str) -> ProductRecord | None:
        """One product, freshly read so a staged price is judged against the live one. A
        title or handle is resolved against the cache first."""
        product_id = normalize_product_id(listing_id)
        if not product_id.startswith(PRODUCT_GID_PREFIX):
            resolved = await self._resolve_by_name(product_id)
            if resolved is None:
                return None
            product_id = resolved
        data = await self._executor.execute(PRODUCT_RECORD, {"id": product_id})
        node = data.get("product")
        if not node:
            return None
        record = ProductRecord.from_node(node)
        if record.shopify_status == "ARCHIVED":
            return None
        self._records[record.product_id] = record
        return record

    async def _resolve_by_name(self, text: str) -> str | None:
        lowered = text.casefold()
        for record in await self.all():
            if lowered in (record.handle.casefold(), record.title.casefold()):
                return record.product_id
        return None

    async def search(self, query: str, limit: int) -> list[ProductRecord]:
        """The Admin API's matches, re-ranked locally. A query the Admin search syntax
        cannot express comes back empty — or is rejected outright — so the cache is scanned
        with the same scorer rather than reporting an empty catalog. A refusal that is
        really a broken token still surfaces, from the catalog read behind the fallback."""
        if is_browse(query):
            return (await self.all())[:limit]
        terms = significant_terms(query)
        search_string = admin_search_query(query)
        matches: list[ProductRecord] = []
        if search_string:
            try:
                data = await self._executor.execute(
                    PRODUCT_SEARCH, {"first": max(limit * 3, 20), "query": search_string}
                )
            except AdminAPIError as error:
                logger.info("the Admin product search was refused, scanning the cache: %s", error)
                data = {}
            matches = [
                record
                for node in ((data.get("products") or {}).get("nodes") or [])
                if (record := ProductRecord.from_node(node)).shopify_status != "ARCHIVED"
            ]
            for record in matches:
                self._records[record.product_id] = record
        if not matches:
            matches = await self.all()
        ranked = sorted(
            ((score(record, terms), record) for record in matches),
            key=lambda pair: (-pair[0], pair[1].title.lower()),
        )
        return [record for points, record in ranked if points > 0][:limit]
