"""The semantic layer against the shop's own system, to the cent.

`test_registerone_quirks.py` proves the adapter carried the data across
faithfully. This file proves the metric definitions on top of it are right: it
computes every number a second time, in SQL, straight out of `registerone_db`,
and compares.

That is the whole point of the exercise. If our "net sales" and the owner's POS
report disagree by a dollar, nothing else we show them gets believed, so the
ground truth here is deliberately the POS's own tables and never our canonical
copy of them.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import analytics as an
from app.analytics import AnalyticsContext, DateRange, Filters
from app.canonical import tables as t

TENANT_SLUG = "tsundoku"
SOURCE_DSN = os.environ.get(
    "REGISTERONE_DB_DSN",
    "postgresql+asyncpg://registerone:registerone@127.0.0.1:5433/registerone",
)
CENT = Decimal("0.01")

# RegisterOne keeps money in integer cents. Everything below converts on the
# source side, so a units mistake shows up as a factor of a hundred rather than
# hiding inside a rounding argument.
CENTS = Decimal("100")

# The shop's own timezone, spelled out here rather than read from our tenants
# table: an independent check should not borrow the value it is checking.
SHOP_TZ = "America/New_York"

# Only orders the shop would count. The canonical side spells this as
# `status <> 'canceled'`; this is the same rule in RegisterOne's vocabulary.
LIVE_ORDERS = "o.state <> 'CANCELED'"

# Stock the shop still sells. Deleting an item in RegisterOne leaves its count
# rows behind, and neither side counts those.
LIVE_STOCK = """
from inventory_counts ic
join item_variations v on v.id = ic.variation_id and v.is_deleted = false
join catalog_items ci on ci.id = v.item_id and ci.is_deleted = false
left join variation_vendor_info vi on vi.variation_id = v.id
where ic.state = 'IN_STOCK'
"""


@pytest.fixture
async def ctx(db: AsyncSession) -> AnalyticsContext:
    tenant = (
        await db.execute(select(t.Tenant).where(t.Tenant.slug == TENANT_SLUG))
    ).scalar_one_or_none()
    if tenant is None:
        pytest.skip(f"{TENANT_SLUG} does not exist; run `python tasks.py backfill`")
    orders = (
        await db.execute(
            select(func.count()).select_from(t.Order).where(t.Order.tenant_id == tenant.id)
        )
    ).scalar_one()
    if not orders:
        pytest.skip(f"{TENANT_SLUG} has no orders; run `python tasks.py backfill`")
    return await an.load_context(db, TENANT_SLUG)


@pytest.fixture
async def source() -> AsyncIterator[AsyncSession]:
    """Ground truth, straight out of the fake POS's database."""
    engine = create_async_engine(SOURCE_DSN, poolclass=NullPool)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        try:
            await session.execute(text("select 1"))
        except (OSError, OperationalError, InterfaceError, DBAPIError) as exc:
            pytest.skip(f"registerone_db not reachable ({type(exc).__name__}); `tasks.py sources`")
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def one(session: AsyncSession, sql: str, **params: Any) -> Any:
    return (await session.execute(text(sql), params)).scalar_one()


async def rows(session: AsyncSession, sql: str, **params: Any) -> list[Any]:
    return list((await session.execute(text(sql), params)).all())


def dollars(cents: Any) -> Decimal:
    return (Decimal(cents or 0) / CENTS).quantize(CENT)


async def require_fresh_sync(db: AsyncSession, ctx: AnalyticsContext, source: AsyncSession) -> None:
    """Skip point-in-time checks when RegisterOne has moved on since the sync.

    Sales checks bind both sides to the same period, so an unsynced day cannot
    reach them. Stock has no such window — it is whatever the last sync brought
    back — so comparing it against a source that has since sold three more
    booster boxes would fail for a reason that has nothing to do with the metric.
    """
    ours = await one(
        db,
        "select max(o.placed_at) from orders o where o.tenant_id = :tenant",
        tenant=ctx.tenant_id,
    )
    theirs = await one(source, "select max(created_at) from orders")
    if theirs > ours:
        pytest.skip(
            f"registerone_db has sales up to {theirs:%Y-%m-%d} but we only hold "
            f"{ours:%Y-%m-%d}; run `python tasks.py incremental`"
        )


def between(period: DateRange) -> dict[str, Any]:
    """Bind a period to the source query the way the shop thinks of it: whole
    local days, not UTC instants."""
    return {"from_day": period.start, "to_day": period.end}


