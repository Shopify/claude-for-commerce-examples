# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Reporting periods, and the two ways this example answers a metrics question.

ShopifyQL through ``shopifyqlQuery`` is the first choice: it is the Admin API's own
reporting surface and the closest thing to a literal mapping for ``query_metrics``. It
needs the ``read_reports`` scope, some of its fields additionally need approved
protected-customer-data access, and its availability varies by plan, so the first refusal
is remembered and every later question is answered from the order scan instead. Which
path served is reported in the merchant context, so the operator is never left guessing
where a number came from.

Sessions and therefore conversion rate have no order-derived equivalent. When ShopifyQL
cannot supply them the snapshot reports zero and ``source_note`` says so, rather than
presenting a derived number as measured traffic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from demo_common.merchant_fixtures import change_pct
from merchant_agent import AlertCounts, BusinessSnapshot, MetricPoint, MetricSeries

from .admin_client import AdminAPIError, AdminExecutor
from .catalog import CatalogCache
from .orders import OrderScan
from .queries import SHOPIFYQL_QUERY

logger = logging.getLogger(__name__)

_PERIOD_DAYS = {
    "today": 1,
    "yesterday": 1,
    "last_7_days": 7,
    "last 7 days": 7,
    "7d": 7,
    "this_week": 7,
    "last_week": 7,
    "last_14_days": 14,
    "last_30_days": 30,
    "last 30 days": 30,
    "30d": 30,
    "this_month": 30,
    "last_month": 30,
    "last_90_days": 90,
    "last 90 days": 90,
    "90d": 90,
    "quarter": 90,
}

_MONEY_METRICS = frozenset({"sales", "revenue", "average_order_value", "aov"})


@dataclass(frozen=True)
class Period:
    """A closed day range. Periods end yesterday: today is still accumulating, and a
    partial day compared against whole ones reads as a collapse in sales."""

    start: date
    end: date

    @property
    def label(self) -> str:
        return f"{self.start.isoformat()}/{self.end.isoformat()}"

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> Period:
        return Period(
            start=self.start - timedelta(days=self.days), end=self.start - timedelta(days=1)
        )


def resolve_period(raw: str | None, *, default_days: int = 7, today: date | None = None) -> Period:
    """The period a request names: an ISO ``start/end`` range, a known window, or the
    default. Bounded by yesterday either way."""
    yesterday = (today or datetime.now(UTC).date()) - timedelta(days=1)
    cleaned = (raw or "").strip().lower()
    if "/" in cleaned:
        try:
            start_text, end_text = cleaned.split("/", 1)
            start = date.fromisoformat(start_text.strip())
            end = min(date.fromisoformat(end_text.strip()), yesterday)
            if start <= end:
                return Period(start=start, end=end)
        except ValueError:
            pass
    if cleaned == "today":
        return Period(start=yesterday, end=yesterday)
    days = _PERIOD_DAYS.get(cleaned, default_days)
    if cleaned == "yesterday":
        return Period(start=yesterday, end=yesterday)
    if cleaned == "last_week":
        end = yesterday - timedelta(days=7)
        return Period(start=end - timedelta(days=6), end=end)
    return Period(start=yesterday - timedelta(days=days - 1), end=yesterday)


def canonical_metric(metric: str) -> str:
    return metric.strip().lower().replace(" ", "_")


@dataclass(frozen=True)
class Table:
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def value(self, column: str, row: int = 0) -> float | None:
        if column not in self.columns or row >= len(self.rows):
            return None
        try:
            return float(self.rows[row][self.columns.index(column)])
        except (ValueError, IndexError):
            return None


