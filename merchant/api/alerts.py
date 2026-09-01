# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Inventory and order alerts, computed rather than stored.

Shopify has no "alert" record to read, so each one here is a rule applied to the catalog
and the order scan. The rules and their thresholds live in ``data/thresholds.json`` so the
figures the agent quotes can be traced to a number an operator set, not to a constant
buried in a comparison.

Two of the interface's ``OrderIssue`` kinds are absent. ``damaged`` has no Admin API
equivalent, and ``buyer_message`` would mean reading customer-authored text out of order
notes: the scopes for that are a separate approval, and this example does not ask for them.
Omitting a kind is the honest mapping; inventing one would put figures in front of the
operator that nothing in the store supports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from merchant_agent import AlertCounts, InventoryAlert, OrderIssue

from .catalog import CatalogCache, ProductRecord
from .orders import OrderRecord, OrderScan, refund_units_by_product, units_by_product


def _int_map(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    values: dict[str, int] = {}
    for key, value in raw.items():
        try:
            values[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return values


def _number(raw: dict[str, object], key: str, default: float) -> float:
    try:
        return float(raw[key])  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError):
        return default


@dataclass(frozen=True)
class AlertRules:
    """The thresholds every alert is measured against."""

    low_stock_default: int = 8
    low_stock_by_type: dict[str, int] = field(default_factory=dict)
    low_stock_by_handle: dict[str, int] = field(default_factory=dict)
    slow_mover_max_units_30d: int = 2
    slow_mover_min_stock: int = 12
    fulfilment_sla_days: int = 3
    return_spike_pct: float = 20.0
    return_spike_min_units: int = 4

    @classmethod
    def load(cls, path: Path, *, low_stock_default: int, fulfilment_sla_days: int) -> AlertRules:
        """The rules from ``path``, with the two settings the environment can override
        taking precedence over the file so one store's deployment can be retuned without
        editing shared data. A missing or partial file leaves the defaults in place."""
        raw: dict[str, object] = {}
        if path.exists():
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict):
                raw = loaded
        return cls(
            low_stock_default=low_stock_default,
            low_stock_by_type=_int_map(raw.get("low_stock_by_type")),
            low_stock_by_handle=_int_map(raw.get("low_stock_by_handle")),
            slow_mover_max_units_30d=int(_number(raw, "slow_mover_max_units_30d", 2)),
            slow_mover_min_stock=int(_number(raw, "slow_mover_min_stock", 12)),
            fulfilment_sla_days=fulfilment_sla_days,
            return_spike_pct=_number(raw, "return_spike_pct", 20.0),
            return_spike_min_units=int(_number(raw, "return_spike_min_units", 4)),
        )

    def low_stock_threshold(self, record: ProductRecord) -> int:
        """The threshold that applies to one product: its own, then its type's, then the
        store default."""
        if (own := self.low_stock_by_handle.get(record.handle)) is not None:
            return own
        if (
            record.product_type
            and (typed := self.low_stock_by_type.get(record.product_type)) is not None
        ):
            return typed
        return self.low_stock_default


# Each rule is a pure function over records the caller has already fetched, so the agent's
# reads (which fetch) and the portal's synchronous reads (which serve the cache the route
# just filled) apply exactly the same thresholds.


def _inventory_kind(
    record: ProductRecord, sold: int, rules: AlertRules
) -> Literal["low_stock", "slow_mover"] | None:
    if record.tracks_inventory and record.stock <= rules.low_stock_threshold(record):
        return "low_stock"
    if record.stock >= rules.slow_mover_min_stock and sold <= rules.slow_mover_max_units_30d:
        return "slow_mover"
    return None


def inventory_alerts_for(
    records: list[ProductRecord], units: dict[str, int], rules: AlertRules
) -> list[InventoryAlert]:
    alerts: list[InventoryAlert] = []
    for record in records:
        sold = units.get(record.product_id, 0)
        kind = _inventory_kind(record, sold, rules)
        if kind is None:
            continue
        alerts.append(
            InventoryAlert(
                listing_id=record.product_id,
                title=record.title,
                kind=kind,
                stock=record.stock,
                threshold=rules.low_stock_threshold(record) if kind == "low_stock" else None,
                days_of_cover=round(record.stock / (sold / 30), 1) if sold else None,
                sales_last_30d=sold,
                storefront_visible=record.status == "active",
            )
        )
    # Low stock first, then the shallowest stock: the order the portal shows and the order
    # the agent reads them in.
    alerts.sort(key=lambda alert: (alert.kind != "low_stock", alert.stock))
    return alerts


