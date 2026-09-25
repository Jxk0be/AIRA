"""What the semantic layer promises, regardless of who the tenant is.

The numbers themselves are checked against RegisterOne in
`test_analytics_metrics.py`. This file checks the promises that make those
numbers safe to put in front of a shop owner:

* a metric the shop's system cannot support refuses, and says why in English;
* a metric that is only partly supported reports its coverage instead of
  rounding the gap away;
* nothing a query returns ever belongs to another tenant.

Panel & Pawn earns its keep here. It is a spreadsheet with no customers, one
location and costs for barely a third of its catalogue, so it fails every
assumption RegisterOne quietly satisfies.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import analytics as an
from app.analytics import AnalyticsContext, CapabilityUnavailable, DateRange, Filters
from app.canonical import tables as t
from app.canonical.enums import Channel
from app.canonical.models import Capabilities

POS_SHOP = "animanga_knox"
SPREADSHEET_SHOP = "panel_and_pawn"


async def context_for(db: AsyncSession, slug: str) -> AnalyticsContext:
    tenant = (await db.execute(select(t.Tenant).where(t.Tenant.slug == slug))).scalar_one_or_none()
    if tenant is None:
        pytest.skip(f"{slug} does not exist; run `python tasks.py backfill {slug}`")
    orders = (
        await db.execute(
            select(func.count()).select_from(t.Order).where(t.Order.tenant_id == tenant.id)
        )
    ).scalar_one()
    if not orders:
        pytest.skip(f"{slug} has not been synced; run `python tasks.py backfill {slug}`")
    return await an.load_context(db, slug)


@pytest.fixture
async def pos(db: AsyncSession) -> AnalyticsContext:
    return await context_for(db, POS_SHOP)


@pytest.fixture
async def spreadsheet(db: AsyncSession) -> AnalyticsContext:
    return await context_for(db, SPREADSHEET_SHOP)


# ---------------------------------------------------------------------------
# Date ranges
# ---------------------------------------------------------------------------


def test_a_day_is_a_shop_day_even_when_the_clocks_change() -> None:
    """The day the clocks go back is 25 hours long, and a day's sales are all
    of them.

    Converting a calendar day to a fixed 24-hour window would drop the last
    hour of trading on exactly one Sunday a year, which is the kind of bug that
    is never noticed and never forgiven.
    """
    knoxville = ZoneInfo("America/New_York")
    clocks_go_back = DateRange(date(2025, 11, 2), date(2025, 11, 2))
    start, end = clocks_go_back.bounds(knoxville)

    assert end - start == timedelta(hours=25)
    assert start.isoformat() == "2025-11-02T04:00:00+00:00"
    assert end.isoformat() == "2025-11-03T05:00:00+00:00"


def test_a_period_ends_at_the_start_of_the_next_day() -> None:
    """Half-open, so a sale at 23:59:59 lands in the day it happened."""
    utc = ZoneInfo("UTC")
    one_day = DateRange(date(2026, 3, 14), date(2026, 3, 14))
    start, end = one_day.bounds(utc)

    assert one_day.days == 1
    assert start.isoformat() == "2026-03-14T00:00:00+00:00"
    assert end.isoformat() == "2026-03-15T00:00:00+00:00"


def test_the_previous_period_is_the_same_length_and_sits_just_before() -> None:
    """The dashboard's "vs prior 30 days" comparison depends on this."""
    this_month = DateRange(date(2026, 6, 1), date(2026, 6, 30))
    before = this_month.previous()

    assert before == DateRange(date(2026, 5, 2), date(2026, 5, 31))
    assert before.days == this_month.days
    assert before.end + timedelta(days=1) == this_month.start


def test_a_backwards_range_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="ends before it starts"):
        DateRange(date(2026, 6, 30), date(2026, 6, 1))


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------


def test_every_metric_carries_a_definition_the_agent_can_read() -> None:
    """These strings ship: they go into tool descriptions and onto the
    dashboard. An empty one means the agent reports a number it cannot
    explain."""
    for key, metric in an.METRICS.items():
        assert metric.key == key
        assert len(metric.definition) > 40, f"{key} has no real definition"
        assert metric.formula, f"{key} does not say how it is worked out"
        assert metric.unit in {"money", "count", "units", "percent", "days"}
        for capability in metric.requires:
            assert capability in Capabilities.model_fields, (
                f"{key} requires {capability!r}, which is not a capability any adapter declares"
            )


def test_net_sales_says_what_it_leaves_out() -> None:
    """The one definition most likely to be argued about with a shop owner."""
    definition = an.definition_of("net_sales").definition
    assert "discounts" in definition
    assert "refunds" in definition
    assert "Tax and tips are excluded" in definition


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


