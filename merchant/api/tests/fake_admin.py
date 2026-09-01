# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""A stand-in for the Admin API, so the suite exercises the real backend without a network.

It dispatches on each document's GraphQL operation name, which is why every document in
``queries.py`` carries one: a renamed operation stops matching and the test fails loudly
rather than quietly reading an empty store.

The fixture store is ACME Supply Co. — invented here, like every other figure in this
repository. It is shaped to exercise the mappings that are easy to get wrong: a product
with a variant ladder, one that does not track inventory, a draft, one with no unit cost,
one with thin content, refunded orders, and an order old enough to breach the fulfilment
window.

Mutations are recorded rather than applied, and ``calls`` is the assertion surface: a test
about staging proves the list is empty, and a test about applying proves exactly which
mutation was sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from merchant.api.admin_client import AdminAPIError, AdminUserError, operation_name

# Anchored at midday so every ``days_ago`` offset lands unambiguously inside its own
# calendar day, whatever time of day the suite runs at.
TODAY = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)


def _iso(days_ago: float) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


def _gid(kind: str, number: int) -> str:
    return f"gid://shopify/{kind}/{number}"


def _variant(
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
    return {
        "id": _gid("ProductVariant", number),
        "title": title,
        "sku": sku,
        "price": price,
        "compareAtPrice": compare_at,
        "inventoryQuantity": quantity,
        "inventoryItem": {
            "id": _gid("InventoryItem", number),
            "tracked": tracked,
            "unitCost": {"amount": cost, "currencyCode": "USD"} if cost else None,
        },
    }


def _product(
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
    rows = variants or [_variant(number * 10, price="40.00", quantity=25)]
    return {
        "id": _gid("Product", number),
        "title": title,
        "handle": handle,
        "status": status,
        "productType": product_type,
        "vendor": vendor,
        "updatedAt": _iso(9),
        "description": description,
        "descriptionHtml": f"<p>{description}</p>" if description else None,
        "totalInventory": sum(row["inventoryQuantity"] for row in rows),
        "tracksInventory": tracks_inventory,
        "featuredImage": {"url": f"https://cdn.example.invalid/{handle}.jpg"},
        "seo": {"title": title, "description": seo_description},
        "media": {"nodes": [{"id": _gid("MediaImage", number * 100 + i)} for i in range(media)]},
        "options": [{"name": "Size"}] if len(rows) > 1 else [{"name": "Title"}],
        "variants": {"nodes": rows},
    }


# 1  the ordinary case: one variant, a unit cost, healthy stock
# 2  a variant ladder, to prove a price move scales rather than flattens it
# 3  low stock against the store default, and thin content
# 4  a draft, which the interface reads as paused
# 5  no unit cost, so margin figures must be absent rather than guessed
# 6  untracked inventory, which cannot be restocked
# 7  plenty of stock and no sales: a slow mover
PRODUCTS = [
    _product(
        1,
        title="Canvas tool apron",
        handle="canvas-tool-apron",
        description=(
            "Cotton duck canvas, triple-stitched at every stress point, with a brass buckle "
            "that will outlast the apron itself. Cut wide enough to cover a lap when seated."
        ),
    ),
    _product(
        2,
        title="Folding step stool",
        handle="folding-step-stool",
        product_type="Storage",
        description=(
            "Hard maple treads on a steel frame that folds flat against a wall. Rated to "
            "three hundred pounds and quiet enough to stand on during a phone call."
        ),
        variants=[
            _variant(20, title="Two-step", price="60.00", quantity=14, cost="24.00"),
            _variant(21, title="Three-step", price="90.00", quantity=9, cost="36.00"),
        ],
    ),
    _product(
        3,
        title="Bench dog set",
        handle="bench-dog-set",
        description="Four dogs.",
        seo_description=None,
        media=0,
        variants=[_variant(30, price="18.00", quantity=2, cost="7.50", sku="ACME-BD-4")],
    ),
    _product(
        4,
        title="Shop stool cushion",
        handle="shop-stool-cushion",
        status="DRAFT",
        description=(
            "Two inches of closed-cell foam under a wool cover, sized for a twelve-inch "
            "stool top. It stays put without a strap and wipes clean with a damp rag."
        ),
        variants=[_variant(40, price="22.00", quantity=30, cost="9.00")],
    ),
    _product(
        5,
        title="Layout square",
        handle="layout-square",
        description=(
            "Anodised aluminium, etched rather than printed, so the graduations survive a "
            "decade of pencil lines. Square to within a thousandth over eight inches."
        ),
        variants=[_variant(50, price="34.00", quantity=18, cost=None)],
    ),
    _product(
        6,
        title="Sawdust broom",
        handle="sawdust-broom",
        tracks_inventory=False,
        description=(
            "Split horsehair bristles, which move fine dust instead of pushing it into the "
            "air. The handle is ash, turned to fit a hand rather than a spec sheet."
        ),
        variants=[_variant(60, price="26.00", quantity=0, cost="11.00", tracked=False)],
    ),
    _product(
        7,
        title="Cast iron hold-down",
        handle="cast-iron-hold-down",
        description=(
            "Grey iron, machined flat where it meets the bench and left rough everywhere "
            "else. Drops into a three-quarter-inch dog hole and holds without a knob."
        ),
        variants=[_variant(70, price="48.00", quantity=64, cost="19.00")],
    ),
    # Archived: the catalog must leave it out entirely.
    _product(
        8,
        title="Discontinued mallet",
        handle="discontinued-mallet",
        status="ARCHIVED",
        description="A mallet the store no longer carries.",
        variants=[_variant(80, price="30.00", quantity=5)],
    ),
]


def _order(
    number: int,
    *,
    days_ago: float,
    total: str,
    fulfillment: str = "FULFILLED",
    financial: str = "PAID",
    lines: list[tuple[int, int, str]],
    refunded: str | None = None,
) -> dict[str, Any]:
    """``lines`` is (product number, quantity, line revenue)."""
    return {
        "id": _gid("Order", number),
        "name": f"#{1000 + number}",
        "createdAt": _iso(days_ago),
        "displayFulfillmentStatus": fulfillment,
        "displayFinancialStatus": financial,
        "currentTotalPriceSet": {"shopMoney": {"amount": total, "currencyCode": "USD"}},
        "lineItems": {
            "nodes": [
                {
                    "quantity": quantity,
                    "title": PRODUCTS[product - 1]["title"],
                    "variant": {"id": PRODUCTS[product - 1]["variants"]["nodes"][0]["id"]},
                    "product": {"id": _gid("Product", product)},
                    "discountedTotalSet": {"shopMoney": {"amount": revenue}},
                }
                for product, quantity, revenue in lines
            ]
        },
        "refunds": (
            [
                {
                    "id": _gid("Refund", number),
                    "createdAt": _iso(days_ago - 1),
                    "totalRefundedSet": {"shopMoney": {"amount": refunded}},
                }
            ]
            if refunded
            else []
        ),
    }


# No order sits exactly 7 or 14 days back: those are the period boundaries the default
# snapshot compares across, and an order on one would move between periods if the suite
# happened to cross midnight.
ORDERS = [
    _order(1, days_ago=1, total="120.00", lines=[(1, 3, "120.00")]),
    _order(2, days_ago=2, total="150.00", lines=[(2, 1, "60.00"), (1, 2, "80.00")]),
    _order(3, days_ago=3, total="90.00", lines=[(2, 1, "90.00")]),
    _order(4, days_ago=4, total="40.00", lines=[(1, 1, "40.00")]),
    _order(5, days_ago=5, total="36.00", lines=[(3, 2, "36.00")]),
    _order(6, days_ago=6, total="34.00", lines=[(5, 1, "34.00")]),
    _order(7, days_ago=8, total="80.00", lines=[(1, 2, "80.00")]),
    _order(8, days_ago=9, total="60.00", lines=[(2, 1, "60.00")]),
    _order(9, days_ago=10, total="18.00", lines=[(3, 1, "18.00")]),
    _order(10, days_ago=11, total="40.00", lines=[(1, 1, "40.00")]),
    _order(11, days_ago=12, total="26.00", lines=[(6, 1, "26.00")]),
    _order(12, days_ago=13, total="48.00", lines=[(7, 1, "48.00")]),
    # Refunded: five units of the bench dog set against the nine sold, over the 20% rule.
    _order(
        13,
        days_ago=13,
        total="45.00",
        financial="REFUNDED",
        lines=[(3, 5, "90.00")],
        refunded="90.00",
    ),
    # Unfulfilled and older than the three-day window: a delayed order.
    _order(
        14,
        days_ago=5,
        total="70.00",
        fulfillment="UNFULFILLED",
        lines=[(1, 1, "40.00"), (3, 1, "18.00")],
    ),
    # Unfulfilled but inside the window: not an issue yet.
    _order(15, days_ago=1, total="22.00", fulfillment="UNFULFILLED", lines=[(5, 1, "34.00")]),
]


@dataclass
class FakeAdmin:
    """The Admin API for one fixture store. ``calls`` records every mutation."""

    products: list[dict[str, Any]] = field(default_factory=lambda: [dict(p) for p in PRODUCTS])
    orders: list[dict[str, Any]] = field(default_factory=lambda: list(ORDERS))
    marketing_activities: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "id": _gid("MarketingActivity", 1),
                "title": "Workshop season email",
                "status": "ACTIVE",
                "createdAt": _iso(20),
                "marketingChannelType": "EMAIL",
                "budget": {"total": {"amount": "800.00", "currencyCode": "USD"}},
            }
        ]
    )
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    shopifyql_queries: list[str] = field(default_factory=list)
    # Operation names to refuse, and how. ShopifyQL and marketing activities are the two
    # the example is built to survive losing.
    refuse: set[str] = field(default_factory=set)
    user_errors: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # ShopifyQL answers, keyed by a substring of the query they answer ("FROM sales",
    # "GROUP BY week"). The first key the query contains wins; a query no key matches comes
    # back with parse errors, which is what a store without the reports scope returns. An
    # empty map therefore puts every metrics read on the orders-derived path.
    shopifyql: dict[str, tuple[tuple[str, ...], list[list[str]]]] = field(default_factory=dict)

    async def execute(
        self, document: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        name = operation_name(document)
        self.reads.append(name)
        if name == "MetricsQuery":
            # Recorded before the refusal check, so a test can count attempts and not only
            # the queries this store was willing to answer.
            self.shopifyql_queries.append(str((variables or {}).get("query") or ""))
        if name in self.refuse:
            raise AdminAPIError(f"{name}: refused by the fixture", codes=("ACCESS_DENIED",))
        handler = getattr(self, f"_{name}", None)
        if handler is None:
            raise AssertionError(f"the fixture has no answer for operation {name!r}")
        return handler(variables or {})

    async def mutate(
        self, document: str, variables: dict[str, Any], *, root: str
    ) -> dict[str, Any]:
        name = operation_name(document)
        if name in self.refuse:
            raise AdminAPIError(f"{name}: refused by the fixture")
        if errors := self.user_errors.get(name):
            raise AdminUserError(
                name,
                [f"{'.'.join(e.get('field') or []) or 'request'}: {e['message']}" for e in errors],
            )
        self.calls.append((name, variables))
        return {}

    # -- Reads ---------------------------------------------------------------------

    def _ShopProfile(self, _: dict[str, Any]) -> dict[str, Any]:
        return {
            "shop": {
                "name": "ACME Supply Co.",
                "myshopifyDomain": "acme-supply.myshopify.com",
                "currencyCode": "USD",
                "ianaTimezone": "America/Toronto",
            }
        }

    def _CatalogPage(self, _: dict[str, Any]) -> dict[str, Any]:
        return {
            "products": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": self.products,
            }
        }

    def _ProductSearch(self, variables: dict[str, Any]) -> dict[str, Any]:
        """A crude stand-in for Shopify's search: every term the query string names is
        matched against the title, type, and sku, which is what the real search covers."""
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

    def _ProductRecord(self, variables: dict[str, Any]) -> dict[str, Any]:
        wanted = variables.get("id")
        found = next((p for p in self.products if p["id"] == wanted), None)
        return {"product": found}

    def _OrdersPage(self, _: dict[str, Any]) -> dict[str, Any]:
        return {
            "orders": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": self.orders,
            }
        }

    def _InventoryLevel(self, variables: dict[str, Any]) -> dict[str, Any]:
        """The available quantity the restock path reads before it adjusts, taken from the
        same variant rows the catalog reads, so a test that stocks a variant sees that
        number here too."""
        wanted = variables.get("inventoryItemId")
        for product in self.products:
            for variant in product["variants"]["nodes"]:
                if variant["inventoryItem"]["id"] == wanted:
                    return {
                        "inventoryItem": {
                            "inventoryLevel": {
                                "quantities": [
                                    {
                                        "name": "available",
                                        "quantity": variant["inventoryQuantity"],
                                    }
                                ]
                            }
                        }
                    }
        return {"inventoryItem": None}

    def _PrimaryLocation(self, _: dict[str, Any]) -> dict[str, Any]:
        return {
            "locations": {
                "nodes": [{"id": _gid("Location", 1), "name": "Bench shop", "isActive": True}]
            }
        }

    def _MarketingActivities(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"marketingActivities": {"nodes": self.marketing_activities}}

    def _MetricsQuery(self, variables: dict[str, Any]) -> dict[str, Any]:
        """ShopifyQL. The ``shopifyql`` map decides which queries this store can answer, so
        a test can grant sales but withhold sessions — the shape a token without
        protected-customer-data access actually has."""
        query = variables.get("query") or ""
        match = next(
            (table for key, table in self.shopifyql.items() if key in query),
            None,
        )
        if match is None:
            return {
                "shopifyqlQuery": {
                    "parseErrors": ["reports not enabled"],
                    "tableData": None,
                }
            }
        columns, rows = match
        return {
            "shopifyqlQuery": {
                "parseErrors": [],
                "tableData": {
                    "columns": [
                        {"name": name, "dataType": "number", "displayName": name}
                        for name in columns
                    ],
                    "rows": [dict(zip(columns, row, strict=True)) for row in rows],
                },
            }
        }

    # -- Helpers for the tests -----------------------------------------------------

    def mutation_names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def only_call(self) -> tuple[str, dict[str, Any]]:
        assert len(self.calls) == 1, f"expected exactly one mutation, got {self.mutation_names()}"
        return self.calls[0]
