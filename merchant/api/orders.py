# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""One scan of the store's recent orders, cached and reused. The daily series behind the
metrics fallback, the per-product sales that drive demand signals and slow-mover alerts,
the open order exceptions, and the portal's order feed all come from this single read
rather than a query each.

Without the ``read_all_orders`` scope the Admin API returns only the trailing 60 days, so
the window here is the ceiling on every period this example can report.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .admin_client import AdminExecutor
from .queries import ORDERS_PAGE

UNFULFILLED = {"UNFULFILLED", "PARTIALLY_FULFILLED", "IN_PROGRESS", "SCHEDULED", "ON_HOLD"}


def _parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class OrderLine:
    product_id: str | None
    variant_id: str | None
    title: str
    quantity: int
    revenue: float


@dataclass(frozen=True)
class OrderRecord:
    order_id: str
    name: str
    created_at: datetime
    fulfillment_status: str
    financial_status: str
    total: float
    currency: str
    lines: tuple[OrderLine, ...]
    refund_times: tuple[datetime, ...]
    refunded_amount: float

    @classmethod
    def from_node(cls, node: dict[str, Any]) -> OrderRecord | None:
        created = _parse_time(node.get("createdAt"))
        if created is None:
            return None
        money = (node.get("currentTotalPriceSet") or {}).get("shopMoney") or {}
        refunds = node.get("refunds") or []
        return cls(
            order_id=node["id"],
            name=node.get("name") or node["id"],
            created_at=created,
            fulfillment_status=(node.get("displayFulfillmentStatus") or "").upper(),
            financial_status=(node.get("displayFinancialStatus") or "").upper(),
            total=_as_float(money.get("amount")),
            currency=money.get("currencyCode") or "USD",
            lines=tuple(
                OrderLine(
                    product_id=(entry.get("product") or {}).get("id"),
                    variant_id=(entry.get("variant") or {}).get("id"),
                    title=entry.get("title") or "",
                    quantity=int(entry.get("quantity") or 0),
                    revenue=_as_float(
                        ((entry.get("discountedTotalSet") or {}).get("shopMoney") or {}).get(
                            "amount"
                        )
                    ),
                )
                for entry in ((node.get("lineItems") or {}).get("nodes") or [])
            ),
            refund_times=tuple(
                stamp
                for refund in refunds
                if (stamp := _parse_time(refund.get("createdAt"))) is not None
            ),
            refunded_amount=sum(
                _as_float(
                    ((refund.get("totalRefundedSet") or {}).get("shopMoney") or {}).get("amount")
                )
                for refund in refunds
            ),
        )

    @property
    def is_open(self) -> bool:
        return self.fulfillment_status in UNFULFILLED and self.financial_status not in {
            "REFUNDED",
            "VOIDED",
        }

    @property
    def units(self) -> int:
        return sum(line.quantity for line in self.lines)


@dataclass(frozen=True)
class DayRow:
    day: date
    sales: float
    orders: int


# The aggregations are module functions over a tuple of orders rather than methods, because
# each one is needed twice: once by the agent's reads, which fetch first, and once by the
# portal's synchronous reads, which serve the cache the route has already filled. One
# definition each keeps the two from drifting.


def daily_rows(
    orders: tuple[OrderRecord, ...],
    start: date,
    end: date,
    product_ids: frozenset[str] | None = None,
) -> list[DayRow]:
    """One row per day from ``start`` to ``end`` inclusive, zero-filled. Narrowed to
    ``product_ids``, sales come from the matching line items rather than the order total
    and an order counts once however many of its lines match."""
    sales: dict[date, float] = {}
    counts: dict[date, int] = {}
    for order in orders:
        day = order.created_at.date()
        if not (start <= day <= end):
            continue
        if product_ids is None:
            sales[day] = sales.get(day, 0.0) + order.total
            counts[day] = counts.get(day, 0) + 1
            continue
        matched = [line for line in order.lines if line.product_id in product_ids]
        if not matched:
            continue
        sales[day] = sales.get(day, 0.0) + sum(line.revenue for line in matched)
        counts[day] = counts.get(day, 0) + 1
    return [
        DayRow(
            day=(current := start + timedelta(days=offset)),
            sales=round(sales.get(current, 0.0), 2),
            orders=counts.get(current, 0),
        )
        for offset in range((end - start).days + 1)
    ]


