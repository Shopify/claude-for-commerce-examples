# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The adapter that lets this example reuse the shared merchant router unchanged.

``build_merchant_router`` takes a storefront alongside the merchant backend, because the
other verticals run both halves in one process and an approved change has to show up in
the storefront immediately. This example has no buyer half: it is a merchant portal over a
real store, and the storefront is Shopify's own.

So rather than a bespoke router — which would mean reimplementing the approval gate, the
SSE streaming, and the memory routes — this class supplies the three things the merchant
routes actually read from a storefront: the store's name, its catalog size, and a recent
order feed. Everything else on the protocol is a buyer-side operation that has no meaning
here, and asking for one says so rather than returning something empty.

Both reads the router makes are synchronous, so they serve from what the caches already
hold. ``main.py`` warms them at startup; a cold cache reports an empty store for one
request rather than blocking the event loop on a network call.
"""

from __future__ import annotations

from typing import Never

from shopping_agent import Cart, Order, OrderItem, OrderStatus, ProductDetails

from .catalog import ProductRecord
from .orders import OrderRecord
from .shopify_backend import ShopifyMerchantBackend

# displayFulfillmentStatus and displayFinancialStatus onto the buyer-side status the order
# feed shows. Fulfilled maps to ``shipped`` rather than ``delivered``: the Admin API knows
# the store handed the order off, not that it arrived.
_FULFILMENT_STATUS = {
    "FULFILLED": OrderStatus.SHIPPED,
    "PARTIALLY_FULFILLED": OrderStatus.PROCESSING,
    "IN_PROGRESS": OrderStatus.PROCESSING,
    "PENDING_FULFILLMENT": OrderStatus.PROCESSING,
    "OPEN": OrderStatus.PROCESSING,
    "UNFULFILLED": OrderStatus.PROCESSING,
    "SCHEDULED": OrderStatus.PROCESSING,
    "ON_HOLD": OrderStatus.DELAYED,
    "RESTOCKED": OrderStatus.CANCELLED,
}
_FINANCIAL_STATUS = {
    "REFUNDED": OrderStatus.REFUNDED,
    "PARTIALLY_REFUNDED": OrderStatus.RETURN_INITIATED,
    "VOIDED": OrderStatus.CANCELLED,
}


def _unsupported(operation: str) -> Never:
    raise NotImplementedError(
        f"{operation} is a buyer-side operation; this example is a merchant portal over a "
        "live Shopify store and runs no storefront of its own"
    )


class ShopifyStoreView:
    """A read-only view of the store for the shared merchant routes."""

    def __init__(self, backend: ShopifyMerchantBackend) -> None:
        self._backend = backend

    @property
    def store_name(self) -> str:
        return self._backend.store_name

    @property
    def products(self) -> dict[str, ProductDetails]:
        """The cached catalog as buyer-side records. Only the count is read by the merchant
        routes, but building the real records keeps the health check's figure equal to the
        catalog the agent sees rather than to a separate tally."""
        currency = self._backend.display_currency
        return {
            record.product_id: _to_details(record, currency)
            for record in self._backend.catalog.cached()
        }

    def recent_orders(self, limit: int = 6) -> list[Order]:
        currency = self._backend.display_currency
        return [_to_order(record, currency) for record in self._backend.orders.cached()[:limit]]

    def reset_session(self, session_id: str) -> None:
        """Nothing is held per buyer session here; the merchant session state lives in the
        router's own store."""
        del session_id

    async def get_cart(self, *args: object, **kwargs: object) -> Cart:
        del args, kwargs
        _unsupported("get_cart")

    async def add_to_cart(self, *args: object, **kwargs: object) -> Cart:
        del args, kwargs
        _unsupported("add_to_cart")


def _to_details(record: ProductRecord, currency: str) -> ProductDetails:
    return ProductDetails(
        product_id=record.product_id,
        title=record.title,
        brand=record.vendor,
        price=record.price,
        currency=currency,
        image_url=record.image_url,
        category=record.product_type,
        attributes=record.attributes(),
        in_stock=record.status != "out_of_stock",
        short_description=record.short_description,
        long_description=record.description,
    )


def _to_order(record: OrderRecord, currency: str) -> Order:
    return Order(
        order_id=record.name,
        status=_FINANCIAL_STATUS.get(record.financial_status)
        or _FULFILMENT_STATUS.get(record.fulfillment_status, OrderStatus.PROCESSING),
        placed_at=record.created_at,
        items=[
            OrderItem(
                product_id=line.product_id or "",
                title=line.title,
                quantity=line.quantity,
                price=round(line.revenue / line.quantity, 2) if line.quantity else 0.0,
            )
            for line in record.lines
        ],
        total=record.total,
        currency=record.currency or currency,
    )
