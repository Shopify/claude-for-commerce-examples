# Copyright 2026 Shopify Inc.
# SPDX-License-Identifier: Apache-2.0

"""Periods, and the two ways a metrics question is answered.

The figures the fixture puts on the ShopifyQL path differ from the ones its orders add up
to, deliberately: that is how each test here proves which source served a read rather than
merely that a number came back.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from merchant.api.metrics import resolve_period
from merchant.api.shopify_backend import ShopifyMerchantBackend

from .fake_admin import FakeAdmin

# What the fixture store's orders add up to over the default seven-day window and the one
# before it.
DERIVED_SALES = 562.0
DERIVED_ORDERS = 8
DERIVED_PRIOR_SALES = 317.0

# What the fixture answers on the ShopifyQL path: near enough to be plausible, far enough
# that no test can pass on the wrong source.
QL_SALES = [["4210.55", "31", "135.83"]]
QL_COLUMNS = ("total_sales", "orders", "average_order_value")
SALES_TABLE = {"FROM sales": (QL_COLUMNS, QL_SALES)}
SESSIONS_TABLE = {"FROM sessions": (("total_sessions",), [["1240"]])}


def test_a_period_ends_yesterday() -> None:
    """Today is still accumulating, and a partial day compared against whole ones reads as
    a collapse in sales."""
    today = date(2026, 6, 15)
    period = resolve_period("last_7_days", today=today)

    assert period.end == date(2026, 6, 14)
    assert period.start == date(2026, 6, 8)
    assert period.days == 7


def test_the_prior_period_is_the_same_length_and_ends_the_day_before() -> None:
    period = resolve_period("last_30_days", today=date(2026, 6, 15))
    prior = period.previous()

    assert prior.days == period.days
    assert prior.end == period.start - timedelta(days=1)


def test_an_explicit_iso_range_is_honoured() -> None:
    period = resolve_period("2026-03-01/2026-03-31", today=date(2026, 6, 15))

    assert (period.start, period.end) == (date(2026, 3, 1), date(2026, 3, 31))
    assert period.label == "2026-03-01/2026-03-31"


def test_an_unknown_period_falls_back_to_the_default_window() -> None:
    period = resolve_period("since the flood", default_days=14, today=date(2026, 6, 15))

    assert period.days == 14
    assert period.end == date(2026, 6, 14)


async def test_the_snapshot_comes_from_the_order_scan_when_shopifyql_is_unavailable(
    backend: ShopifyMerchantBackend, session
) -> None:
    snapshot = await backend.get_business_snapshot(session)

    assert snapshot.sales == DERIVED_SALES
    assert snapshot.orders == DERIVED_ORDERS
    assert snapshot.average_order_value == round(DERIVED_SALES / DERIVED_ORDERS, 2)
    assert snapshot.sales_change_pct is not None and snapshot.sales_change_pct > 0

    metrics = await backend.metrics()
    assert "derived from the trailing order scan" in metrics.source_note


async def test_the_snapshot_prefers_shopifyql_when_the_store_can_answer_it(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    admin.shopifyql = dict(SALES_TABLE)

    snapshot = await backend.get_business_snapshot(session)

    assert snapshot.sales == 4210.55
    assert snapshot.orders == 31
    metrics = await backend.metrics()
    assert metrics.source_note.startswith("metrics from ShopifyQL")


async def test_sessions_are_separately_gated(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    """A token can read the sales report and not the sessions one, so traffic has to be
    absent on its own rather than with the rest of the figures."""
    admin.shopifyql = dict(SALES_TABLE)
    snapshot = await backend.get_business_snapshot(session)
    assert snapshot.traffic == 0
    assert "sessions unavailable to this token" in (await backend.metrics()).source_note

    granted = ShopifyMerchantBackend(admin, backend._settings, backend._config)  # noqa: SLF001
    admin.shopifyql = {**SALES_TABLE, **SESSIONS_TABLE}
    with_traffic = await granted.get_business_snapshot(session)

    assert with_traffic.traffic == 1240
    assert with_traffic.conversion_rate == round(31 / 1240 * 100, 2)
    assert "sessions from the same source" in (await granted.metrics()).source_note


async def test_a_refused_shopifyql_is_not_asked_again(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    """The first refusal is remembered. Retrying a permission the store does not grant
    would cost a request on every turn and answer nothing."""
    admin.refuse.add("MetricsQuery")

    await backend.get_business_snapshot(session)
    first = len(admin.shopifyql_queries)
    await backend.query_metrics(session, "sales")

    assert first == 1
    assert len(admin.shopifyql_queries) == 1


async def test_a_rejected_query_does_not_disable_the_source(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    """Parse errors are about one query, not about the store's access, so the next question
    still tries. The fixture answers the sales report and rejects the sessions one."""
    admin.shopifyql = dict(SALES_TABLE)

    await backend.get_business_snapshot(session)
    series = await backend.query_metrics(session, "sales", granularity="day")

    assert any("FROM sessions" in query for query in admin.shopifyql_queries)
    assert any("GROUP BY day" in query for query in admin.shopifyql_queries)
    assert series.metric == "sales"


async def test_configuration_can_keep_shopifyql_out_of_the_loop_entirely(
    no_shopifyql: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    admin.shopifyql = dict(SALES_TABLE)

    snapshot = await no_shopifyql.get_business_snapshot(session)

    assert admin.shopifyql_queries == []
    assert snapshot.sales == DERIVED_SALES
    assert "disabled by configuration" in (await no_shopifyql.metrics()).source_note


async def test_a_series_from_the_order_scan_covers_every_day_in_the_period(
    backend: ShopifyMerchantBackend, session
) -> None:
    series = await backend.query_metrics(session, "sales", period="last_30_days")

    assert series.granularity == "day"
    assert series.unit == "USD"
    assert len(series.points) == 30
    assert sum(point.value for point in series.points) == pytest.approx(
        DERIVED_SALES + DERIVED_PRIOR_SALES + 0.0, abs=0.01
    )


async def test_a_weekly_series_buckets_the_days(backend: ShopifyMerchantBackend, session) -> None:
    series = await backend.query_metrics(
        session, "orders", period="last_14_days", granularity="week"
    )

    assert series.granularity == "week"
    assert len(series.points) == 2
    assert series.unit is None
    assert sum(point.value for point in series.points) == DERIVED_ORDERS + 7


async def test_a_series_prefers_shopifyql_when_it_answers(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    admin.shopifyql = {
        "GROUP BY day": (
            ("day", "total_sales"),
            [["2026-06-01", "101.00"], ["2026-06-02", "99.00"]],
        )
    }

    series = await backend.query_metrics(session, "sales", granularity="day")

    assert [point.value for point in series.points] == [101.0, 99.0]
    assert [point.date for point in series.points] == ["2026-06-01", "2026-06-02"]


async def test_a_segmented_series_is_always_order_derived(
    backend: ShopifyMerchantBackend, session, admin: FakeAdmin
) -> None:
    """ShopifyQL could group by product, but the segment an operator names is matched
    against type, vendor, and title here, so the two would not agree on what the segment
    covers. One definition, applied where the matching happens."""
    admin.shopifyql = {
        "GROUP BY day": (("day", "total_sales"), [["2026-06-01", "999.00"]]),
    }

    series = await backend.query_metrics(session, "sales", period="last_7_days", segment="storage")

    assert series.segment == "storage"
    assert 999.0 not in [point.value for point in series.points]
    # The folding step stool is the only Storage product: 60 + 90 over the window.
    assert sum(point.value for point in series.points) == pytest.approx(150.0, abs=0.01)


async def test_traffic_and_conversion_read_zero_rather_than_being_derived(
    backend: ShopifyMerchantBackend, session
) -> None:
    series = await backend.query_metrics(session, "conversion_rate", period="last_7_days")

    assert {point.value for point in series.points} == {0.0}