# The day-in-shop-time expression every source query filters on.
LOCAL_DAY = f"(o.created_at at time zone '{SHOP_TZ}')::date"
REFUND_LOCAL_DAY = f"(r.created_at at time zone '{SHOP_TZ}')::date"
IN_PERIOD = f"{LOCAL_DAY} between :from_day and :to_day"
REFUND_IN_PERIOD = f"{REFUND_LOCAL_DAY} between :from_day and :to_day"

# The month with the Christmas spike: a busy period with discounts, refunds,
# custom amounts and con-booth sales all in it.
BUSY_MONTH = (2025, 12)


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------


async def test_sales_summary_matches_the_source(
    db: AsyncSession, ctx: AnalyticsContext, source: AsyncSession
) -> None:
    """Every figure on the KPI row, checked against the POS.

    A drift here is the worst failure this product can have: it is wrong and it
    looks right.
    """
    period = ctx.month(*BUSY_MONTH)
    ours = await an.sales_summary(db, ctx, period)

    gross = dollars(
        await one(
            source,
            f"""
            select coalesce(sum(l.gross_sales_money), 0)
            from order_line_items l join orders o on o.id = l.order_id
            where {LIVE_ORDERS} and {IN_PERIOD}
            """,
            **between(period),
        )
    )
    discounts = dollars(
        await one(
            source,
            f"""
            select coalesce(sum(l.total_discount_money), 0)
            from order_line_items l join orders o on o.id = l.order_id
            where {LIVE_ORDERS} and {IN_PERIOD}
            """,
            **between(period),
        )
    )
    refunds = dollars(
        await one(
            source,
            f"select coalesce(sum(r.amount_money), 0) from refunds r where {REFUND_IN_PERIOD}",
            **between(period),
        )
    )
    units = Decimal(
        await one(
            source,
            f"""
            select coalesce(sum(l.quantity::numeric), 0)
            from order_line_items l join orders o on o.id = l.order_id
            where {LIVE_ORDERS} and {IN_PERIOD}
            """,
            **between(period),
        )
    )
    orders = await one(
        source,
        f"select count(*) from orders o where {LIVE_ORDERS} and {IN_PERIOD}",
        **between(period),
    )

    assert ours.gross_sales == gross
    assert ours.discounts == discounts
    assert ours.refunds == refunds
    assert ours.net_sales == gross - discounts - refunds
    assert ours.units_sold == units
    assert ours.order_count == orders
    assert ours.average_order_value == ((gross - discounts) / orders).quantize(CENT)


async def test_every_month_matches_the_source(
    db: AsyncSession, ctx: AnalyticsContext, source: AsyncSession
) -> None:
    """The whole history, month by month.

    One matching month could be luck with timezones; eighteen of them could not.
    A month-end boundary cut in UTC instead of shop time would move a Knoxville
    evening's sales into the next month and show up right here.

    The window is the history we hold, and the source query is bound to exactly
    that window. A simulated day sitting in RegisterOne that nobody has synced
    yet is a freshness question for the sync suite, not a wrong definition here.
    """
    first_at, last_at = (
        await one(
            db,
            "select min(o.placed_at) from orders o where o.tenant_id = :tenant",
            tenant=ctx.tenant_id,
        ),
        await one(
            db,
            "select max(o.placed_at) from orders o where o.tenant_id = :tenant",
            tenant=ctx.tenant_id,
        ),
    )
    period = DateRange(first_at.astimezone(ctx.tz).date(), last_at.astimezone(ctx.tz).date())
    ours = await an.sales_series(db, ctx, period, an.Grain.MONTH)

    truth = {
        row.month: dollars(row.net)
        for row in await rows(
            source,
            f"""
            with sold as (
                select date_trunc('month', o.created_at at time zone '{SHOP_TZ}')::date as month,
                       sum(l.gross_sales_money) - sum(l.total_discount_money) as net
                from orders o join order_line_items l on l.order_id = o.id
                where {LIVE_ORDERS} and {IN_PERIOD}
                group by 1
            ),
            given_back as (
                select date_trunc('month', r.created_at at time zone '{SHOP_TZ}')::date as month,
                       sum(r.amount_money) as refunds
                from refunds r where {REFUND_IN_PERIOD} group by 1
            )
            select s.month as month, s.net - coalesce(g.refunds, 0) as net
            from sold s left join given_back g on g.month = s.month
            """,
            **between(period),
        )
    }

    assert {p.bucket for p in ours.points} == set(truth)
    for point in ours.points:
        assert point.net_sales == truth[point.bucket], f"{point.label} disagrees with the POS"


