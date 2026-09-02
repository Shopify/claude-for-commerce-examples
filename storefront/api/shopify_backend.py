# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""``StorefrontBackend`` over a live Shopify shop's UCP tools: catalog reads through
``search_catalog``/``get_product``/``lookup_catalog``, the cart through the UCP cart
capability (``create_cart``/``update_cart``/``get_cart`` on the same endpoint;
``update_cart``'s line list replaces the cart's contents, so every write re-reads the
cart and sends the full desired state) with one Shopify cart id kept per session — the
storefront's own cart when the page hands it over (``attach_cart``) — and policies
through ``search_shop_policies_and_faqs``. A cart id the shop no longer accepts is
dropped and the write retried once into a fresh cart. Search returns products under their
``gid://shopify/Product/…`` ids and details list variants under their
``gid://shopify/ProductVariant/…`` ids, so both enter provenance; cart lines carry the
variant id. Preferences are a static guest profile and fulfillment is empty; a session
signed in with Shop (``identity.py``) differs only in the buyer-linked token
and buyer IP its catalog calls carry.

Checkout stays a handoff: the host's cart payloads call ``checkout_url_for``,
which lazily stages a non-empty cart as a UCP checkout (``create_checkout``, re-synced
by ``update_checkout``) and returns its ``continue_url`` for the customer to finish on
Shopify's own pages. ``complete_checkout`` is never called — it places the order and
takes payment, and nothing in this repo charges anyone. Orders are the agent-placed
ones only: after the customer completes, a buyer's ask (never a schedule) lets
``get_checkout`` name the order, the session ledger maps checkout id to order id, and
``get_order`` — Token tier, the agent token paired with the buyer IP — fills in totals,
fulfillment events, and tracking. Until the key carries the orders scope the shop
answers ``orders_not_allowed``: the backend flips ``orders_enabled`` off and the order
tools return nothing, and the deployment's config (``agent_config.py``) tells the
model how to answer when they do.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote

from shopping_agent import (
    Cart,
    CartItem,
    FulfillmentOption,
    Order,
    OrderItem,
    OrderStatus,
    Policy,
    Product,
    ProductDetails,
    SearchFilters,
    ShoppingSessionContext,
    StorefrontBackend,
    UserPreferences,
)

from .identity import AgentToken, ShopSignIn
from .ucp_client import UcpAuthError, UcpCartGoneError, UcpClient, UcpError

logger = logging.getLogger(__name__)

# The buyer context every catalog call sends; the shop prices for its own region
# regardless (the demo store answers in CAD).
_CONTEXT = {"address_country": "US", "language": "en"}
_VARIANT_PREFIX = "gid://shopify/ProductVariant/"
_CART_PREFIX = "gid://shopify/Cart/"


def cart_gid(value: str) -> str:
    """The UCP id of a cart a storefront already holds. A Shopify storefront keeps it in
    the ``cart`` cookie as ``<token>?key=<key>``, percent-encoded; the Storefront API
    hands out the full gid. Either form normalizes to ``gid://shopify/Cart/<token>?key=<key>``."""
    token = unquote(value.strip())
    return token if token.startswith(_CART_PREFIX) else f"{_CART_PREFIX}{token}"
_TAG = re.compile(r"<[^>]+>")

# The order envelope's fulfillment event and adjustment types, mapped to the shared
# OrderStatus; both type fields are open strings, so unknown values keep the last
# status. Missing scope answers orders_not_allowed; a just-completed checkout's order
# can be order_not_found for a few seconds.
_EVENT_STATUS = {
    "shipped": OrderStatus.SHIPPED,
    "in_transit": OrderStatus.SHIPPED,
    "out_for_delivery": OrderStatus.OUT_FOR_DELIVERY,
    "delivered": OrderStatus.DELIVERED,
    "picked_up": OrderStatus.DELIVERED,
}
_ADJUSTMENT_STATUS = {
    "cancellation": OrderStatus.CANCELLED,
    "return": OrderStatus.RETURN_INITIATED,
    "refund": OrderStatus.REFUNDED,
}
_ORDER_ERROR_CODES = {"orders_not_allowed", "order_not_found", "invalid_order_id"}


