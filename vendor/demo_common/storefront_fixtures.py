# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Helpers the mock storefronts share: fixture loading, keyword search, help-content
search, and order re-dating. Each mock keeps its own field weights, synonyms, and
filters; what is here is the part that must rank the same way in every vertical."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from shopping_agent import (
    Cart,
    CartItem,
    Order,
    Policy,
    Product,
    ProductDetails,
    SearchFilters,
    UserPreferences,
)

_WORD = re.compile(r"[a-z0-9]+")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_IN_FLIGHT_STATUSES = {"processing", "shipped", "delayed"}

# Function words carry no topic, and without dropping them a long help entry outscores a
# short one on any question phrased as a sentence. Catalog search keeps plain overlap.
_HELP_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "get",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "so",
        "that",
        "the",
        "there",
        "this",
        "to",
        "up",
        "we",
        "what",
        "when",
        "which",
        "will",
        "with",
        "you",
        "your",
    }
)

# What the catalog listing and search results omit; the detail record carries them.
SUMMARY_EXCLUDES = frozenset({"long_description", "specs", "review_highlights", "variants"})


def example_data_dir(api_module_file: str) -> Path:
    """An example's ``data/`` directory, given the ``__file__`` of a module in its ``api/``."""
    return Path(api_module_file).resolve().parent.parent / "data"


def load_json(data_dir: Path, filename: str) -> dict[str, Any]:
    return json.loads((data_dir / filename).read_text(encoding="utf-8"))


def load_users(data_dir: Path) -> dict[str, UserPreferences]:
    users = load_json(data_dir, "users.json")["users"]
    return {user["user_id"]: UserPreferences.model_validate(user) for user in users}


def preferences_of(users: Mapping[str, UserPreferences], user_id: str) -> UserPreferences:
    """The fixture profile, or a guest profile for an id the fixtures do not carry."""
    return users.get(user_id) or UserPreferences(user_id=user_id, display_name="Guest")


def load_orders(data_dir: Path) -> list[tuple[str, Order]]:
    """``(user_id, order)`` pairs, with in-flight orders re-dated relative to boot."""
    raw = redate_in_flight_orders(load_json(data_dir, "orders.json"))
    return [
        (entry["user_id"], Order.model_validate({k: v for k, v in entry.items() if k != "user_id"}))
        for entry in raw["orders"]
    ]


def load_policies(data_dir: Path) -> list[Policy]:
    return [
        Policy.model_validate(entry) for entry in load_json(data_dir, "policies.json")["policies"]
    ]


