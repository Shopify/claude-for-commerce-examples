# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""The eight reads, over the fixture store.

What these assert is the mapping, not the Admin API: that a Shopify product becomes a
``Listing`` an operator would recognise, that the states with no clean equivalent land
where ``shopify_backend``'s docstring says they land, and that a figure the store
does not record is absent rather than estimated.
"""

from __future__ import annotations

import pytest

from merchant_agent import ListingFilters
from merchant_agent.changes import ChangeNotApplicable
from merchant.api.shopify_backend import ShopifyMerchantBackend

from .fake_admin import FakeAdmin

APRON = "gid://shopify/Product/1"
STOOL = "gid://shopify/Product/2"
BENCH_DOGS = "gid://shopify/Product/3"
CUSHION = "gid://shopify/Product/4"
SQUARE = "gid://shopify/Product/5"
BROOM = "gid://shopify/Product/6"
ARCHIVED = "gid://shopify/Product/8"


async def test_profile_reads_the_shop_once(
    backend: ShopifyMerchantBackend, admin: FakeAdmin
) -> None:
    assert (await backend.profile()).name == "ACME Supply Co."
    assert await backend.currency() == "USD"
    assert (await backend.profile()).timezone == "America/Toronto"
    assert admin.reads.count("ShopProfile") == 1


async def test_snapshot_carries_the_period_the_alerts_and_the_currency(
    backend: ShopifyMerchantBackend, session
) -> None:
    snapshot = await backend.get_business_snapshot(session)

    assert snapshot.currency == "USD"
    assert snapshot.period != snapshot.compare_to
    assert snapshot.sales > 0
    assert snapshot.orders > 0
    assert snapshot.average_order_value == round(snapshot.sales / snapshot.orders, 2)
    # One low-stock listing and three slow movers, from data/thresholds.json.
    assert snapshot.alerts.low_stock == 1
    assert snapshot.alerts.slow_movers == 3
    assert snapshot.alerts.order_issues == 2
    assert snapshot.alerts.pending_changes == 0


async def test_snapshot_reports_no_traffic_rather_than_a_derived_number(
    backend: ShopifyMerchantBackend, session
) -> None:
    """The store exposes no session data to this token, so traffic and conversion are zero
    and the merchant context says why. Deriving a plausible figure would put a number in
    front of the operator that nothing measured."""
    snapshot = await backend.get_business_snapshot(session)

    assert snapshot.traffic == 0
    assert snapshot.conversion_rate == 0.0
    context = await backend.get_merchant_context(session)
    assert context is not None
    assert "traffic and conversion read 0" in context["data_source"]


async def test_search_matches_on_title_and_respects_filters(
    backend: ShopifyMerchantBackend, session
) -> None:
    matches = await backend.search_listings(session, "apron")
    assert [listing.listing_id for listing in matches] == [APRON]

    stools = await backend.search_listings(session, "stool")
    assert {listing.listing_id for listing in stools} == {STOOL, CUSHION}

    paused = await backend.search_listings(session, "stool", ListingFilters(status="paused"))
    assert [listing.listing_id for listing in paused] == [CUSHION]


async def test_search_falls_back_to_the_cache_when_the_admin_search_finds_nothing(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    """A phrase Shopify's search syntax cannot express must not read as an empty catalog.
    The cache is scanned with the same scorer instead."""
    admin.refuse.add("ProductSearch")

    matches = await backend.search_listings(session, "canvas apron")

    assert [listing.listing_id for listing in matches] == [APRON]


async def test_search_accepts_a_pasted_listing_id(backend: ShopifyMerchantBackend, session) -> None:
    matches = await backend.search_listings(session, BENCH_DOGS)
    assert [listing.listing_id for listing in matches] == [BENCH_DOGS]


async def test_a_browse_query_returns_the_catalog_without_the_archived_product(
    backend: ShopifyMerchantBackend, session
) -> None:
    listings = await backend.search_listings(session, "show me everything", limit=25)

    ids = [listing.listing_id for listing in listings]
    assert len(ids) == 7
    assert ARCHIVED not in ids


async def test_a_draft_product_reads_as_paused(backend: ShopifyMerchantBackend, session) -> None:
    details = await backend.get_listing(session, CUSHION)
    assert details is not None
    assert details.status == "paused"


async def test_a_variant_ladder_reports_the_first_price_and_the_summed_stock(
    backend: ShopifyMerchantBackend, session
) -> None:
    details = await backend.get_listing(session, STOOL)
    assert details is not None
    assert details.price == 60.0
    assert details.stock == 23
    # The interface has no variant dimension, so the spread is carried as an attribute
    # rather than lost.
    assert details.attributes["variants"] == "2"
    assert details.attributes["price_range"] == "60–90"


async def test_listing_details_carry_sales_and_a_return_rate(
    backend: ShopifyMerchantBackend, session
) -> None:
    details = await backend.get_listing(session, BENCH_DOGS)
    assert details is not None
    assert details.sales_last_30d == 9
    # Five of the nine units sold sit on a refunded order.
    assert details.return_rate_pct == 55.6


async def test_content_gaps_are_named(backend: ShopifyMerchantBackend, session) -> None:
    thin = await backend.get_listing(session, BENCH_DOGS)
    assert thin is not None
    assert set(thin.missing_attributes) == {"long_description", "images", "seo_description"}
    assert thin.content_quality == "poor"

    good = await backend.get_listing(session, APRON)
    assert good is not None
    assert good.missing_attributes == []
    assert good.content_quality == "good"


async def test_an_unknown_listing_id_reads_as_absent(
    backend: ShopifyMerchantBackend, session
) -> None:
    assert await backend.get_listing(session, "gid://shopify/Product/999") is None
    assert await backend.get_listing(session, "no-such-handle") is None


async def test_an_archived_product_is_not_readable_by_id(
    backend: ShopifyMerchantBackend, session
) -> None:
    """It is absent from the catalog, so it must also be absent by id: otherwise the agent
    could quote a listing the operator cannot act on."""
    assert await backend.get_listing(session, ARCHIVED) is None


async def test_inventory_alerts_apply_the_configured_thresholds(
    backend: ShopifyMerchantBackend, session
) -> None:
    alerts = await backend.get_inventory_alerts(session)

    low = [alert for alert in alerts if alert.kind == "low_stock"]
    assert [alert.listing_id for alert in low] == [BENCH_DOGS]
    # data/thresholds.json raises the Workshop tools threshold above the store default.
    assert low[0].threshold == 12
    assert low[0].stock == 2

    slow = [alert for alert in alerts if alert.kind == "slow_mover"]
    assert [alert.listing_id for alert in slow] == [SQUARE, CUSHION, "gid://shopify/Product/7"]
    # Low stock first, then the shallowest stock: the order the portal shows.
    assert alerts[0].kind == "low_stock"


async def test_an_untracked_product_raises_no_stock_alert(
    backend: ShopifyMerchantBackend, session
) -> None:
    alerts = await backend.get_inventory_alerts(session)
    assert BROOM not in {alert.listing_id for alert in alerts}


async def test_a_paused_listing_is_marked_as_not_on_the_storefront(
    backend: ShopifyMerchantBackend, session
) -> None:
    alerts = await backend.get_inventory_alerts(session)
    cushion = next(alert for alert in alerts if alert.listing_id == CUSHION)
    assert cushion.storefront_visible is False


async def test_order_issues_cover_the_two_kinds_the_admin_api_supports(
    backend: ShopifyMerchantBackend, session
) -> None:
    issues = await backend.get_order_issues(session)

    kinds = {issue.kind for issue in issues}
    assert kinds == {"delayed", "return_spike"}
    delayed = next(issue for issue in issues if issue.kind == "delayed")
    assert delayed.order_id == "#1014"
    assert "past the 3-day fulfilment window" in delayed.summary
    spike = next(issue for issue in issues if issue.kind == "return_spike")
    assert spike.listing_id == BENCH_DOGS


async def test_a_recent_unfulfilled_order_is_not_yet_an_issue(
    backend: ShopifyMerchantBackend, session
) -> None:
    issues = await backend.get_order_issues(session)
    assert "#1015" not in {issue.order_id for issue in issues}


async def test_pricing_context_echoes_the_caps_the_deployment_enforces(
    backend: ShopifyMerchantBackend, session, config
) -> None:
    """The model plans against these numbers, so they have to be the same ones the
    guardrails will check the staged change against."""
    context = await backend.get_pricing_context(session, APRON)

    assert context is not None
    assert context.current_price == 40.0
    assert context.max_price_delta_pct == config.max_price_delta_pct
    assert context.max_promotion_discount_pct == config.max_promotion_discount_pct
    assert context.max_price == round(40.0 * (1 + config.max_price_delta_pct / 100), 2)


async def test_the_price_floor_is_raised_to_unit_cost(
    backend: ShopifyMerchantBackend, session
) -> None:
    """A move the guardrails would allow can still be a move below cost, so the floor is
    the higher of the two."""
    context = await backend.get_pricing_context(session, BENCH_DOGS)

    assert context is not None
    assert context.unit_cost == 7.5
    # 20% off 18.00 is 14.40, which is above cost, so the cap is the binding floor here.
    assert context.min_price == 14.4
    assert context.margin_pct is not None


async def test_margin_is_absent_when_the_store_records_no_cost(
    backend: ShopifyMerchantBackend, session
) -> None:
    context = await backend.get_pricing_context(session, SQUARE)

    assert context is not None
    assert context.unit_cost is None
    assert context.margin_pct is None
    assert context.min_price == round(34.0 * 0.8, 2)


async def test_pricing_context_is_absent_for_an_unknown_listing(
    backend: ShopifyMerchantBackend, session
) -> None:
    assert await backend.get_pricing_context(session, "gid://shopify/Product/999") is None


async def test_campaigns_map_from_marketing_activities_and_declare_what_is_missing(
    backend: ShopifyMerchantBackend, session
) -> None:
    campaigns = await backend.get_campaign_performance(session)

    assert len(campaigns) == 1
    campaign = campaigns[0]
    assert campaign.name == "Workshop season email"
    assert campaign.status == "active"
    assert campaign.channel == "email"
    assert campaign.budget == 800.0
    # Marketing activities expose no spend or revenue, so these are zero and the merchant
    # context says so rather than the figures implying a campaign earned nothing.
    assert campaign.spend == 0.0
    assert campaign.revenue == 0.0


async def test_no_campaign_data_and_no_campaigns_are_reported_differently(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    """Both are refusals, but they say different things: one store cannot be read, the
    other has nothing to read. They lead to different advice."""
    admin.refuse.add("MarketingActivities")
    with pytest.raises(ChangeNotApplicable, match="cannot read marketing activities"):
        await backend.get_campaign_performance(session)

    admin.refuse.clear()
    admin.marketing_activities = []
    with pytest.raises(ChangeNotApplicable, match="no marketing activities"):
        await backend.get_campaign_performance(session)


async def test_merchant_context_names_the_store_and_the_history_it_can_see(
    backend: ShopifyMerchantBackend, session
) -> None:
    context = await backend.get_merchant_context(session)

    assert context is not None
    assert context["store"] == "ACME Supply Co."
    assert context["shop_domain"] == "acme-supply.myshopify.com"
    assert context["catalog_size"] == 7
    assert context["order_history_days"] == 60
    assert context["pending_changes"] == 0
    assert "spend and revenue" in context["campaigns"]


async def test_analysis_stays_off_for_this_deployment(
    backend: ShopifyMerchantBackend, session, config
) -> None:
    """The example exposes no SQL surface, so the optional analysis reads stay at their
    default and the config leaves the tool out rather than offering one that always
    fails."""
    assert config.enable_analysis is False
    assert await backend.get_analysis_schema(session) is None
    assert await backend.execute_analysis_query(session, "SELECT 1") is None