def _strip_html(value: Any) -> str | None:
    html = (value or {}).get("html") if isinstance(value, dict) else value
    if not html:
        return None
    return re.sub(r"\s+", " ", _TAG.sub(" ", html)).strip() or None


def _minor(money: dict[str, Any]) -> tuple[float, str]:
    """UCP money: integer minor units (``{"amount": 4000, "currency": "CAD"}``)."""
    return round(money["amount"] / 100, 2), money["currency"]


def _image(record: dict[str, Any]) -> str | None:
    for media in record.get("media") or []:
        if media.get("type") == "image" and media.get("url"):
            return media["url"]
    return None


@dataclass
class _SessionState:
    cart_id: str | None = None
    checkout_url: str | None = None
    currency: str = "USD"
    default_variant: dict[str, str] = field(default_factory=dict)  # product gid -> variant gid
    lines: dict[str, tuple[str, int]] = field(default_factory=dict)  # variant -> (line id, qty)
    variant_of: dict[str, str] = field(default_factory=dict)  # variant gid -> product gid
    checkout_id: str | None = None  # the staged UCP checkout, kept for re-sync
    checkout_handoff_url: str | None = None  # its continue_url; cleared by cart writes
    order_of_checkout: dict[str, str] = field(default_factory=dict)  # checkout id -> order id
    order_seen_at: dict[str, datetime] = field(default_factory=dict)  # order id -> ledger time


