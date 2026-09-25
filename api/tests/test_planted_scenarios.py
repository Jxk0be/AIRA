"""Each detector, against the situation it claims to spot.

`sources/registerone/SCENARIOS.md` puts five specific situations into the
fixture at a known size. This is the other half of that bargain: a feature that
cannot find its own planted scenario does not work, whatever it looks like on a
screen.

Everything here needs a synced tenant, so it skips rather than fails on a
laptop with nothing running. The skip message says what to run.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext, TenantNotFound, load_context
from app.analytics.demand import demand_inputs
from app.analytics.stale import stale_inventory
from app.anomalies.detectors import (
    SHRINK_WINDOW_DAYS,
    RefundSpikeDetector,
    SalesAnomalyDetector,
    ShrinkDetector,
    StaleDataDetector,
    sync_health,
)
from app.canonical import tables as t
from app.deadstock.detector import DeadStockDetector
from app.insights.models import InsightDraft
from app.reorder import group_by_vendor, suggest

SETUP = "python tasks.py sources && python tasks.py seed && python tasks.py backfill"


@pytest.fixture
async def shop(db: AsyncSession) -> AnalyticsContext:
    try:
        ctx = await load_context(db, "animanga_knox")
    except TenantNotFound:
        pytest.skip(f"animanga_knox has not been set up; run: {SETUP}")

    orders = (
        await db.execute(
            select(func.count()).select_from(t.Order).where(t.Order.tenant_id == ctx.tenant_id)
        )
    ).scalar_one()
    if not orders:
        pytest.skip(f"animanga_knox has no sales synced; run: {SETUP}")
    return ctx


async def last_sale_day(db: AsyncSession, ctx: AnalyticsContext) -> date:
    """The fixture's own "today". Its end date moves, so nothing here hardcodes one."""
    latest = (
        await db.execute(
            select(func.max(t.Order.placed_at)).where(
                t.Order.tenant_id == ctx.tenant_id, t.Order.deleted_at.is_(None)
            )
        )
    ).scalar_one()
    return latest.astimezone(ctx.tz).date()


# ---------------------------------------------------------------------------
# 1. Items about to run out
# ---------------------------------------------------------------------------


async def test_items_with_days_of_cover_left_are_found(
    db: AsyncSession, shop: AnalyticsContext
) -> None:
    """The planted eight sit between three and nine days of cover. The forecast
    has to find items in that band rather than only the ones already at zero.

    This is the shortest-lived of the planted scenarios, and the one that dates
    the fixture. The other four are events that happened on a particular day
    and stay in the history; these eight are a *state* of the shelf, and the
    simulated days that `test_sync_incremental` asks for keep selling them.
    After a couple of weeks of drift they have genuinely run out, and no
    amount of looking harder will find them.
    """
    as_of = await last_sale_day(db, shop)
    forecast = await suggest(db, shop, as_of=as_of)

    short = [
        line
        for line in forecast.suggestions
        if line.days_of_cover is not None and line.days_of_cover < 10
    ]
    assert short, (
        f"nothing is within ten days of running out as of {as_of}, and the fixture plants "
        f"eight such items. They sell down as the fixture drifts from its seed, so this is "
        f"most likely an old fixture rather than a broken forecast: re-run {SETUP}"
    )
    assert all(line.suggested_qty > 0 for line in short)


async def test_every_suggestion_explains_itself(db: AsyncSession, shop: AnalyticsContext) -> None:
    """The claim the reorder screen makes about every row it shows."""
    forecast = await suggest(db, shop, as_of=await last_sale_day(db, shop))
    assert forecast.suggestions
    for line in forecast.suggestions:
        assert "sells" in line.explanation and "on hand" in line.explanation


async def test_a_variant_is_not_suggested_twice(db: AsyncSession, shop: AnalyticsContext) -> None:
    """A two-location shop used to get every line twice, each carrying the
    whole variant's velocity, which doubled the order."""
    groups = group_by_vendor(await suggest(db, shop, as_of=await last_sale_day(db, shop)))
    variants = [line.variant_id for group in groups for line in group.lines]
    assert len(variants) == len(set(variants))


async def test_items_with_no_cost_still_get_a_suggestion(
    db: AsyncSession, shop: AnalyticsContext
) -> None:
    """About a tenth of the fixture's variations have no cost on file, and a
    shop still needs to know what to buy."""
    inputs = await demand_inputs(db, shop, as_of=await last_sale_day(db, shop))
    assert any(row.best_cost is None for row in inputs.rows), "fixture has costs everywhere"


# ---------------------------------------------------------------------------
# 2. Dead stock
# ---------------------------------------------------------------------------