async def test_daily_buckets_are_cut_in_shop_time(
    db: AsyncSession, ctx: AnalyticsContext, source: AsyncSession
) -> None:
    """Day boundaries, which are where a UTC mistake actually bites.

    Knoxville is five hours behind UTC, so a sale rung up at 8pm on a Saturday
    is Sunday in UTC. Bucketing in the wrong zone moves that evening's takings
    into the next day, which is exactly the kind of error an owner spots first.
    """
    period = DateRange(ctx.month(*BUSY_MONTH).start, ctx.month(*BUSY_MONTH).start.replace(day=14))
    ours = await an.sales_series(db, ctx, period, an.Grain.DAY)

    truth = {
        row.day: dollars(row.net)
        for row in await rows(
            source,
            f"""
            select {LOCAL_DAY} as day,
                   sum(l.gross_sales_money) - sum(l.total_discount_money) as net
            from orders o join order_line_items l on l.order_id = o.id
            where {LIVE_ORDERS} and {IN_PERIOD}
            group by 1
            """,
            **between(period),
        )
    }
    refunds = {
        row.day: dollars(row.given_back)
        for row in await rows(
            source,
            f"""
            select {REFUND_LOCAL_DAY} as day, sum(r.amount_money) as given_back
            from refunds r where {REFUND_IN_PERIOD} group by 1
            """,
            **between(period),
        )
    }

    for point in ours.points:
        expected = truth.get(point.bucket, Decimal("0.00")) - refunds.get(
            point.bucket, Decimal("0.00")
        )
        assert point.net_sales == expected, f"{point.bucket} disagrees with the POS"


async def test_top_products_match_the_source(
    db: AsyncSession, ctx: AnalyticsContext, source: AsyncSession
) -> None:
    """Both the order and the amounts, since "what sells" is the question this
    product exists to answer."""
    period = ctx.month(*BUSY_MONTH)
    ours = await an.top_products(db, ctx, period, limit=10)

    truth = [
        (row.item, dollars(row.net))
        for row in await rows(
            source,
            f"""
            select i.name as item,
                   sum(l.gross_sales_money) - sum(l.total_discount_money) as net
            from order_line_items l
            join orders o on o.id = l.order_id
            join item_variations v on v.id = l.catalog_object_id
            join catalog_items i on i.id = v.item_id
            where {LIVE_ORDERS} and {IN_PERIOD}
            group by 1 order by 2 desc limit 10
            """,
            **between(period),
        )
    ]

    assert [(r.label, r.net_sales) for r in ours.rows] == truth


async def test_category_breakdown_matches_the_source(
    db: AsyncSession, ctx: AnalyticsContext, source: AsyncSession
) -> None:
    """Categories, including the one that was renamed mid-history.

    The rename keeps its id, so both sides must report today's name against all
    of the history — which is what happens when a breakdown reads the category
    a product is in now rather than a name snapshotted onto the sale.
    """
    period = ctx.month(*BUSY_MONTH)
    ours = await an.category_breakdown(db, ctx, period)

    truth = {
        row.category: dollars(row.net)
        for row in await rows(
            source,
            f"""
            select coalesce(c.name, 'Uncategorised') as category,
                   sum(l.gross_sales_money) - sum(l.total_discount_money) as net
            from order_line_items l
            join orders o on o.id = l.order_id
            join item_variations v on v.id = l.catalog_object_id
            join catalog_items i on i.id = v.item_id
            left join categories c on c.id = i.category_id
            where {LIVE_ORDERS} and {IN_PERIOD}
            group by 1
            """,
            **between(period),
        )
    }

    assert {r.label: r.net_sales for r in ours.rows} == truth


async def test_channel_and_location_splits_add_up(
    db: AsyncSession, ctx: AnalyticsContext, source: AsyncSession
) -> None:
    """A split has to reconcile with the headline number, or the dashboard
    contradicts itself on one screen.

    Both dimensions can own their refunds — an order knows where it was rung up
    — so unlike a product split, these two must come to exactly net sales.
    """
    period = ctx.month(*BUSY_MONTH)
    summary = await an.sales_summary(db, ctx, period)

    for split in (
        await an.channel_breakdown(db, ctx, period),
        await an.location_breakdown(db, ctx, period),
    ):
        assert sum((r.net_sales for r in split.rows), Decimal("0")) == summary.net_sales
        assert split.total_net_sales == summary.net_sales
        assert not split.truncated

    con_booth = await an.channel_breakdown(db, ctx, period)
    event_row = next(r for r in con_booth.rows if r.key == "event")
    event_truth = dollars(
        await one(
            source,
            f"""
            select coalesce(sum(l.gross_sales_money) - sum(l.total_discount_money), 0)
            from order_line_items l join orders o on o.id = l.order_id
            where {LIVE_ORDERS} and {IN_PERIOD} and o.location_id = 'LOC_CON'
            """,
            **between(period),
        )
    ) - dollars(
        await one(
            source,
            f"""
            select coalesce(sum(r.amount_money), 0)
            from refunds r join orders o on o.id = r.order_id
            where {REFUND_IN_PERIOD} and o.location_id = 'LOC_CON'
            """,
            **between(period),
        )
    )
    assert event_row.net_sales == event_truth


