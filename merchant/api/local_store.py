# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""A local stand-in for one store's Admin API, so the example runs with no Shopify account.

``SHOPIFY_LOCAL_STORE=1`` puts this in place of ``AdminGraphQLClient``. Nothing else changes:
the backend, the caches, the ledger, the gates, the router, and the portal are the same
modules serving a real store, and this object answers the same documents from
``queries.py`` over its own state. Mutations are *applied*, not recorded, so the whole
approval path is real end to end — stage a price move, approve it on the card, and the next
catalog read shows the new price because this store now holds it.

What it is not: Shopify. It stands in for the Admin API's object surface — products,
variants, inventory, orders, locations — and for the sales half of ShopifyQL, computed from
its own orders. It does not stand in for sessions and conversion (no fake store can know
them), for the schema, for scopes, or for anything a real store would reject. Those need a
development store; ``scripts/smoke_live.py`` is what checks them.

Two things this store can do that a freshly seeded real one cannot, because the Admin API
will not backdate an order: it has a prior period to compare against, and it has refunds, so
the period comparison and the return-spike alert both have something to work on.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from .admin_client import AdminAPIError, AdminUserError, operation_name

# Anchored at midday so every ``days_ago`` offset lands unambiguously inside its own
# calendar day, whatever time of day the process starts at.
TODAY = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)

CURRENCY = "USD"
LOCATION_ID = "gid://shopify/Location/1"
LOCATION_NAME = "Bench shop"

# How far back the invented order history reaches. Long enough for the default snapshot's
# week-over-week comparison and for the portal's 30-point sparklines.
HISTORY_DAYS = 60
# Fixed, so two runs of the same seed file tell the same story and a test can assert figures.
HISTORY_SEED = 20260828


def iso(days_ago: float) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


def gid(kind: str, number: int) -> str:
    return f"gid://shopify/{kind}/{number}"


def variant_node(
    number: int,
    *,
    title: str = "Default Title",
    price: str,
    sku: str | None = None,
    quantity: int = 0,
    cost: str | None = "10.00",
    tracked: bool = True,
    compare_at: str | None = None,
) -> dict[str, Any]:
    """One ``ProductVariant`` as the Admin API returns it, with the fields
    ``queries.py`` asks for and no others."""
    return {
        "id": gid("ProductVariant", number),
        "title": title,
        "sku": sku,
        "price": price,
        "compareAtPrice": compare_at,
        "inventoryQuantity": quantity,
        "inventoryItem": {
            "id": gid("InventoryItem", number),
            "tracked": tracked,
            "unitCost": {"amount": cost, "currencyCode": CURRENCY} if cost else None,
        },
    }