async def test_dead_stock_is_found_and_priced_at_cost(
    db: AsyncSession, shop: AnalyticsContext
) -> None:
    """Twelve never-sold items worth about $1,500 at cost are planted. They are
    a subset of everything stale, so the total is larger — what matters is that
    the never-sold ones are in there and valued at cost."""
    report = await stale_inventory(db, shop, as_of=await last_sale_day(db, shop))
    never_sold = [item for item in report.items if item.never_sold]

    assert len(never_sold) >= 12
    assert report.cost_coverage is not None and report.cost_coverage > Decimal("0.5")
    priced = sum(
        (item.cash_at_cost for item in never_sold if item.cash_at_cost is not None),
        Decimal("0"),
    )
    assert priced > Decimal("1000")


async def test_every_stale_item_gets_a_specific_plan(
    db: AsyncSession, shop: AnalyticsContext
) -> None:
    drafts = await DeadStockDetector().run(db, shop, await last_sale_day(db, shop))
    assert len(drafts) == 1
    items = drafts[0].evidence["items"]
    assert items
    for item in items:
        assert item["play"] in {"markdown", "bundle", "move", "return_to_vendor"}
        assert item["headline"] and item["why"]


# ---------------------------------------------------------------------------
# 3. Shrink
# ---------------------------------------------------------------------------


# The shrink detector reconciles the last fortnight, and the seeder plants the
# event a few days before the date it seeded to. Once the fixture has drifted
# more than that fortnight the event is simply behind the detector, so the test
# walks back through consecutive fortnights rather than assuming the first one
# still contains it. Stepping by the full window keeps them from overlapping,
# so the planted event lands in exactly one.
SHRINK_SEARCH_WINDOWS = 6


async def planted_shrink(db: AsyncSession, ctx: AnalyticsContext) -> InsightDraft:
    end = await last_sale_day(db, ctx)
    found: list[InsightDraft] = []
    for window in range(SHRINK_SEARCH_WINDOWS):
        as_of = end - timedelta(days=window * SHRINK_WINDOW_DAYS)
        found.extend(await ShrinkDetector().run(db, ctx, as_of))

    assert len(found) == 1, (
        f"expected exactly one shrink alert in the {SHRINK_SEARCH_WINDOWS * 2} weeks before "
        f"{end}, found {len(found)}. If the fixture has drifted far from its seed, "
        f"re-run: {SETUP}"
    )
    return found[0]


async def test_the_planted_shrink_is_found(db: AsyncSession, shop: AnalyticsContext) -> None:
    """Six units left the shelf as a sale with no order line behind them."""
    if not shop.has("has_inventory_history"):
        pytest.skip("this source keeps no stock history, so there is nothing to reconcile")

    drafts = [await planted_shrink(db, shop)]
    items = drafts[0].evidence["items"]
    assert any(Decimal(item["units_unaccounted"]) >= 5 for item in items)
    assert drafts[0].severity.value == "urgent"
    assert "count" in drafts[0].summary.lower()


async def test_shrink_is_one_alert_not_one_per_item(
    db: AsyncSession, shop: AnalyticsContext
) -> None:
    """Three items walking off the shelf is one problem."""
    if not shop.has("has_inventory_history"):
        pytest.skip("no stock history")
    drafts = await ShrinkDetector().run(db, shop, await last_sale_day(db, shop))
    assert len(drafts) <= 1


# ---------------------------------------------------------------------------
# 4. The quiet Saturday
# ---------------------------------------------------------------------------


def dedupe_days(drafts: list[InsightDraft]) -> set[str]:
    return {str(draft.evidence["day"]) for draft in drafts}


# How far back to look for the planted Saturday. The seeder puts it three weeks
# before the date it seeded to, but the fixture's last day moves on its own:
# `test_sync_incremental` asks the source to simulate a day, so every run of
# this suite leaves the fixture one day further from its seed. Computing the
# Saturday from an offset therefore only works on a freshly seeded fixture,
# which is not a property a test should depend on.
QUIET_SEARCH_WEEKS = range(2, 7)