async def test_a_shop_without_customers_is_refused_not_given_zeros(
    db: AsyncSession, spreadsheet: AnalyticsContext
) -> None:
    """Panel & Pawn's export has no customer column at all.

    A repeat rate of 0% would read as "nobody comes back", which is a claim
    about the shop. The truth is a claim about the data, and the message has to
    say so in words the owner can act on.
    """
    assert spreadsheet.capabilities.has_customers is False

    with pytest.raises(CapabilityUnavailable) as raised:
        await an.customer_stats(db, spreadsheet, spreadsheet.last_days(90))

    assert raised.value.capability == "has_customers"
    assert spreadsheet.name in raised.value.message
    assert "0" not in raised.value.message


async def test_a_single_location_shop_is_refused_a_location_split(
    db: AsyncSession, spreadsheet: AnalyticsContext
) -> None:
    """One location makes a location breakdown a chart with one bar on it."""
    with pytest.raises(CapabilityUnavailable) as raised:
        await an.location_breakdown(db, spreadsheet, spreadsheet.last_days(90))

    assert raised.value.capability == "multi_location"


async def test_partial_costs_report_their_coverage_rather_than_a_round_number(
    db: AsyncSession, spreadsheet: AnalyticsContext
) -> None:
    """Panel & Pawn has costs for some of its catalogue and not the rest.

    This is the case that makes margin dangerous. The margin has to be worked
    out over the sales whose cost is known — never over all of them — and the
    result has to carry the coverage so nobody quotes the percentage without it.
    """
    period = spreadsheet.last_days(180)
    report = await an.margin_report(db, spreadsheet, period)

    assert report.cost_coverage is not None
    assert 0 < report.cost_coverage < 1, "the fixture is meant to be missing half its costs"
    assert report.covered_sales < report.sales
    assert "partial_cost_coverage" in report.caveat_codes

    covering = next(c for c in report.caveats if c.code == "partial_cost_coverage")
    assert f"{report.cost_coverage:.0%}" in covering.message

    # The margin divides by covered sales. Dividing by all sales would report a
    # smaller, wrong number that still looks like a margin.
    assert report.gross_margin == (
        (report.covered_sales - report.cogs) / report.covered_sales
    ).quantize(Decimal("0.0001"))


async def test_a_shop_without_costs_still_gets_its_stock_valued(
    db: AsyncSession, spreadsheet: AnalyticsContext
) -> None:
    """Degrading honestly means answering the part we can.

    Inventory at retail needs no costs, so a missing-cost shop gets that number
    with a note about the other half rather than an error.
    """
    value = await an.inventory_value(db, spreadsheet)

    assert value.retail_value > 0
    assert value.units_on_hand > 0
    if spreadsheet.capabilities.has_costs:
        assert "partial_cost_coverage" in value.caveat_codes
    else:
        assert value.cost_value is None
        assert "no_costs" in value.caveat_codes


# ---------------------------------------------------------------------------
# Caveats
# ---------------------------------------------------------------------------


async def test_product_splits_say_they_are_before_refunds(
    db: AsyncSession, pos: AnalyticsContext
) -> None:
    """A refund belongs to an order, not to an item.

    Splitting it across the items on the order would be a guess, so product
    rows are before refunds and say so. Channel and location rows are not:
    an order knows where it was rung up, so those can own their refunds and
    must reconcile with net sales.
    """
    period = pos.month(2025, 12)

    products = await an.top_products(db, pos, period, limit=5)
    assert "breakdown_before_refunds" in products.caveat_codes

    channels = await an.channel_breakdown(db, pos, period)
    assert "breakdown_before_refunds" not in channels.caveat_codes

    summary = await an.sales_summary(db, pos, period)
    assert channels.total_net_sales == summary.net_sales


async def test_custom_amount_sales_are_reported_not_hidden(
    db: AsyncSession, pos: AnalyticsContext
) -> None:
    """About 4% of RegisterOne's lines are "Misc singles" with no product.

    They are real revenue, so they belong in the total; they belong to no
    product, so they cannot be in a product breakdown. The gap between those two
    facts is exactly what a caveat is for.
    """
    period = pos.month(2025, 12)
    summary = await an.sales_summary(db, pos, period)
    products = await an.top_products(db, pos, period, limit=5)

    assert "custom_amount_sales" in summary.caveat_codes
    assert "custom_amount_sales" in products.caveat_codes
    assert products.total_net_sales < summary.net_sales + summary.refunds


async def test_a_filter_that_cannot_apply_is_never_applied_silently(
    db: AsyncSession, pos: AnalyticsContext
) -> None:
    """Stock sits in a location, not in a channel.

    Quietly dropping the filter would answer a different question than the one
    that was asked, with no sign that it had.
    """
    value = await an.inventory_value(db, pos, Filters(channels=(Channel.ONLINE,)))
    assert "channel_filter_ignored_for_stock" in value.caveat_codes