def product_node(
    number: int,
    *,
    title: str,
    handle: str,
    status: str = "ACTIVE",
    product_type: str | None = "Workshop tools",
    vendor: str | None = "ACME Supply Co.",
    description: str,
    seo_description: str | None = "A well-made thing for the workshop bench.",
    media: int = 3,
    tracks_inventory: bool = True,
    variants: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One ``Product`` node. ``totalInventory`` is derived here for the same reason Shopify
    derives it: it is the sum of the variants, and a store where the two disagree is a
    store that would teach the backend the wrong lesson."""
    rows = variants or [variant_node(number * 10, price="40.00", quantity=25)]
    return {
        "id": gid("Product", number),
        "title": title,
        "handle": handle,
        "status": status,
        "productType": product_type,
        "vendor": vendor,
        "updatedAt": iso(9),
        "description": description,
        "descriptionHtml": f"<p>{description}</p>" if description else None,
        "totalInventory": sum(row["inventoryQuantity"] for row in rows),
        "tracksInventory": tracks_inventory,
        "featuredImage": {"url": f"https://cdn.example.invalid/{handle}.jpg"},
        "seo": {"title": title, "description": seo_description},
        "media": {"nodes": [{"id": gid("MediaImage", number * 100 + i)} for i in range(media)]},
        "options": [{"name": "Size"}] if len(rows) > 1 else [{"name": "Title"}],
        "variants": {"nodes": rows},
    }


def order_node(
    number: int,
    *,
    days_ago: float,
    lines: list[tuple[dict[str, Any], int]],
    fulfillment: str = "FULFILLED",
    financial: str = "PAID",
    refunded: str | None = None,
) -> dict[str, Any]:
    """One ``Order`` node. ``lines`` pairs a product node with a quantity, and each line's
    revenue is that product's first variant price times the quantity, so the store's sales
    figures and its catalog agree with each other."""
    rows = []
    total = Decimal("0")
    for product, quantity in lines:
        variant = product["variants"]["nodes"][0]
        revenue = Decimal(variant["price"]) * quantity
        total += revenue
        rows.append(
            {
                "quantity": quantity,
                "title": product["title"],
                "variant": {"id": variant["id"]},
                "product": {"id": product["id"]},
                "discountedTotalSet": {"shopMoney": {"amount": f"{revenue:.2f}"}},
            }
        )
    return {
        "id": gid("Order", number),
        "name": f"#{1000 + number}",
        "createdAt": iso(days_ago),
        "displayFulfillmentStatus": fulfillment,
        "displayFinancialStatus": "REFUNDED" if refunded else financial,
        "currentTotalPriceSet": {"shopMoney": {"amount": f"{total:.2f}", "currencyCode": CURRENCY}},
        "lineItems": {"nodes": rows},
        "refunds": (
            [
                {
                    "id": gid("Refund", number),
                    "createdAt": iso(max(0.0, days_ago - 1)),
                    "totalRefundedSet": {"shopMoney": {"amount": refunded}},
                }
            ]
            if refunded
            else []
        ),
    }


def products_from_seed(seed: dict[str, Any]) -> list[dict[str, Any]]:
    """The seed catalog as Admin API nodes. This is the same file ``scripts/seed_store.py``
    sends to a real store, so the local store and a seeded one hold the same catalog."""
    vendor = (seed.get("store") or {}).get("vendor") or "ACME Supply Co."
    products = []
    for number, entry in enumerate(seed["products"], start=1):
        variants = [
            variant_node(
                number * 10 + index,
                title=variant.get("title") or "Default Title",
                price=str(variant["price"]),
                sku=variant.get("sku"),
                # A null quantity means the product does not track stock at all.
                quantity=int(variant.get("quantity") or 0),
                cost=None if variant.get("cost") is None else str(variant["cost"]),
                tracked=entry.get("tracks_inventory", True),
            )
            for index, variant in enumerate(entry["variants"])
        ]
        products.append(
            product_node(
                number,
                title=entry["title"],
                handle=entry["handle"],
                status=entry.get("status") or "ACTIVE",
                product_type=entry.get("product_type"),
                vendor=vendor,
                description=entry.get("description") or "",
                seo_description=entry.get("seo_description"),
                media=int(entry.get("media", 3)),
                tracks_inventory=entry.get("tracks_inventory", True),
                variants=variants,
            )
        )
    return products


def orders_from_seed(seed: dict[str, Any], products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The seed orders, then invented history behind them.

    The seed orders are the ones ``seed_store.py`` places on a real store, and they carry the
    cases the alerts are built to find: a refunded order over the return-spike rule, and an
    order left unfulfilled. Their ``days_ago`` and ``refunded`` fields are honoured here and
    ignored by ``seed_store.py``, because the Admin API stamps an order with the time it was
    created and will not create a refund for the asking.

    Behind them sits invented history: the same line patterns replayed across
    ``HISTORY_DAYS`` from a fixed seed, so the snapshot has a prior period to compare against
    and the portal's sparklines have something to draw. Every figure is invented, like every
    other figure in this repository."""
    by_handle = {product["handle"]: product for product in products}
    sellable = [
        product
        for product in products
        if product["status"] == "ACTIVE" and float(product["variants"]["nodes"][0]["price"]) > 0
    ]

    orders = []
    number = 0
    for entry in seed["orders"]:
        lines = [
            (by_handle[line["handle"]], int(line["quantity"]))
            for line in entry["lines"]
            if line["handle"] in by_handle
        ]
        if not lines:
            continue
        number += 1
        orders.append(
            order_node(
                number,
                days_ago=float(entry.get("days_ago", 1)),
                lines=lines,
                fulfillment=entry.get("fulfillment") or "FULFILLED",
                refunded=entry.get("refunded"),
            )
        )

    rng = random.Random(HISTORY_SEED)
    seed_depth = max((float(e.get("days_ago", 1)) for e in seed["orders"]), default=1.0)
    for day in range(int(seed_depth) + 1, HISTORY_DAYS):
        # A weekday shape: quieter at the weekend, so a week-over-week comparison is not
        # flat and a daily sparkline has a rhythm rather than noise.
        weekday = (TODAY - timedelta(days=day)).weekday()
        count = rng.choice((0, 1, 1, 2) if weekday >= 5 else (1, 2, 2, 3))
        for _ in range(count):
            number += 1
            picked = rng.sample(sellable, k=min(len(sellable), rng.choice((1, 1, 2))))
            orders.append(
                order_node(
                    number,
                    days_ago=day + rng.random() * 0.4,
                    lines=[(product, rng.choice((1, 1, 1, 2))) for product in picked],
                )
            )
    return orders


# One campaign, so the campaign read answers locally. Spend and revenue are deliberately
# absent rather than zero: a marketing activity does not expose them, so neither does this.
MARKETING_ACTIVITIES = (
    {
        "id": gid("MarketingActivity", 1),
        "title": "Workshop season email",
        "status": "ACTIVE",
        "createdAt": iso(20),
        "marketingChannelType": "EMAIL",
        "budget": {"total": {"amount": "800.00", "currencyCode": CURRENCY}},
    },
)


@dataclass
class LocalStore:
    """One store, held in memory, answering the Admin API documents this example sends.

    Reads are served from ``products`` and ``orders``; mutations edit them in place. State
    lives for the life of the process, so a restart is a fresh store — the same property a
    demo wants and a warning against treating this as a database."""

    products: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    shop_name: str = "ACME Supply Co."
    shop_domain: str = "acme-supply.local"
    marketing_activities: list[dict[str, Any]] = field(
        default_factory=lambda: [dict(activity) for activity in MARKETING_ACTIVITIES]
    )
    # Every mutation applied, for the smoke script and the suite to read back.
    applied: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @classmethod
    def from_seed(cls, seed_path: Path, *, shop_domain: str = "acme-supply.local") -> LocalStore:
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
        products = products_from_seed(seed)
        return cls(
            products=products,
            orders=orders_from_seed(seed, products),
            shop_name=(seed.get("store") or {}).get("name") or "ACME Supply Co.",
            shop_domain=shop_domain,
        )

    # -- The transport ---------------------------------------------------------------

    async def execute(
        self, document: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        name = operation_name(document)
        handler = getattr(self, f"_read_{name}", None)
        if handler is None:
            raise AdminAPIError(f"the local store has no answer for operation {name!r}")
        return handler(variables or {})

    async def mutate(
        self, document: str, variables: dict[str, Any], *, root: str
    ) -> dict[str, Any]:
        name = operation_name(document)
        handler = getattr(self, f"_write_{name}", None)
        if handler is None:
            # What a real store does with a document it will not accept: a user error, which
            # is the path ``staging.py``'s version fallbacks are written against.
            raise AdminUserError(name, [f"{name} is not a mutation the local store applies"])
        result = handler(variables)
        self.applied.append((name, variables))
        return result

    # -- Reads -----------------------------------------------------------------------

    def _read_ShopProfile(self, _: dict[str, Any]) -> dict[str, Any]:
        return {
            "shop": {
                "name": self.shop_name,
                "myshopifyDomain": self.shop_domain,
                "currencyCode": CURRENCY,
                "ianaTimezone": "America/Toronto",
            }
        }

    def _read_TokenScopes(self, _: dict[str, Any]) -> dict[str, Any]:
        """Every scope the example asks for, because a local store gates nothing. On a real
        store this is the read that says which fallbacks the process will be using."""
        handles = (
            "read_products",
            "write_products",
            "read_inventory",
            "write_inventory",
            "read_orders",
            "read_locations",
            "read_marketing_events",
        )
        return {
            "currentAppInstallation": {"accessScopes": [{"handle": handle} for handle in handles]}
        }

    def _read_CatalogPage(self, _: dict[str, Any]) -> dict[str, Any]:
        return {
            "products": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": self.products,
            }
        }

    def _read_ProductSearch(self, variables: dict[str, Any]) -> dict[str, Any]:
        """A stand-in for Shopify's product search: each term the query names is matched
        against title, type, and sku, which is the field set the real search covers. It is
        deliberately cruder than Shopify's — the backend's own scorer, not this, is what
        decides the order the agent sees."""
        query = (variables.get("query") or "").lower()
        terms = {part.strip("*:") for chunk in query.split(" or ") for part in chunk.split(":")[1:]}
        matched = [
            product
            for product in self.products
            if any(
                term
                and (
                    term in product["title"].lower()
                    or term in (product["productType"] or "").lower()
                    or any(term in (v["sku"] or "").lower() for v in product["variants"]["nodes"])
                )
                for term in terms
            )
        ]
        return {"products": {"nodes": matched}}

    def _read_ProductRecord(self, variables: dict[str, Any]) -> dict[str, Any]:
        wanted = variables.get("id")
        return {"product": next((p for p in self.products if p["id"] == wanted), None)}

    def _read_OrdersPage(self, _: dict[str, Any]) -> dict[str, Any]:
        return {
            "orders": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": self.orders,
            }
        }

    def _read_PrimaryLocation(self, _: dict[str, Any]) -> dict[str, Any]:
        return {
            "locations": {"nodes": [{"id": LOCATION_ID, "name": LOCATION_NAME, "isActive": True}]}
        }

    def _read_InventoryLevel(self, variables: dict[str, Any]) -> dict[str, Any]:
        """The available quantity at the location, which the restock path reads before it
        adjusts. A real store refuses an adjustment that does not say which quantity it
        believes it is changing from, so this stand-in answers the same read."""
        _, variant = self._by_inventory_item(variables["inventoryItemId"])
        return {
            "inventoryItem": {
                "inventoryLevel": {
                    "quantities": [{"name": "available", "quantity": variant["inventoryQuantity"]}]
                }
            }
        }

    def _read_MarketingActivities(self, _: dict[str, Any]) -> dict[str, Any]:
        """One activity, so the campaign read has something to return locally.

        A real store is likely to return none: marketing activities are scoped to the app
        that created them, and this example creates none. The backend treats that as an
        unmanaged campaign system rather than as zero spend, which is the distinction the
        interface cannot express — and the reason a local demo shows the read working while a
        development store shows the refusal. ``marketing_activities = []`` is how to see the
        other branch."""
        return {"marketingActivities": {"nodes": self.marketing_activities}}

    def _read_MetricsQuery(self, variables: dict[str, Any]) -> dict[str, Any]:
        """The sales half of ShopifyQL, computed from this store's own orders.

        ``FROM sessions`` comes back with parse errors, which is what a token without
        protected-customer-data access gets from a real store — and the reason the example
        reports traffic as unmeasured rather than as zero. The alternative would be to invent
        a session count, which would read as measured and be worth less than the gap."""
        query = str(variables.get("query") or "")
        if not query.startswith("FROM sales"):
            return self._parse_errors("the local store answers FROM sales only")
        start, end = _window(query)
        wanted = _columns(query)
        bucket = _bucket(query)
        if bucket is None:
            rows = [[_format(value) for value in _totals(self.orders, start, end, wanted)]]
        else:
            rows = [
                [day.isoformat(), *(_format(v) for v in _totals(self.orders, day, day, wanted))]
                for day in _buckets(start, end, bucket)
            ]
            wanted = ("day", *wanted)
        return {
            "shopifyqlQuery": {
                "parseErrors": [],
                "tableData": {
                    "columns": [
                        {"name": name, "dataType": "number", "displayName": name} for name in wanted
                    ],
                    "rows": [dict(zip(wanted, row, strict=True)) for row in rows],
                },
            }
        }

    @staticmethod
    def _parse_errors(message: str) -> dict[str, Any]:
        return {
            "shopifyqlQuery": {
                "parseErrors": [message],
                "tableData": None,
            }
        }

    # -- Writes ----------------------------------------------------------------------

    def _variant(self, variant_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        for product in self.products:
            for variant in product["variants"]["nodes"]:
                if variant["id"] == variant_id:
                    return product, variant
        raise AdminUserError("productVariantsBulkUpdate", [f"no such variant: {variant_id}"])

    def _product(self, product_id: str, operation: str) -> dict[str, Any]:
        found = next((p for p in self.products if p["id"] == product_id), None)
        if found is None:
            raise AdminUserError(operation, [f"no such product: {product_id}"])
        return found

    def _write_SetVariantPrices(self, variables: dict[str, Any]) -> dict[str, Any]:
        updated = []
        for entry in variables["variants"]:
            _, variant = self._variant(entry["id"])
            variant["price"] = f"{float(entry['price']):.2f}"
            updated.append({"id": variant["id"], "price": variant["price"]})
        return {"productVariants": updated}

    def _write_UpdateVariantDetails(self, variables: dict[str, Any]) -> dict[str, Any]:
        return self._write_SetVariantPrices(variables)

    def _write_UpdateProduct(self, variables: dict[str, Any]) -> dict[str, Any]:
        """``ProductUpdateInput``, applied. Only the fields ``staging.py`` writes are
        accepted; anything else is a user error, as it would be on a real store."""
        fields = variables["product"]
        product = self._product(fields["id"], "productUpdate")
        for key, value in fields.items():
            if key == "id":
                continue
            if key == "seo":
                product["seo"] = {**product["seo"], **value}
            elif key == "descriptionHtml":
                product["descriptionHtml"] = value
                product["description"] = _text(value)
            elif key in {"title", "status", "productType", "vendor", "handle"}:
                product[key] = value
            else:
                raise AdminUserError("productUpdate", [f"{key}: not a field this store writes"])
        product["updatedAt"] = iso(0)
        return {"product": {key: product.get(key) for key in ("id", "title", "status")}}

    def _write_AdjustInventory(self, variables: dict[str, Any]) -> dict[str, Any]:
        for change in variables["input"]["changes"]:
            product, variant = self._by_inventory_item(change["inventoryItemId"])
            if not variant["inventoryItem"]["tracked"]:
                raise AdminUserError(
                    "inventoryAdjustQuantities",
                    [f"{product['title']} does not track inventory at this location"],
                )
            # The two refusals a real store makes here, kept so the restock path is exercised
            # against the same rules locally. ``changeFromQuantity`` is typed as optional in
            # the schema and required at runtime, and a stale value means someone else moved
            # the stock between the read and the write.
            if "changeFromQuantity" not in change:
                raise AdminUserError(
                    "inventoryAdjustQuantities",
                    [
                        "InventoryChangeInput must include the following argument: "
                        "changeFromQuantity."
                    ],
                )
            if int(change["changeFromQuantity"]) != variant["inventoryQuantity"]:
                raise AdminUserError(
                    "inventoryAdjustQuantities",
                    [
                        f"{product['title']}: the available quantity is "
                        f"{variant['inventoryQuantity']}, not "
                        f"{change['changeFromQuantity']}; the stock moved since it was read"
                    ],
                )
            variant["inventoryQuantity"] = max(
                0, variant["inventoryQuantity"] + int(change["delta"])
            )
            product["totalInventory"] = sum(
                row["inventoryQuantity"] for row in product["variants"]["nodes"]
            )
        return {"inventoryAdjustmentGroup": {"createdAt": iso(0), "reason": "correction"}}

    def _by_inventory_item(self, item_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        for product in self.products:
            for variant in product["variants"]["nodes"]:
                if variant["inventoryItem"]["id"] == item_id:
                    return product, variant
        raise AdminUserError("inventoryAdjustQuantities", [f"no such inventory item: {item_id}"])


# -- The ShopifyQL subset -------------------------------------------------------------
#
# Three query shapes reach this store, all of them built by `metrics.py`: a sales row for a
# window, a sales column grouped by day or week, and a sessions total (refused above). The
# parsing is deliberately literal — it recognises what this example sends and nothing more,
# so a query the example stops sending stops being answered rather than quietly matching.

_COLUMN_SOURCES = ("total_sales", "orders", "average_order_value")


def _columns(query: str) -> tuple[str, ...]:
    shown = query.partition(" SHOW ")[2].partition(" SINCE ")[0]
    wanted = tuple(part.strip() for part in shown.split(",") if part.strip())
    return wanted or ("total_sales",)


def _window(query: str) -> tuple[Any, Any]:
    since = query.partition(" SINCE ")[2].partition(" ")[0].strip()
    until = query.partition(" UNTIL ")[2].partition(" ")[0].strip()
    start = datetime.fromisoformat(since).date()
    end = datetime.fromisoformat(until).date() if until else TODAY.date()
    return start, end


def _bucket(query: str) -> str | None:
    grouped = query.partition(" GROUP BY ")[2].strip()
    return grouped.split()[0] if grouped else None


def _buckets(start: Any, end: Any, bucket: str) -> list[Any]:
    step = timedelta(days=7 if bucket == "week" else 1)
    days, cursor = [], start
    while cursor <= end:
        days.append(cursor)
        cursor += step
    return days


def _totals(
    orders: list[dict[str, Any]], start: Any, end: Any, columns: tuple[str, ...]
) -> list[float]:
    sales, count = 0.0, 0
    for order in orders:
        placed = datetime.fromisoformat(order["createdAt"]).date()
        if start <= placed <= end:
            sales += float(order["currentTotalPriceSet"]["shopMoney"]["amount"])
            count += 1
    values = {
        "total_sales": sales,
        "orders": float(count),
        "average_order_value": sales / count if count else 0.0,
    }
    return [values.get(column, 0.0) for column in columns]


def _format(value: float) -> str:
    return f"{value:.2f}"


def _text(html: str) -> str:
    """The plain text of a description, the way a store derives it. Crude on purpose: the
    example never asks this store to render anything."""
    out, depth = [], 0
    for character in html:
        if character == "<":
            depth += 1
        elif character == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(character)
    return "".join(out).strip()