def units_by_product(orders: tuple[OrderRecord, ...], days: int = 30) -> dict[str, int]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    units: dict[str, int] = {}
    for order in orders:
        if order.created_at < cutoff:
            continue
        for line in order.lines:
            if line.product_id:
                units[line.product_id] = units.get(line.product_id, 0) + line.quantity
    return units


def refund_units_by_product(orders: tuple[OrderRecord, ...], days: int = 30) -> dict[str, int]:
    """Units on orders that carry a refund, the closest stand-in for a return rate the
    order records support."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    units: dict[str, int] = {}
    for order in orders:
        if order.created_at < cutoff or not order.refund_times:
            continue
        for line in order.lines:
            if line.product_id:
                units[line.product_id] = units.get(line.product_id, 0) + line.quantity
    return units


class OrderScan:
    """The trailing ``window_days`` of orders, newest first, held for ``ttl_s``."""

    def __init__(
        self,
        executor: AdminExecutor,
        *,
        window_days: int = 60,
        page_size: int = 100,
        max_orders: int = 400,
        ttl_s: float = 60.0,
    ) -> None:
        self._executor = executor
        self._window_days = window_days
        self._page_size = page_size
        self._max_orders = max_orders
        self._ttl_s = ttl_s
        self._orders: tuple[OrderRecord, ...] = ()
        self._loaded_at: float | None = None
        self._currency: str | None = None

    def invalidate(self) -> None:
        self._loaded_at = None

    @property
    def window_days(self) -> int:
        """How far back this scan reaches; the ceiling on every period reported from it."""
        return self._window_days

    @property
    def currency(self) -> str | None:
        return self._currency

    def cached(self) -> tuple[OrderRecord, ...]:
        return self._orders

    async def orders(self) -> tuple[OrderRecord, ...]:
        if self._loaded_at is None or time.monotonic() - self._loaded_at > self._ttl_s:
            await self._reload()
        return self._orders

    async def _reload(self) -> None:
        since = (datetime.now(UTC) - timedelta(days=self._window_days)).date().isoformat()
        collected: list[OrderRecord] = []
        cursor: str | None = None
        while len(collected) < self._max_orders:
            data = await self._executor.execute(
                ORDERS_PAGE,
                {
                    "first": self._page_size,
                    "after": cursor,
                    "query": f"created_at:>={since}",
                },
            )
            page = data.get("orders") or {}
            for node in page.get("nodes") or []:
                if (record := OrderRecord.from_node(node)) is not None:
                    collected.append(record)
            info = page.get("pageInfo") or {}
            if not info.get("hasNextPage") or not (cursor := info.get("endCursor")):
                break
        self._orders = tuple(collected)
        self._currency = collected[0].currency if collected else None
        self._loaded_at = time.monotonic()

    async def daily(
        self, start: date, end: date, product_ids: frozenset[str] | None = None
    ) -> list[DayRow]:
        return daily_rows(await self.orders(), start, end, product_ids)

    async def totals(
        self, start: date, end: date, product_ids: frozenset[str] | None = None
    ) -> tuple[float, int]:
        rows = await self.daily(start, end, product_ids)
        return round(sum(row.sales for row in rows), 2), sum(row.orders for row in rows)

    async def units_by_product(self, days: int = 30) -> dict[str, int]:
        return units_by_product(await self.orders(), days)

    async def refund_units_by_product(self, days: int = 30) -> dict[str, int]:
        return refund_units_by_product(await self.orders(), days)