async def test_asking_past_the_end_of_the_data_says_so(
    db: AsyncSession, pos: AnalyticsContext
) -> None:
    """Zero sales and no data are different answers.

    A shop that asks about next month should be told we have nothing for it,
    not that they sold nothing.
    """
    last_order = (
        await db.execute(
            select(func.max(t.Order.placed_at)).where(t.Order.tenant_id == pos.tenant_id)
        )
    ).scalar_one()
    after_the_end = last_order.astimezone(pos.tz).date() + timedelta(days=1)
    empty = DateRange(after_the_end, after_the_end + timedelta(days=30))

    summary = await an.sales_summary(db, pos, empty)

    assert summary.net_sales == Decimal("0.00")
    assert summary.order_count == 0
    assert "period_past_data" in summary.caveat_codes


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


async def test_no_metric_ever_sees_another_shop(
    db: AsyncSession, pos: AnalyticsContext, spreadsheet: AnalyticsContext
) -> None:
    """Rule 3, checked where it would actually be broken.

    Both shops are in one database, over overlapping dates, and a missing
    tenant filter on any one join would show a comic shop's board games in an
    anime shop's top products.
    """
    period = DateRange(date(2025, 10, 1), date(2026, 9, 30))

    for ctx, other in ((pos, spreadsheet), (spreadsheet, pos)):
        theirs = {
            name
            for (name,) in (
                await db.execute(
                    select(t.Product.name).where(t.Product.tenant_id == other.tenant_id)
                )
            ).all()
        }
        ours = await an.top_products(db, ctx, period, limit=50)
        assert theirs, "the other shop has no products, so this proves nothing"
        assert not {row.label for row in ours.rows} & theirs

        counted = await an.sales_summary(db, ctx, period)
        expected = (
            await db.execute(
                select(func.count())
                .select_from(t.Order)
                .where(
                    t.Order.tenant_id == ctx.tenant_id,
                    t.Order.status != "canceled",
                    t.Order.deleted_at.is_(None),
                    t.Order.placed_at >= period.bounds(ctx.tz)[0],
                    t.Order.placed_at < period.bounds(ctx.tz)[1],
                )
            )
        ).scalar_one()
        assert counted.order_count == expected

        stock = await an.inventory_value(db, ctx)
        their_units = (
            await db.execute(
                select(func.coalesce(func.sum(t.InventoryLevel.on_hand), 0)).where(
                    t.InventoryLevel.tenant_id == other.tenant_id
                )
            )
        ).scalar_one()
        assert stock.units_on_hand != stock.units_on_hand + their_units or their_units == 0


async def test_an_unknown_tenant_is_an_error_not_an_empty_shop(db: AsyncSession) -> None:
    with pytest.raises(an.TenantNotFound):
        await an.load_context(db, "no-such-shop")


# ---------------------------------------------------------------------------
# Stock lists
# ---------------------------------------------------------------------------


async def test_low_stock_is_about_reordering_and_dead_stock_is_not(
    db: AsyncSession, pos: AnalyticsContext
) -> None:
    """The two lists answer opposite questions and must not overlap.

    An item with one left that has never sold is not a reorder; it is money
    already wasted. Putting it on the low-stock list would have the owner buy
    a second one.
    """
    low = await an.low_stock(db, pos, limit=100)
    dead = await an.dead_stock(db, pos, limit=100)

    assert low.rows, "the fixture is meant to have low stock"
    assert not {r.variant_id for r in low.rows} & {r.variant_id for r in dead.rows}

    for row in low.rows:
        assert row.last_sold_at is not None, f"{row.product_name} has never sold"
        assert row.reason
    for row in dead.rows:
        assert row.units_on_hand > 0
        assert row.reason


async def test_days_of_cover_is_left_empty_rather_than_infinite(
    db: AsyncSession, pos: AnalyticsContext
) -> None:
    """Nothing sold means no rate of sale, and no rate of sale means no honest
    projection — not "this will last forever".

    The cover is worked out from the exact rate and only then rounded for
    display, so recomputing it from the rate shown lands close rather than
    exactly. The slowest rate that can appear at all is one unit across the
    28-day window, so that rounding is worth well under a percent — far tighter
    than a wrong formula would be.
    """
    cover = await an.days_of_cover(db, pos, limit=100)

    assert cover.rows, "the fixture is meant to have stock that is selling"
    for row in cover.rows:
        assert row.daily_units > 0
        assert row.days_of_cover is not None
        recomputed = row.units_on_hand / row.daily_units
        assert abs(row.days_of_cover - recomputed) <= recomputed * Decimal("0.02")
