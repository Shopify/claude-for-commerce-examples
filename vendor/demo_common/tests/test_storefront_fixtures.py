# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

from datetime import date, datetime, timedelta

from demo_common.storefront_fixtures import (
    SessionCarts,
    find_by_id,
    keyword_score,
    rank_products,
    redate_in_flight_orders,
    search_help,
    within_price_and_rating,
)
from shopping_agent import Policy, ProductDetails, SearchFilters


def product(
    product_id: str, title: str, price: float, rating: float, **attributes: str
) -> ProductDetails:
    return ProductDetails(
        product_id=product_id,
        title=title,
        price=price,
        currency="USD",
        rating=rating,
        category="camping",
        attributes=attributes,
    )


TENT = product("P-1", "Family camping tent", 180.0, 4.2, capacity="4 people")
LANTERN = product("P-2", "Camping lantern", 25.0, 4.8)
SOFA = product("P-3", "Sofa", 900.0, 4.9)
CATALOG = [TENT, LANTERN, SOFA]
WEIGHTS = {"title": 3.0, "attributes": 1.0}


def score(item: ProductDetails, query_tokens: list[str]) -> float:
    fields = {
        "title": item.title,
        "attributes": " ".join(f"{k} {v}" for k, v in item.attributes.items()),
    }
    return keyword_score(fields, WEIGHTS, query_tokens, {"couch": ["sofa"]})


def soft(item: ProductDetails, filters: SearchFilters) -> bool:
    return all(str(v) in item.attributes.get(k, "") for k, v in filters.attributes.items())


def rank(query: str, filters: SearchFilters | None = None) -> list[str]:
    ranked = rank_products(
        CATALOG,
        query,
        filters,
        8,
        score=score,
        hard_filter=within_price_and_rating,
        soft_filter=soft,
    )
    return [item.product_id for item in ranked]


def test_synonyms_stems_and_field_weights_drive_the_score():
    assert score(SOFA, ["couch"]) == 3.0
    assert score(TENT, ["tents", "people"]) == 4.0
    assert score(LANTERN, ["sofa"]) == 0.0


def test_ranking_cuts_faint_matches_relaxes_empty_soft_filters_and_sorts():
    assert rank("camping tent") == ["P-1", "P-2"]  # relevance, then rating breaks ties
    assert rank("camping") == ["P-2", "P-1"]  # equal scores: the better rated first
    assert rank("camping", SearchFilters(sort="price_desc")) == ["P-1", "P-2"]
    assert rank("camping", SearchFilters(max_price=100)) == ["P-2"]
    assert rank("camping", SearchFilters(attributes={"capacity": "4"})) == ["P-1"]
    assert rank("camping", SearchFilters(attributes={"capacity": "9"})) == [
        "P-2",
        "P-1",
    ]  # nothing matches, so relaxed
    assert rank("") == []


def test_help_search_ignores_function_words_and_weights_headings():
    returns = Policy(policy_id="h-1", title="Returns", category="orders", content="Thirty days.")
    guide = Policy(
        policy_id="h-2",
        title="Buying guide",
        category="guides",
        content="How do I choose a tent? What is the return on a good tent? Returns matter.",
    )
    assert search_help([guide, returns], "how do returns work") == [returns, guide]
    assert search_help([guide, returns], "the a of") == []


def test_ids_resolve_case_insensitively():
    items = {"AR-1601": object()}
    assert find_by_id(items, "AR-1601") == "AR-1601"
    assert find_by_id(items, "ar-1601") == "AR-1601"
    assert find_by_id(items, "AR-9999") is None


def test_in_flight_orders_shift_with_the_anchor_and_finished_orders_do_not():
    anchor = date.today() - timedelta(days=30)
    raw = {
        "dates_anchored_to": anchor.isoformat(),
        "orders": [
            {
                "status": "delayed",
                "placed_at": f"{(anchor - timedelta(days=3)).isoformat()}T10:00:00Z",
                "estimated_delivery": f"{(anchor + timedelta(days=4)).isoformat()} (was {anchor.isoformat()})",
            },
            {
                "status": "delivered",
                "placed_at": "2026-01-01T10:00:00Z",
                "estimated_delivery": "2026-01-04",
            },
        ],
    }
    delayed, delivered = redate_in_flight_orders(raw)["orders"]
    today = datetime.now().date()
    assert delayed["placed_at"] == f"{(today - timedelta(days=3)).isoformat()}T10:00:00Z"
    assert (
        delayed["estimated_delivery"]
        == f"{(today + timedelta(days=4)).isoformat()} (was {today.isoformat()})"
    )
    assert delivered["estimated_delivery"] == "2026-01-04"
    assert redate_in_flight_orders({"orders": []}) == {"orders": []}


def test_session_carts_keep_sessions_apart():
    carts = SessionCarts()
    carts.put("s-1", TENT, 2)
    assert carts.cart("s-2").items == []
    assert carts.set_quantity("s-1", "P-1", 3).items[0].quantity == 3
    assert carts.set_quantity("s-1", "P-9", 3).item_count == 3  # unknown lines are ignored
    assert carts.remove("s-1", "P-1").items == []
    carts.put("s-1", LANTERN, 1)
    carts.reset("s-1")
    assert carts.cart("s-1").items == []