class MetricsSource:
    """Answers the snapshot and series reads, preferring ShopifyQL and falling back to the
    order scan for good."""

    def __init__(
        self,
        executor: AdminExecutor,
        *,
        order_scan: OrderScan,
        catalog: CatalogCache,
        currency: str,
        shopifyql_enabled: bool = True,
    ) -> None:
        self._executor = executor
        self._orders = order_scan
        self._catalog = catalog
        self._currency = currency
        self._shopifyql_enabled = shopifyql_enabled
        self._shopifyql_available: bool | None = None if shopifyql_enabled else False
        self._sessions_available: bool | None = None if shopifyql_enabled else False

    @property
    def source_note(self) -> str:
        """One line for the fenced merchant context, so the model can say where the
        figures came from and what is missing."""
        if self._shopifyql_available:
            traffic = (
                "sessions from the same source"
                if self._sessions_available
                else "sessions unavailable to this token, so traffic and conversion read 0"
            )
            return f"metrics from ShopifyQL; {traffic}"
        reason = "disabled by configuration" if not self._shopifyql_enabled else "unavailable"
        return (
            f"metrics derived from the trailing order scan (ShopifyQL {reason}); "
            "traffic and conversion read 0 because orders carry no session data"
        )

    # -- ShopifyQL ----------------------------------------------------------------

    async def _shopifyql(self, query: str) -> Table | None:
        if self._shopifyql_available is False:
            return None
        try:
            data = await self._executor.execute(SHOPIFYQL_QUERY, {"query": query})
        except AdminAPIError as error:
            logger.info("ShopifyQL unavailable, using the order scan instead: %s", error)
            self._shopifyql_available = False
            return None
        payload = data.get("shopifyqlQuery") or {}
        if payload.get("parseErrors"):
            messages = "; ".join(
                str(entry.get("message")) for entry in payload["parseErrors"] or []
            )
            logger.info("ShopifyQL rejected a query (%s): %s", messages, query)
            return None
        table = payload.get("tableData") or {}
        columns = tuple(
            str(column.get("name")) for column in (table.get("columns") or []) if column.get("name")
        )
        if not columns:
            return None
        self._shopifyql_available = True
        return Table(
            columns=columns,
            rows=tuple(tuple(str(cell) for cell in row) for row in (table.get("rowData") or [])),
        )

    async def _sales_row(self, period: Period) -> Table | None:
        return await self._shopifyql(
            "FROM sales SHOW total_sales, orders, average_order_value "
            f"SINCE {period.start.isoformat()} UNTIL {period.end.isoformat()}"
        )

    async def _sessions_total(self, period: Period) -> int | None:
        if self._sessions_available is False:
            return None
        table = await self._shopifyql(
            "FROM sessions SHOW total_sessions "
            f"SINCE {period.start.isoformat()} UNTIL {period.end.isoformat()}"
        )
        if table is None or (total := table.value("total_sessions")) is None:
            self._sessions_available = False
            return None
        self._sessions_available = True
        return int(total)

    # -- Snapshot ------------------------------------------------------------------

    async def snapshot(self, period_text: str | None, alerts: AlertCounts) -> BusinessSnapshot:
        period = resolve_period(period_text, default_days=7)
        prior = period.previous()
        current = await self._window_totals(period)
        previous = await self._window_totals(prior)
        traffic = await self._sessions_total(period) or 0
        prior_traffic = (await self._sessions_total(prior) or 0) if traffic else 0
        conversion = round(current[1] / traffic * 100, 2) if traffic else 0.0
        prior_conversion = round(previous[1] / prior_traffic * 100, 2) if prior_traffic else 0.0
        return BusinessSnapshot(
            period=period.label,
            compare_to=prior.label,
            sales=current[0],
            orders=current[1],
            traffic=traffic,
            conversion_rate=conversion,
            average_order_value=round(current[0] / current[1], 2) if current[1] else 0.0,
            sales_change_pct=change_pct(current[0], previous[0]),
            orders_change_pct=change_pct(current[1], previous[1]),
            traffic_change_pct=change_pct(traffic, prior_traffic) if prior_traffic else None,
            conversion_change_pct=change_pct(conversion, prior_conversion)
            if prior_conversion
            else None,
            currency=self._currency,
            alerts=alerts,
        )

    async def _window_totals(self, period: Period) -> tuple[float, int]:
        if (table := await self._sales_row(period)) is not None:
            sales = table.value("total_sales")
            orders = table.value("orders")
            if sales is not None and orders is not None:
                return round(sales, 2), int(orders)
        return await self._orders.totals(period.start, period.end)

    # -- Series --------------------------------------------------------------------

    async def series(
        self,
        metric: str,
        period_text: str | None,
        granularity: str,
        segment: str | None,
    ) -> MetricSeries:
        name = canonical_metric(metric)
        period = resolve_period(period_text, default_days=30)
        bucket = "week" if granularity == "week" else "month" if granularity == "month" else "day"
        segment_name = (segment or "").strip().lower() or None
        points = await self._series_points(name, period, bucket, segment_name)
        return MetricSeries(
            metric=name,
            unit=self._currency if name in _MONEY_METRICS else None,
            granularity=bucket,
            period=period.label,
            segment=segment_name,
            points=points,
        )

    async def _series_points(
        self, name: str, period: Period, bucket: str, segment: str | None
    ) -> list[MetricPoint]:
        if segment is None and (column := _SHOPIFYQL_COLUMNS.get(name)):
            table = await self._shopifyql(
                f"FROM sales SHOW {column} "
                f"SINCE {period.start.isoformat()} UNTIL {period.end.isoformat()} "
                f"GROUP BY {bucket}"
            )
            if table is not None and table.rows and len(table.columns) >= 2:
                index = table.columns.index(column) if column in table.columns else 1
                points = [
                    MetricPoint(date=row[0], value=float(row[index]))
                    for row in table.rows
                    if len(row) > index and _is_number(row[index])
                ]
                if points:
                    return points
        return await self._derived_points(name, period, bucket, segment)

    async def _derived_points(
        self, name: str, period: Period, bucket: str, segment: str | None
    ) -> list[MetricPoint]:
        product_ids = await self._segment_products(segment) if segment else None
        rows = await self._orders.daily(period.start, period.end, product_ids)
        span = 1 if bucket == "day" else 7 if bucket == "week" else 30
        points: list[MetricPoint] = []
        for start in range(0, len(rows), span):
            chunk = rows[start : start + span]
            sales = round(sum(row.sales for row in chunk), 2)
            orders = sum(row.orders for row in chunk)
            if name in {"orders"}:
                value = float(orders)
            elif name in {"average_order_value", "aov"}:
                value = round(sales / orders, 2) if orders else 0.0
            elif name in {"traffic", "sessions", "conversion", "conversion_rate"}:
                value = 0.0
            else:
                value = sales
            points.append(MetricPoint(date=chunk[0].day.isoformat(), value=value))
        return points

    async def _segment_products(self, segment: str) -> frozenset[str]:
        """The products a segment name covers, matched against product type, vendor, and
        title so a category the operator names in their own words still resolves."""
        wanted = segment.replace("-", " ").replace("_", " ").strip()
        return frozenset(
            record.product_id
            for record in await self._catalog.all()
            if wanted in (record.product_type or "").lower()
            or wanted in (record.vendor or "").lower()
            or wanted in record.title.lower()
        )


_SHOPIFYQL_COLUMNS = {
    "sales": "total_sales",
    "revenue": "total_sales",
    "orders": "orders",
    "average_order_value": "average_order_value",
    "aov": "average_order_value",
}


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True