def weeks_since(authored_today: date, today: date | None = None) -> timedelta:
    """The whole weeks from the day a fixture was written for to today. Fixture dates
    move forward by this, so their spacing and weekdays survive and every file authored
    for the same day (metrics, campaigns, messages) lands in the same week."""
    today = today or datetime.now(UTC).date()
    return timedelta(weeks=max(0, (today - authored_today).days // 7))


def anchored_shift(raw: Mapping[str, Any], today: date | None = None) -> timedelta:
    """The shift for a fixture carrying ``dates_anchored_to``, the day it was written
    for; zero when it carries none. ``today`` is for a backend that runs on its own
    clock; the default is the real date."""
    anchor = raw.get("dates_anchored_to")
    return weeks_since(date.fromisoformat(anchor), today) if anchor else timedelta(0)


def redate_in_flight_orders(raw: dict[str, Any]) -> dict[str, Any]:
    """Shift every in-flight order's dates by (today - ``dates_anchored_to``), including
    the dates inside its free-text delivery estimate, so estimates stay current however
    long after authoring the demo boots. Finished orders keep their dates."""
    anchor = raw.get("dates_anchored_to")
    if not anchor:
        return raw
    delta = timedelta(days=(datetime.now().date() - date.fromisoformat(anchor)).days)
    for order in raw.get("orders", []):
        if order.get("status") not in _IN_FLIGHT_STATUSES:
            continue
        placed = datetime.fromisoformat(order["placed_at"].replace("Z", "+00:00")) + delta
        order["placed_at"] = placed.isoformat().replace("+00:00", "Z")
        if estimate := order.get("estimated_delivery"):
            order["estimated_delivery"] = _ISO_DATE.sub(
                lambda match: (date.fromisoformat(match.group(0)) + delta).isoformat(), estimate
            )
    return raw


def tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def stem(token: str) -> str:
    return token[:-1] if len(token) > 3 and token.endswith("s") else token


def keyword_score(
    fields: Mapping[str, str],
    weights: Mapping[str, float],
    query_tokens: Iterable[str],
    synonyms: Mapping[str, list[str]],
) -> float:
    """Each query token scores the weight of the best field it (or a synonym) appears in."""
    stemmed_fields = {name: {stem(t) for t in tokens(text)} for name, text in fields.items()}
    score = 0.0
    for token in query_tokens:
        stemmed = stem(token)
        candidates = [stemmed, *(stem(s) for s in synonyms.get(stemmed, []))]
        score += max(
            (
                weight
                for name, weight in weights.items()
                if any(c in stemmed_fields[name] for c in candidates)
            ),
            default=0.0,
        )
    return score


def within_price_and_rating(product: ProductDetails, filters: SearchFilters) -> bool:
    if filters.min_price is not None and product.price < filters.min_price:
        return False
    if filters.max_price is not None and product.price > filters.max_price:
        return False
    return filters.min_rating is None or (product.rating or 0) >= filters.min_rating


def matches_attribute_filters(
    product: ProductDetails, filters: SearchFilters, *, ignore: frozenset[str] = frozenset()
) -> bool:
    """Category and attribute filters as the agent's taxonomy guesses: a named attribute
    must contain the wanted value; an attribute the product lacks is looked for across
    its attributes and title. ``ignore`` names filter keys handled elsewhere."""
    if filters.category and filters.category.lower() not in (product.category or "").lower():
        return False
    haystack = (
        " ".join(f"{k}={v}".lower() for k, v in product.attributes.items())
        + " "
        + product.title.lower()
    )
    for key, value in filters.attributes.items():
        if key in ignore:
            continue
        actual = product.attributes.get(key)
        if str(value).lower() not in (actual.lower() if actual is not None else haystack):
            return False
    return True


def rank_products(
    products: Iterable[ProductDetails],
    query: str,
    filters: SearchFilters | None,
    limit: int,
    *,
    score: Callable[[ProductDetails, list[str]], float],
    hard_filter: Callable[[ProductDetails, SearchFilters], bool],
    soft_filter: Callable[[ProductDetails, SearchFilters], bool],
    relevance_tiebreak: Callable[[ProductDetails], Any] = lambda product: -(product.rating or 0),
) -> list[ProductDetails]:
    """The ranking every mock uses: hard filters, then a relevance cutoff at half the best
    score, then the soft filters (dropped again if they would empty the result), then the
    requested sort."""
    query_tokens = tokens(query)
    if not query_tokens:
        return []
    scored = [
        (score(product, query_tokens), product)
        for product in products
        if filters is None or hard_filter(product, filters)
    ]
    scored = [(points, product) for points, product in scored if points > 0]
    if scored:
        best = max(points for points, _ in scored)
        scored = [(points, product) for points, product in scored if points >= best / 2]
    if filters is not None:
        narrowed = [
            (points, product) for points, product in scored if soft_filter(product, filters)
        ]
        scored = narrowed or scored
    sort = filters.sort if filters else "relevance"
    if sort == "price_asc":
        scored.sort(key=lambda pair: pair[1].price)
    elif sort == "price_desc":
        scored.sort(key=lambda pair: -pair[1].price)
    elif sort == "rating":
        scored.sort(key=lambda pair: -(pair[1].rating or 0))
    else:
        scored.sort(key=lambda pair: (-pair[0], relevance_tiebreak(pair[1])))
    return [product for _, product in scored[:limit]]


def summary_of(product: ProductDetails) -> Product:
    return Product.model_validate(product.model_dump(exclude=set(SUMMARY_EXCLUDES)))


def find_by_id(items: Mapping[str, Any], wanted: str) -> str | None:
    """The key matching ``wanted`` exactly or case-insensitively; ids are typed by hand."""
    if wanted in items:
        return wanted
    lowered = wanted.lower()
    return next((key for key in items if key.lower() == lowered), None)


def _help_terms(text: str) -> set[str]:
    return {stem(t) for t in tokens(text) if t not in _HELP_STOPWORDS}


def search_help(policies: Iterable[Policy], query: str, limit: int = 3) -> list[Policy]:
    """Help entries ranked by term overlap; a hit in the title or category counts double."""
    query_terms = _help_terms(query)
    if not query_terms:
        return []
    scored = []
    for policy in policies:
        heading_hits = len(query_terms & _help_terms(f"{policy.title} {policy.category}"))
        body_hits = len(query_terms & _help_terms(policy.content))
        if points := 2 * heading_hits + body_hits:
            scored.append((points, policy))
    scored.sort(key=lambda pair: -pair[0])
    return [policy for _, policy in scored[:limit]]


def orders_for(orders: Iterable[tuple[str, Order]], user_id: str, limit: int) -> list[Order]:
    mine = [order for owner, order in orders if owner == user_id]
    mine.sort(key=lambda order: order.placed_at, reverse=True)
    return mine[:limit]


def newest_orders(orders: Iterable[tuple[str, Order]], limit: int) -> list[Order]:
    """Across every user: the merchant overview's order feed."""
    return sorted((order for _, order in orders), key=lambda o: o.placed_at, reverse=True)[:limit]


def find_order(orders: Iterable[tuple[str, Order]], user_id: str, order_id: str) -> Order | None:
    wanted = order_id.lower()
    return next(
        (order for owner, order in orders if owner == user_id and order.order_id.lower() == wanted),
        None,
    )


def cart_line(product: ProductDetails, quantity: int) -> CartItem:
    return CartItem(
        product_id=product.product_id,
        title=product.title,
        price=product.price,
        quantity=quantity,
        image_url=product.image_url,
    )


class SessionCarts:
    """Per-session cart lines for a mock whose cart is plain state (a ticketing cart is a
    view of live holds instead). Each mock applies its own quantity rules, then ``put``s
    the resulting line."""

    def __init__(self) -> None:
        self._lines: dict[str, dict[str, CartItem]] = {}

    def lines(self, session_id: str) -> dict[str, CartItem]:
        return self._lines.setdefault(session_id, {})

    def cart(self, session_id: str) -> Cart:
        return Cart(items=list(self.lines(session_id).values()))

    def put(self, session_id: str, product: ProductDetails, quantity: int) -> Cart:
        self.lines(session_id)[product.product_id] = cart_line(product, quantity)
        return self.cart(session_id)

    def set_quantity(self, session_id: str, product_id: str, quantity: int) -> Cart:
        lines = self.lines(session_id)
        if product_id in lines:
            lines[product_id] = lines[product_id].model_copy(update={"quantity": quantity})
        return self.cart(session_id)

    def remove(self, session_id: str, product_id: str) -> Cart:
        self.lines(session_id).pop(product_id, None)
        return self.cart(session_id)

    def reset(self, session_id: str) -> None:
        self._lines.pop(session_id, None)