def delayed_orders_for(
    orders: tuple[OrderRecord, ...], rules: AlertRules, *, now: datetime | None = None
) -> list[OrderIssue]:
    """Orders still unfulfilled past the deployment's fulfilment window. Shopify tracks the
    fulfilment state; the window is this example's rule, not the platform's."""
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(days=rules.fulfilment_sla_days)
    issues = [
        OrderIssue(
            issue_id=f"delayed-{order.name.lstrip('#')}",
            order_id=order.name,
            kind="delayed",
            summary=(
                f"{order.name} has been unfulfilled for {(moment - order.created_at).days} days "
                f"({order.units} units), past the {rules.fulfilment_sla_days}-day fulfilment "
                "window"
            ),
            listing_id=order.lines[0].product_id if order.lines else None,
            opened_at=order.created_at,
        )
        for order in orders
        if order.is_open and order.created_at <= cutoff
    ]
    issues.sort(key=lambda issue: issue.opened_at or moment)
    return issues


def return_spikes_for(
    sold: dict[str, int],
    refunded: dict[str, int],
    titles: dict[str, str],
    rules: AlertRules,
) -> list[OrderIssue]:
    """Products refunded above the configured share of their units. A refund is attributed
    to every line on the refunded order, so this reads as a signal to look at a product
    rather than as a per-item return rate."""
    issues: list[OrderIssue] = []
    for product_id, refund_units in sorted(refunded.items()):
        total = sold.get(product_id, 0)
        if refund_units < rules.return_spike_min_units or total <= 0:
            continue
        rate = round(refund_units / total * 100, 1)
        if rate < rules.return_spike_pct:
            continue
        issues.append(
            OrderIssue(
                issue_id=f"returns-{product_id.rsplit('/', 1)[-1]}",
                order_id=f"{refund_units} refunded units, last 30 days",
                kind="return_spike",
                summary=(
                    f"{titles.get(product_id, product_id)} was refunded on {rate}% of the "
                    f"{total} units sold in the last 30 days"
                ),
                listing_id=product_id,
            )
        )
    return issues


def counts_for(
    inventory: list[InventoryAlert], issues: list[OrderIssue], pending_changes: int
) -> AlertCounts:
    return AlertCounts(
        low_stock=sum(1 for alert in inventory if alert.kind == "low_stock"),
        slow_movers=sum(1 for alert in inventory if alert.kind == "slow_mover"),
        order_issues=len(issues),
        pending_changes=pending_changes,
    )


class AlertBuilder:
    """The rules above over the catalog and order caches. The ``*_cached`` variants read
    what is already loaded, for the portal's synchronous routes."""

    def __init__(self, catalog: CatalogCache, orders: OrderScan, rules: AlertRules) -> None:
        self._catalog = catalog
        self._orders = orders
        self.rules = rules

    async def inventory_alerts(self) -> list[InventoryAlert]:
        return inventory_alerts_for(
            await self._catalog.all(), await self._orders.units_by_product(days=30), self.rules
        )

    def inventory_alerts_cached(self) -> list[InventoryAlert]:
        return inventory_alerts_for(
            self._catalog.cached(), units_by_product(self._orders.cached(), 30), self.rules
        )

    async def order_issues(self) -> list[OrderIssue]:
        orders = await self._orders.orders()
        titles = {record.product_id: record.title for record in await self._catalog.all()}
        return delayed_orders_for(orders, self.rules) + return_spikes_for(
            units_by_product(orders, 30), refund_units_by_product(orders, 30), titles, self.rules
        )

    def order_issues_cached(self) -> list[OrderIssue]:
        orders = self._orders.cached()
        titles = {record.product_id: record.title for record in self._catalog.cached()}
        return delayed_orders_for(orders, self.rules) + return_spikes_for(
            units_by_product(orders, 30), refund_units_by_product(orders, 30), titles, self.rules
        )

    async def counts(self, pending_changes: int) -> AlertCounts:
        return counts_for(await self.inventory_alerts(), await self.order_issues(), pending_changes)