async def test_filtered_sales_match_the_source(
    db: AsyncSession, ctx: AnalyticsContext, source: AsyncSession
) -> None:
    """A category filter, checked end to end.

    Filters are where a semantic layer usually starts lying: the filter lands on
    the wrong join and the number quietly changes.
    """
    period = ctx.month(*BUSY_MONTH)
    category = (
        await db.execute(
            select(t.Category).where(
                t.Category.tenant_id == ctx.tenant_id, t.Category.name == "Manga"
            )
        )
    ).scalar_one()

    ours = await an.sales_summary(db, ctx, period, Filters(category_ids=(category.id,)))
    truth = dollars(
        await one(
            source,
            f"""
            select coalesce(sum(l.gross_sales_money) - sum(l.total_discount_money), 0)
            from order_line_items l
            join orders o on o.id = l.order_id
            join item_variations v on v.id = l.catalog_object_id
            join catalog_items i on i.id = v.item_id
            join categories c on c.id = i.category_id
            where {LIVE_ORDERS} and {IN_PERIOD} and c.name = 'Manga'
            """,
            **between(period),
        )
    )

    assert ours.net_sales == truth
    # No refunds were subtracted, and the result says so rather than implying a
    # product-level refund figure exists.
    assert ours.refunds == Decimal("0.00")
    assert "refunds_not_attributable" in ours.caveat_codes


# ---------------------------------------------------------------------------
# Margin
# ---------------------------------------------------------------------------


async def test_margin_matches_the_source_and_reports_its_coverage(
    db: AsyncSession, ctx: AnalyticsContext, source: AsyncSession
) -> None:
    """Cost of goods sold over the lines that have a cost, and nothing else.

    RegisterOne leaves unit cost off about a tenth of its variations. The check
    that matters is not just that COGS matches, but that the sales it is divided
    by are only the covered ones — spreading a known margin over unknown-cost
    items is the failure this is written to catch.
    """
    period = ctx.month(*BUSY_MONTH)
    ours = await an.margin_report(db, ctx, period)

    truth = (
        await rows(
            source,
            f"""
            select
              coalesce(sum(l.gross_sales_money - l.total_discount_money), 0) as sales,
              coalesce(sum(l.gross_sales_money - l.total_discount_money)
                       filter (where vi.unit_cost_amount is not null), 0) as covered,
              coalesce(sum(l.quantity::numeric * vi.unit_cost_amount)
                       filter (where vi.unit_cost_amount is not null), 0) as cogs
            from order_line_items l
            join orders o on o.id = l.order_id
            left join item_variations v on v.id = l.catalog_object_id
            left join variation_vendor_info vi on vi.variation_id = v.id
            where {LIVE_ORDERS} and {IN_PERIOD}
            """,
            **between(period),
        )
    )[0]

    assert ours.sales == dollars(truth.sales)
    assert ours.covered_sales == dollars(truth.covered)
    assert ours.cogs == dollars(truth.cogs)
    assert ours.gross_profit == dollars(truth.covered) - dollars(truth.cogs)

    coverage = ours.cost_coverage
    assert coverage is not None and coverage < 1, "the fixture is meant to have missing costs"
    assert "partial_cost_coverage" in ours.caveat_codes
    # The margin divides by covered sales, not by all sales.
    assert ours.gross_margin == (
        (dollars(truth.covered) - dollars(truth.cogs)) / dollars(truth.covered)
    ).quantize(Decimal("0.0001"))


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


async def test_inventory_value_matches_the_source(
    db: AsyncSession, ctx: AnalyticsContext, source: AsyncSession
) -> None:
    """What is on the shelves, at retail and at cost.

    Items the shop deleted from its catalog are left out of both sides. Stock
    rows outlive a deletion in the POS, and listing something the owner can no
    longer find in their own system as part of their inventory value would be
    worse than useless.
    """
    await require_fresh_sync(db, ctx, source)
    ours = await an.inventory_value(db, ctx)

    truth = (
        await rows(
            source,
            f"""
            select
              coalesce(sum(ic.quantity::numeric), 0) as units,
              coalesce(sum(ic.quantity::numeric * v.price_amount), 0) as retail,
              coalesce(sum(ic.quantity::numeric * vi.unit_cost_amount), 0) as at_cost
            {LIVE_STOCK}
            """,
        )
    )[0]

    assert ours.units_on_hand == Decimal(truth.units)
    assert ours.retail_value == dollars(truth.retail)
    assert ours.cost_value == dollars(truth.at_cost)