class ShopifyStorefrontBackend(StorefrontBackend):
    def __init__(
        self,
        client: UcpClient | None = None,
        store_name: str = "Shopify Demo Store",
        identity: ShopSignIn | None = None,
        agent_token: AgentToken | None = None,
    ):
        self.client = client or UcpClient()
        self.store_name = store_name
        # Sign in with Shop. Signed-in sessions get their buyer-linked token and
        # buyer IP on catalog calls; without it every call is anonymous, byte-identical
        # to a backend with no identity wired. Cart and policy calls are guest either way.
        self.identity = identity
        # The deployment's Global API token, for Token-tier get_order only.
        self.agent_token = agent_token
        # Lazily filled from responses; also serves the host's public /api/products reads.
        self.products: dict[str, ProductDetails] = {}
        # Backend-global display caches beside it: a variant image per variant gid, and
        # the default variant per product gid (what an add without a variant resolves
        # to). Filled by session reads and by the startup warm-up alike.
        self._variant_images: dict[str, str] = {}
        self.default_variants: dict[str, str] = {}
        self._sessions: dict[str, _SessionState] = {}
        # Flipped off the first time the shop answers orders_not_allowed (the key's
        # read_global_api_orders grant is pending): the order tools then return nothing
        # without asking again.
        self.orders_enabled = True

    def _state(self, session: ShoppingSessionContext) -> _SessionState:
        return self._sessions.setdefault(session.session_id, _SessionState())

    def reset_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        if self.identity is not None:
            self.identity.drop(session_id)

    def cart_id_for(self, session_id: str) -> str | None:
        """The session's UCP cart id, for the host's cart payloads: a page embedding the
        agent sets the storefront's cart cookie from it, so the buyer's cart and the
        agent's are one cart."""
        state = self._sessions.get(session_id)
        return state.cart_id if state else None

    async def attach_cart(self, session_id: str, cart_id: str) -> Cart | None:
        """Bind the session to a cart that exists already — the storefront's own, whose id
        the buyer's page holds — and read it as the session's starting point. ``None``
        when the shop does not know the id; the session then keeps whatever it had."""
        try:
            payload = await self.client.call_ucp("get_cart", {"id": cart_id})
        except UcpCartGoneError:
            return None
        state = self._sessions.setdefault(session_id, _SessionState())
        # A checkout staged for the previous cart does not describe this one.
        state.checkout_id = None
        state.checkout_handoff_url = None
        return self._map_cart(state, payload)

    def recent_orders(self, limit: int = 6) -> list[Order]:
        return []

    async def checkout_url_for(self, session_id: str) -> str | None:
        """The session's handoff link for the host's cart payloads. A non-empty cart
        is staged as a UCP checkout lazily — created on the first ask, re-synced once
        a cart write has invalidated the last staging — and its ``continue_url``
        returned; the cart's own hosted checkout stands in when there is nothing to
        stage or staging fails."""
        state = self._sessions.get(session_id)
        if state is None:
            return None
        if state.lines and state.checkout_handoff_url is None:
            await self._stage_handoff(state)
        return state.checkout_handoff_url or state.checkout_url

    # -- Catalog ------------------------------------------------------------------

    async def _call_catalog(
        self, session: ShoppingSessionContext, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Anonymous unless the session is signed in; then the buyer-linked token and
        buyer IP ride along. A 401 means the token expired (~an hour, no refresh):
        re-mint once and retry, and if that fails the session continues anonymous."""
        auth = None
        if self.identity is not None:
            auth = await self.identity.credentials_for(session.session_id)
        if auth is None:
            return await self.client.call_ucp(name, arguments)
        try:
            return await self.client.call_ucp(
                name, arguments, bearer_token=auth[0], buyer_ip=auth[1]
            )
        except UcpAuthError:
            auth = await self.identity.refresh(session.session_id)
            if auth is None:
                return await self.client.call_ucp(name, arguments)
            return await self.client.call_ucp(
                name, arguments, bearer_token=auth[0], buyer_ip=auth[1]
            )

    async def search_products(
        self,
        session: ShoppingSessionContext,
        query: str,
        filters: SearchFilters | None = None,
        limit: int = 8,
    ) -> list[Product]:
        catalog: dict[str, Any] = {
            "query": query,
            "context": _CONTEXT,
            "pagination": {"limit": limit},
        }
        price: dict[str, int] = {}
        if filters and filters.min_price is not None:
            price["min"] = int(filters.min_price * 100)
        if filters and filters.max_price is not None:
            price["max"] = int(filters.max_price * 100)
        if price:
            catalog["filters"] = {"price": price}
        payload = await self._call_catalog(session, "search_catalog", {"catalog": catalog})
        state = self._state(session)
        return [self._remember_product(state, record) for record in payload.get("products") or []]

    async def get_product_details(
        self, session: ShoppingSessionContext, product_id: str
    ) -> ProductDetails | None:
        state = self._state(session)
        if product_id.startswith(_VARIANT_PREFIX):
            parent_id = state.variant_of.get(product_id)
            if parent_id is None:
                return None
            details = self.products.get(parent_id) or await self.get_product_details(
                session, parent_id
            )
            if details is None:
                return None
            for variant in details.variants:
                if variant.product_id == product_id:
                    return ProductDetails(
                        **variant.model_dump(), long_description=details.long_description
                    )
            return None
        try:
            payload = await self._call_catalog(
                session, "get_product", {"catalog": {"id": product_id, "context": _CONTEXT}}
            )
            record = payload.get("product")
        except UcpError:
            try:
                payload = await self._call_catalog(
                    session,
                    "lookup_catalog",
                    {"catalog": {"ids": [product_id], "context": _CONTEXT}},
                )
            except UcpError:
                return None
            found = payload.get("products") or []
            record = found[0] if found else None
        if not record:
            return None
        return self._remember_product(state, record)

    def _remember_product(self, state: _SessionState, record: dict[str, Any]) -> ProductDetails:
        product_id = record["id"]
        price, currency = _minor(record["price_range"]["min"])
        description = _strip_html(record.get("description"))
        variants = [self._map_variant(record, variant) for variant in record.get("variants") or []]
        # get_product resolves to the one selected variant while search lists them all;
        # merge with what the session has already seen so the fuller list survives.
        existing = self.products.get(product_id)
        if existing:
            merged = {v.product_id: v for v in existing.variants}
            merged.update({v.product_id: v for v in variants})
            variants = list(merged.values())
        available = [v for v in variants if v.in_stock]
        details = ProductDetails(
            product_id=product_id,
            title=record["title"],
            price=price,
            currency=currency,
            image_url=_image(record),
            category=(record.get("tags") or [None])[0],
            in_stock=bool(available) if variants else True,
            short_description=description[:200] if description else None,
            long_description=description,
            variants=variants,
        )
        state.currency = currency
        for variant in variants:
            state.variant_of[variant.product_id] = product_id
            if variant.image_url:
                self._variant_images[variant.product_id] = variant.image_url
        if available or variants:
            state.default_variant[product_id] = (available or variants)[0].product_id
            self.default_variants[product_id] = state.default_variant[product_id]
        self.products[product_id] = details
        return details

    def warm_display_cache(self, details: ProductDetails) -> None:
        """Record a warmed catalog entry (``catalog_warmup``) in the backend-global
        display caches, exactly as ``get_product_details`` would. The boundary: the
        display cache is not provenance. Nothing here touches ``_SessionState`` or any
        ``ShoppingSessionState.seen_products`` — those fill only from a session's own
        tool calls, so a warmed-but-unseen product still fails the cart's provenance
        gate until this session reads it."""
        self.products[details.product_id] = details
        for variant in details.variants:
            if variant.image_url:
                self._variant_images[variant.product_id] = variant.image_url
        available = [v for v in details.variants if v.in_stock]
        if available or details.variants:
            self.default_variants[details.product_id] = (available or details.variants)[
                0
            ].product_id

    def _map_variant(self, record: dict[str, Any], variant: dict[str, Any]) -> Product:
        price, currency = _minor(variant["price"])
        options = {opt["name"]: opt["label"] for opt in variant.get("options") or []}
        return Product(
            product_id=variant["id"],
            title=f"{record['title']} — {variant['title']}",
            price=price,
            currency=currency,
            image_url=_image(variant) or _image(record),
            attributes=options,
            in_stock=bool((variant.get("availability") or {}).get("available")),
        )

    # -- Cart ---------------------------------------------------------------------

    async def get_cart(self, session: ShoppingSessionContext) -> Cart:
        state = self._state(session)
        if state.cart_id is None:
            return Cart(currency=state.currency)
        try:
            payload = await self.client.call_ucp("get_cart", {"id": state.cart_id})
        except UcpCartGoneError:
            self._drop_cart(state)
            return Cart(currency=state.currency)
        return self._map_cart(state, payload)

    async def add_to_cart(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        state = self._state(session)
        variant_id = await self._resolve_variant(session, state, product_id)
        await self._refresh_lines(state)
        already = state.lines.get(variant_id)
        line_items = self._line_items(state, variant_id, (already[1] if already else 0) + quantity)
        if state.cart_id is None:
            return await self._create_cart(state, line_items)
        try:
            payload = await self.client.call_ucp(
                "update_cart", {"id": state.cart_id, "cart": {"line_items": line_items}}
            )
        except UcpCartGoneError:
            # The shop dropped the cart; start a fresh one with just the new item.
            self._drop_cart(state)
            return await self._create_cart(
                state, [{"item": {"id": variant_id}, "quantity": quantity}]
            )
        return self._map_cart_after_write(state, payload)

    async def update_cart_item(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        return await self._set_line(session, product_id, quantity)

    async def remove_from_cart(self, session: ShoppingSessionContext, product_id: str) -> Cart:
        return await self._set_line(session, product_id, 0)

    async def _set_line(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        state = self._state(session)
        await self._refresh_lines(state)
        variant_id = (
            product_id
            if product_id in state.lines
            else state.default_variant.get(product_id, product_id)
        )
        if state.cart_id is None or variant_id not in state.lines:
            return await self.get_cart(session)
        try:
            payload = await self.client.call_ucp(
                "update_cart",
                {
                    "id": state.cart_id,
                    "cart": {"line_items": self._line_items(state, variant_id, quantity)},
                },
            )
        except UcpCartGoneError:
            # Nothing left to change: the cart is gone, so the line is too.
            self._drop_cart(state)
            return Cart(currency=state.currency)
        return self._map_cart_after_write(state, payload)

    async def _create_cart(self, state: _SessionState, line_items: list[dict[str, Any]]) -> Cart:
        payload = await self.client.call_ucp(
            "create_cart", {"cart": {"line_items": line_items, "context": _CONTEXT}}
        )
        return self._map_cart_after_write(state, payload)

    def _map_cart_after_write(self, state: _SessionState, payload: dict[str, Any]) -> Cart:
        # The staged checkout no longer matches the cart; the next checkout_url_for
        # ask re-syncs it.
        state.checkout_handoff_url = None
        return self._map_cart(state, payload)

    async def _refresh_lines(self, state: _SessionState) -> None:
        """Re-read the cart before composing a write. ``update_cart`` replaces the whole
        line list, and the cart is not this session's alone: the storefront that shares
        it (``attach_cart``) may have changed it since the last read."""
        if state.cart_id is None:
            return
        try:
            payload = await self.client.call_ucp("get_cart", {"id": state.cart_id})
        except UcpCartGoneError:
            self._drop_cart(state)
            return
        self._map_cart(state, payload)

    def _line_items(
        self, state: _SessionState, variant_id: str, quantity: int
    ) -> list[dict[str, Any]]:
        """The cart's full desired line list with one variant's quantity set —
        ``update_cart`` replaces the contents, so unchanged lines ride along."""
        items: list[dict[str, Any]] = []
        for vid, (line_id, qty) in state.lines.items():
            items.append(
                {
                    "id": line_id,
                    "item": {"id": vid},
                    "quantity": quantity if vid == variant_id else qty,
                }
            )
        if variant_id not in state.lines:
            items.append({"item": {"id": variant_id}, "quantity": quantity})
        return items

    def _drop_cart(self, state: _SessionState) -> None:
        state.cart_id = None
        state.checkout_url = None
        state.checkout_handoff_url = None
        state.lines = {}

    async def _resolve_variant(
        self, session: ShoppingSessionContext, state: _SessionState, product_id: str
    ) -> str:
        if product_id.startswith(_VARIANT_PREFIX):
            return product_id
        # The warmed display cache can answer too (the provenance gate has already
        # passed by the time a write reaches here), sparing a live read.
        if product_id not in state.default_variant and product_id not in self.default_variants:
            await self.get_product_details(session, product_id)
        variant_id = state.default_variant.get(product_id) or self.default_variants.get(product_id)
        if variant_id is None:
            raise UcpError(f"No purchasable variant for {product_id}.")
        return variant_id

    def _map_cart(self, state: _SessionState, cart: dict[str, Any]) -> Cart:
        """The UCP cart document: ``line_items`` each carry their variant under
        ``item`` with a minor-unit unit price, and ``continue_url`` is the hosted
        checkout link."""
        state.cart_id = cart["id"]
        state.checkout_url = cart.get("continue_url") or state.checkout_url
        state.lines = {}
        items: list[CartItem] = []
        currency = cart.get("currency") or state.currency
        for line in cart.get("line_items") or []:
            item = line.get("item") or {}
            variant_id = item["id"]
            state.lines[variant_id] = (line["id"], line["quantity"])
            items.append(
                CartItem(
                    product_id=variant_id,
                    title=item.get("title") or variant_id,
                    price=round(item.get("price", 0) / 100, 2),
                    quantity=line["quantity"],
                    image_url=item.get("image_url") or self._variant_images.get(variant_id),
                )
            )
        state.currency = currency
        return Cart(items=items, currency=currency)

    # -- Checkout: staging only, never completion ------------------------------------

    async def _stage_handoff(self, state: _SessionState) -> None:
        """Stage the cart's lines as a UCP checkout, keeping its id and ``continue_url``
        on the session. The shop answers ``isError`` while the checkout awaits buyer
        input (contact, address) — that is the expected shape, and the buyer supplies
        those on Shopify's page. ``complete_checkout`` is never called: it is the
        trusted-tier order placement, and nothing in this repo charges anyone. On any
        failure the cart's own hosted checkout link stands in, so the handoff never
        goes missing."""
        line_items = [
            {"item": {"id": vid}, "quantity": qty} for vid, (_, qty) in state.lines.items()
        ]
        try:
            document = await self._stage_checkout(state, line_items)
        except UcpError:
            logger.warning("checkout staging failed; handing off the cart's own link")
            return
        state.checkout_id = document.get("id") or state.checkout_id
        state.checkout_handoff_url = document.get("continue_url") or state.checkout_handoff_url

    async def _stage_checkout(
        self, state: _SessionState, line_items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """``update_checkout`` re-syncs the staged checkout; expired, canceled, or
        completed ones (a ~6 h TTL, or the buyer finished with it) fail the update,
        and a fresh ``create_checkout`` replaces them."""
        checkout = {"line_items": line_items}
        if state.checkout_id is not None:
            try:
                return await self.client.call_ucp(
                    "update_checkout",
                    {"id": state.checkout_id, "checkout": checkout},
                    document_error_ok=True,
                )
            except UcpError:
                state.checkout_id = None
        return await self.client.call_ucp(
            "create_checkout", {"checkout": checkout}, document_error_ok=True
        )

    # -- Customer context, orders, policies, fulfillment ---------------------------

    async def get_preferences(self, session: ShoppingSessionContext) -> UserPreferences:
        return UserPreferences(user_id=session.user_id, display_name="Guest")

    async def get_orders(self, session: ShoppingSessionContext, limit: int = 5) -> list[Order]:
        """The session's agent-placed orders, newest first — and nothing while order
        tracking isn't enabled for the deployment (``orders_enabled``). Order MCP v1
        sees no others: buyer identity linking isn't supported, so cross-channel store
        history is out by design (README's appendix points at the alternative)."""
        state = self._state(session)
        await self._sync_ledger(state)
        order_ids = list(state.order_of_checkout.values())[-limit:]
        orders = []
        for order_id in reversed(order_ids):
            order = await self._fetch_order(session, state, order_id)
            if order is not None:
                orders.append(order)
        return orders

    async def get_order(self, session: ShoppingSessionContext, order_id: str) -> Order | None:
        state = self._state(session)
        await self._sync_ledger(state)
        if order_id not in state.order_of_checkout.values():
            return None  # unknown, or not this session's — never look up others' orders
        return await self._fetch_order(session, state, order_id)

    async def _sync_ledger(self, state: _SessionState) -> None:
        """Map the staged checkout to its order once the buyer completes: a completed
        checkout's document names the order. Runs only inside a buyer's ask — the
        Order MCP docs rule out scheduled polling."""
        if state.checkout_id is None or state.checkout_id in state.order_of_checkout:
            return
        try:
            document = await self.client.call_ucp(
                "get_checkout", {"id": state.checkout_id}, document_error_ok=True
            )
        except UcpError:
            return  # expired or gone; nothing to map
        order_id = (document.get("order") or {}).get("id")
        if order_id:
            state.order_of_checkout[state.checkout_id] = order_id
            state.order_seen_at.setdefault(order_id, datetime.now(UTC))

    async def _fetch_order(
        self, session: ShoppingSessionContext, state: _SessionState, order_id: str
    ) -> Order | None:
        if not self.orders_enabled:
            return None
        bearer = await self.agent_token.bearer() if self.agent_token is not None else None
        buyer_ip = self.identity.buyer_ip(session.session_id) if self.identity else None
        if bearer is None or buyer_ip is None:
            return None  # no agent token, or no buyer IP to pair it with
        try:
            document = await self.client.call_ucp(
                "get_order", {"id": order_id}, bearer_token=bearer, buyer_ip=buyer_ip
            )
        except UcpError as error:
            if "orders_not_allowed" in error.codes:
                # The key's read_global_api_orders grant is pending; remember and
                # stop asking.
                self.orders_enabled = False
                return None
            if error.codes & _ORDER_ERROR_CODES:
                return None  # order_not_found is transient right after checkout
            raise
        return self._map_order(state, document)

    def _map_order(self, state: _SessionState, document: dict[str, Any]) -> Order:
        """The docs' order envelope: minor-unit signed ``totals`` rows, ``line_items``
        with quantity splits, ``fulfillment.events`` (typed, with tracking), and
        ``adjustments``. The envelope has no placed timestamp, so the earliest event
        stands in, else the moment the ledger first saw the order."""
        order_id = document["id"]
        timeline: list[tuple[str, OrderStatus | None]] = []
        events = (document.get("fulfillment") or {}).get("events") or []
        for event in events:
            timeline.append((event.get("occurred_at") or "", _EVENT_STATUS.get(event.get("type"))))
        for adjustment in document.get("adjustments") or []:
            status = _ADJUSTMENT_STATUS.get(adjustment.get("type"))
            timeline.append((adjustment.get("occurred_at") or "", status))
        timeline.sort(key=lambda entry: entry[0])
        statuses = [status for _, status in timeline if status is not None]
        items = []
        for line in document.get("line_items") or []:
            if line.get("status") == "removed":
                continue
            item = line.get("item") or {}
            quantity = line.get("quantity") or {}
            items.append(
                OrderItem(
                    product_id=item.get("id", ""),
                    title=item.get("title") or item.get("id", "item"),
                    quantity=quantity.get("total") or quantity.get("original") or 1,
                    price=round((item.get("price") or 0) / 100, 2),
                )
            )
        total = 0.0
        for row in document.get("totals") or []:
            if row.get("type") == "total":
                total = round(row.get("amount", 0) / 100, 2)
        tracked = [e for e in events if e.get("tracking_url")]
        placed_at = state.order_seen_at.get(order_id, datetime.now(UTC))
        if timeline and timeline[0][0]:
            placed_at = min(placed_at, datetime.fromisoformat(timeline[0][0]))
        return Order(
            order_id=order_id,
            status=statuses[-1] if statuses else OrderStatus.PROCESSING,
            placed_at=placed_at,
            items=items,
            total=total,
            currency=document.get("currency") or state.currency,
            tracking_url=tracked[-1]["tracking_url"] if tracked else None,
        )

    async def search_policies(self, session: ShoppingSessionContext, query: str) -> list[Policy]:
        payload = await self.client.call_storefront(
            "search_shop_policies_and_faqs", {"query": query}
        )
        results = payload.get("results") or payload.get("policies") or []
        policies: list[Policy] = []
        for index, item in enumerate(results):
            if not isinstance(item, dict):
                continue
            content = _strip_html(item.get("body")) or item.get("answer") or item.get("text") or ""
            policies.append(
                Policy(
                    policy_id=str(item.get("id") or item.get("handle") or f"policy-{index}"),
                    title=str(item.get("title") or item.get("question") or query),
                    content=str(content),
                )
            )
        return policies

    async def get_fulfillment_options(
        self, session: ShoppingSessionContext, product_ids: list[str]
    ) -> list[FulfillmentOption]:
        # No fulfillment surface here; the agent tells the customer shipping is
        # settled on Shopify's own checkout page.
        return []