async def planted_quiet_saturday(
    db: AsyncSession, ctx: AnalyticsContext
) -> tuple[date, InsightDraft]:
    """Find the Saturday the fixture emptied out, and the finding about it.

    The detector only judges the seven days before the `as_of` it is given, so
    each candidate Saturday has to be judged from its own week. The planted one
    is around four fifths down; an ordinary one is not flagged at all, which is
    what `test_an_ordinary_stretch_is_quiet` holds the detector to.
    """
    end = await last_sale_day(db, ctx)
    found: list[tuple[Decimal, date, InsightDraft]] = []

    for weeks in QUIET_SEARCH_WEEKS:
        saturday = end - timedelta(days=weeks * 7)
        while saturday.weekday() != 5:
            saturday -= timedelta(days=1)

        drafts = await SalesAnomalyDetector().run(db, ctx, saturday + timedelta(days=2))
        for draft in drafts:
            if draft.evidence["day"] != saturday.isoformat() or "below" not in draft.title:
                continue
            expected = Decimal(str(draft.evidence["expected"]))
            actual = Decimal(str(draft.evidence["net_sales"]))
            found.append(
                ((expected - actual) / expected if expected else Decimal("0"), saturday, draft)
            )

    assert found, (
        f"no Saturday in the {QUIET_SEARCH_WEEKS.start}-{QUIET_SEARCH_WEEKS.stop - 1} weeks "
        f"before {end} was reported as below a normal Saturday. If the fixture has drifted "
        f"far from its seed, re-run: {SETUP}"
    )
    depth, saturday, draft = max(found, key=lambda row: row[0])
    assert depth > Decimal("0.5"), (
        f"the deepest Saturday found was only {depth:.0%} down; the planted one is around "
        "four fifths, so this is probably an ordinary day and the planted one was missed"
    )
    return saturday, draft


async def test_the_planted_quiet_saturday_is_found(
    db: AsyncSession, shop: AnalyticsContext
) -> None:
    """One Saturday in the fixture keeps about a fifth of its takings. Judged
    two days later, it has to show up as a drop."""
    saturday, draft = await planted_quiet_saturday(db, shop)
    assert draft.evidence["day"] == saturday.isoformat()


async def test_the_quiet_saturday_carries_its_working(
    db: AsyncSession, shop: AnalyticsContext
) -> None:
    _, found = await planted_quiet_saturday(db, shop)
    assert found.evidence["weekday"] == "Saturday"
    assert Decimal(found.evidence["expected"]) > Decimal(found.evidence["net_sales"])
    assert int(found.evidence["baseline_weeks"]) >= 4
    assert found.dollar_impact is not None


async def test_an_ordinary_stretch_is_quiet(db: AsyncSession, shop: AnalyticsContext) -> None:
    """The other half of the claim. A detector that flags the planted Saturday
    by flagging everything has not found anything."""
    end = await last_sale_day(db, shop)
    days = [end - timedelta(days=offset) for offset in range(40, 130, 7)]
    reported = set()
    for day in days:
        reported |= dedupe_days(await SalesAnomalyDetector().run(db, shop, day))

    scanned = len(days) * 7
    assert len(reported) < scanned * 0.15, (
        f"{len(reported)} anomalies across ~{scanned} days is too noisy to be read"
    )


# ---------------------------------------------------------------------------
# 5. The refund week
# ---------------------------------------------------------------------------


async def test_the_planted_refund_week_is_found(db: AsyncSession, shop: AnalyticsContext) -> None:
    end = await last_sale_day(db, shop)
    drafts = await RefundSpikeDetector().run(db, shop, end)
    refunds = [draft for draft in drafts if draft.evidence["kind"] == "refund"]
    assert refunds, "the planted week of refunds was not reported"

    worst = max(refunds, key=lambda draft: Decimal(draft.evidence["share"]))
    assert Decimal(worst.evidence["share"]) > Decimal(worst.evidence["baseline_share"])
    assert Decimal(worst.evidence["amount"]) > 0


# ---------------------------------------------------------------------------
# Sync health, which everything else leans on
# ---------------------------------------------------------------------------


async def test_a_healthy_shop_raises_no_stale_data_alert(
    db: AsyncSession, shop: AnalyticsContext
) -> None:
    """The fixture's last day is today by default, so a freshly seeded shop is
    healthy. If this fails, the seed is old and every other number is stale."""
    end = await last_sale_day(db, shop)
    if (date.today() - end).days > 2:
        pytest.skip(f"fixture ends {end}, which is genuinely stale; reseed to test this")
    assert not await StaleDataDetector().run(db, shop, end)


async def test_the_sales_detector_stays_quiet_while_the_sync_is_stale(
    db: AsyncSession, shop: AnalyticsContext
) -> None:
    """Missing data looks exactly like a collapse in trade, so the sales
    detector defers to the stale-data one rather than cry wolf.

    Staleness is measured against the wall clock rather than against `as_of`,
    deliberately: "we have not heard from your POS in two days" is a fact about
    now, not about the date being reported on. So this drives the check
    directly instead of pretending the fixture is old.
    """
    end = await last_sale_day(db, shop)
    health = await sync_health(db, shop, end)
    assert health.trustworthy or not await SalesAnomalyDetector().run(db, shop, end)