async def test_sell_through_matches_the_source(
    db: AsyncSession, ctx: AnalyticsContext, source: AsyncSession
) -> None:
    await require_fresh_sync(db, ctx, source)
    period = ctx.month(*BUSY_MONTH)
    ours = await an.sell_through(db, ctx, period, limit=5)

    sold = Decimal(
        await one(
            source,
            f"""
            select coalesce(sum(l.quantity::numeric), 0)
            from order_line_items l join orders o on o.id = l.order_id
            where {LIVE_ORDERS} and {IN_PERIOD} and l.catalog_object_id is not null
            """,
            **between(period),
        )
    )
    on_hand = Decimal(
        await one(source, f"select coalesce(sum(ic.quantity::numeric), 0) {LIVE_STOCK}")
    )

    assert ours.units_sold == sold
    assert ours.units_on_hand == on_hand
    assert ours.sell_through == (sold / (sold + on_hand)).quantize(Decimal("0.0001"))


async def test_dead_stock_really_has_not_sold(
    db: AsyncSession, ctx: AnalyticsContext, source: AsyncSession
) -> None:
    """Every row on the dead-stock list, checked back against the POS.

    Telling an owner to mark down something that sold last week destroys trust
    faster than being a dollar out, so this asserts the absence of a sale rather
    than a total.
    """
    await require_fresh_sync(db, ctx, source)
    listing = await an.dead_stock(db, ctx, days=90, limit=10)
    if not listing.rows:
        pytest.skip("no dead stock in the fixture")

    for row in listing.rows:
        variant = (
            await db.execute(select(t.Variant).where(t.Variant.id == row.variant_id))
        ).scalar_one()
        recent = await one(
            source,
            f"""
            select count(*)
            from order_line_items l join orders o on o.id = l.order_id
            where {LIVE_ORDERS}
              and l.catalog_object_id = :variation
              and o.created_at >= now() - interval '90 days'
            """,
            variation=variant.external_id,
        )
        assert recent == 0, f"{row.product_name} sold {recent} times in the last 90 days"
        assert row.units_on_hand > 0


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


async def test_repeat_rate_counts_merged_duplicates_once(
    db: AsyncSession, ctx: AnalyticsContext, source: AsyncSession
) -> None:
    """The customer numbers, with the fixture's duplicate records resolved.

    RegisterOne holds the same person under two ids about 5% of the time. The
    source-side truth groups by email to undo that, which is the same thing our
    dedupe step does through `merged_into_id` — computed independently, so a
    dedupe that silently stopped running would fail here.
    """
    period = ctx.month(*BUSY_MONTH)
    ours = await an.customer_stats(db, ctx, period)

    truth = (
        await rows(
            source,
            f"""
            with person as (
                select id, coalesce(lower(nullif(email, '')), 'id:' || id) as key
                from customers
            ),
            in_period as (
                select distinct p.key
                from orders o join person p on p.id = o.customer_id
                where {LIVE_ORDERS} and {IN_PERIOD}
            )
            select
              (select count(*) from in_period) as buyers,
              (select count(*) from in_period ip where exists (
                  select 1 from orders o2 join person p2 on p2.id = o2.customer_id
                  where o2.state <> 'CANCELED'
                    and p2.key = ip.key
                    and (o2.created_at at time zone '{SHOP_TZ}')::date < :from_day
              )) as returning_buyers,
              (select count(*) from orders o where {LIVE_ORDERS} and {IN_PERIOD}) as orders,
              (select count(*) from orders o
               where {LIVE_ORDERS} and {IN_PERIOD} and o.customer_id is not null) as identified
            """,
            **between(period),
        )
    )[0]

    assert ours.customers_in_period == truth.buyers
    assert ours.returning_customers == truth.returning_buyers
    assert ours.new_customers == truth.buyers - truth.returning_buyers
    assert ours.orders_total == truth.orders
    assert ours.orders_with_customer == truth.identified
    assert ours.repeat_rate == (Decimal(truth.returning_buyers) / Decimal(truth.buyers)).quantize(
        Decimal("0.0001")
    )
    # Most sales are cash walk-ins, and a repeat rate is meaningless without
    # saying so.
    assert "anonymous_sales" in ours.caveat_codes
