"""The semantic layer: every metric, defined exactly once.

The dashboard and the agent both call these functions. Neither computes a
number of its own, and the model never writes SQL (CLAUDE.md rule 4), so when a
shop owner asks the assistant for last December and then looks at the chart,
they are looking at the same arithmetic.

Three habits run through all of it:

* **Say what was left out.** Results carry caveats. A margin figure that covers
  88% of sales says so, every time, in a sentence a shop owner can act on.
* **Refuse rather than invent.** When the source system cannot support a metric,
  the function raises `CapabilityUnavailable` instead of returning zeros.
* **Shop time, not UTC.** Every period and every bucket is cut in the tenant's
  timezone, because "yesterday" in Knoxville is not yesterday in UTC.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.context import AnalyticsContext, CapabilityUnavailable, DateRange
from app.analytics.queries import (
    DIMENSION_SQL,
    DISCOUNTS,
    GRAIN_SQL,
    GROSS,
    ORDERS,
    REFUND_GRAIN_SQL,
    REFUND_ROWS,
    SOLD_LINES,
    STOCK_ROWS,
    UNITS,
    VARIANT_LABEL,
    VELOCITY_WINDOW_DAYS,
    Filters,
    combine,
    coverage_caveats,
    data_window,
    fetch_all,
    fetch_one,
    order_scope,
    refund_scope,
    sales_scope,
    stock_scope,
    velocity_window,
    with_ctes,
)
from app.analytics.results import (
    Breakdown,
    BreakdownRow,
    Caveat,
    CustomerStats,
    Dimension,
    Grain,
    InventoryValue,
    MarginReport,
    SalesSeries,
    SalesSummary,
    SellThrough,
    SellThroughRow,
    SeriesPoint,
    StockList,
    StockRow,
    money,
    share,
    units,
)

# Below this, a caveat about custom-amount sales is noise rather than news.
CUSTOM_AMOUNT_NOTICE = Decimal("0.01")
# Default "running out" line for days of cover, when the caller names none.
DEFAULT_COVER_DAYS = 14

NO_FILTERS = Filters()


def _base(ctx: AnalyticsContext, period: DateRange) -> dict[str, object]:
    return {
        "tenant": ctx.slug,
        "currency": ctx.currency,
        "timezone": ctx.timezone,
        "period_start": period.start,
        "period_end": period.end,
    }


def _custom_amount_caveat(custom: Decimal, net: Decimal) -> Caveat | None:
    """Custom-amount sales are real money attached to no product.

    They belong in a sales total and cannot belong in a product breakdown, so
    every total that contains them says how much they were.
    """
    if net <= 0 or custom <= 0:
        return None
    portion = custom / net
    if portion < CUSTOM_AMOUNT_NOTICE:
        return None
    return Caveat(
        code="custom_amount_sales",
        message=(
            f"{portion:.1%} of these sales ({money(custom)}) were rung up as a custom amount "
            "with no product attached, so they count toward the total but not toward any one "
            "product."
        ),
    )


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------


async def sales_summary(
    session: AsyncSession,
    ctx: AnalyticsContext,
    period: DateRange,
    filters: Filters = NO_FILTERS,
) -> SalesSummary:
    """Gross sales, discounts, refunds, net sales, units, orders and AOV.

    One query for what sold and one for what came back, because the two are
    dated differently: a sale counts on the day it was rung up, a refund on the
    day the money went back.
    """
    scope = sales_scope(ctx, period, filters)
    sold = await fetch_one(
        session,
        scope.statement(
            f"""
            select
              {GROSS} as gross,
              {DISCOUNTS} as discounts,
              {UNITS} as units_sold,
              {ORDERS} as order_count,
              coalesce(sum(l.quantity * l.unit_price - l.discount)
                       filter (where l.variant_id is null), 0) as custom_amount
            {SOLD_LINES}
            where {scope.where}
            """
        ),
        scope.params,
    )

    caveats: list[Caveat] = []
    refunds = Decimal("0")
    if filters.narrows_to_products:
        # Nothing to subtract honestly: see `refund_scope`.
        caveats.extend(refund_scope(ctx, period, filters).caveats)
    else:
        rscope = refund_scope(ctx, period, filters)
        refunds = Decimal(
            (
                await fetch_one(
                    session,
                    f"select coalesce(sum(r.amount), 0) as refunds {REFUND_ROWS} "
                    f"where {rscope.where}",
                    rscope.params,
                )
            ).refunds
        )

    gross = Decimal(sold.gross)
    discounts = Decimal(sold.discounts)
    net = gross - discounts - refunds
    order_count = int(sold.order_count)

    first_at, last_at = await data_window(session, ctx)
    caveats.extend(coverage_caveats(ctx, period, first_at, last_at))
    custom = _custom_amount_caveat(Decimal(sold.custom_amount), gross - discounts)
    if custom is not None:
        caveats.append(custom)

    return SalesSummary(
        **_base(ctx, period),
        gross_sales=money(gross),
        discounts=money(discounts),
        refunds=money(refunds),
        net_sales=money(net),
        units_sold=units(sold.units_sold),
        order_count=order_count,
        average_order_value=money((gross - discounts) / order_count) if order_count else money(0),
        caveats=caveats,
    )


def _bucket_starts(period: DateRange, grain: Grain) -> list[date]:
    """Every bucket in the range, including the empty ones.

    A chart with a missing week reads as a quiet week rather than as a gap, so
    the zeros are filled in here rather than left to the caller.
    """
    if grain is Grain.DAY:
        return [period.start + timedelta(days=i) for i in range(period.days)]

    if grain is Grain.WEEK:
        # Monday, matching Postgres's date_trunc('week').
        cursor = period.start - timedelta(days=period.start.weekday())
        buckets = []
        while cursor <= period.end:
            buckets.append(cursor)
            cursor += timedelta(days=7)
        return buckets

    cursor = period.start.replace(day=1)
    buckets = []
    while cursor <= period.end:
        buckets.append(cursor)
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return buckets


def _bucket_label(bucket: date, grain: Grain) -> str:
    if grain is Grain.MONTH:
        return bucket.strftime("%b %Y")
    if grain is Grain.WEEK:
        return f"week of {bucket.isoformat()}"
    return bucket.isoformat()


async def sales_series(
    session: AsyncSession,
    ctx: AnalyticsContext,
    period: DateRange,
    grain: Grain = Grain.WEEK,
    filters: Filters = NO_FILTERS,
) -> SalesSeries:
    """Net sales over time, bucketed in the shop's own timezone."""
    scope = sales_scope(ctx, period, filters)
    params = {**scope.params, "tz": ctx.timezone}
    sold = await fetch_all(
        session,
        scope.statement(
            f"""
            select {GRAIN_SQL[grain]} as bucket,
                   {GROSS} as gross,
                   {DISCOUNTS} as discounts,
                   {UNITS} as units_sold,
                   {ORDERS} as order_count
            {SOLD_LINES}
            where {scope.where}
            group by 1
            """
        ),
        params,
    )

    caveats: list[Caveat] = []
    refunded: dict[date, Decimal] = {}
    if filters.narrows_to_products:
        caveats.extend(refund_scope(ctx, period, filters).caveats)
    else:
        rscope = refund_scope(ctx, period, filters)
        rows = await fetch_all(
            session,
            f"""
            select {REFUND_GRAIN_SQL[grain]} as bucket, coalesce(sum(r.amount), 0) as refunds
            {REFUND_ROWS}
            where {rscope.where}
            group by 1
            """,
            {**rscope.params, "tz": ctx.timezone},
        )
        refunded = {row.bucket: Decimal(row.refunds) for row in rows}

    by_bucket = {row.bucket: row for row in sold}
    points = []
    for bucket in _bucket_starts(period, grain):
        row = by_bucket.get(bucket)
        gross = Decimal(row.gross) if row else Decimal("0")
        discounts = Decimal(row.discounts) if row else Decimal("0")
        refunds = refunded.get(bucket, Decimal("0"))
        points.append(
            SeriesPoint(
                bucket=bucket,
                label=_bucket_label(bucket, grain),
                gross_sales=money(gross),
                discounts=money(discounts),
                refunds=money(refunds),
                net_sales=money(gross - discounts - refunds),
                units_sold=units(row.units_sold) if row else Decimal("0"),
                order_count=int(row.order_count) if row else 0,
            )
        )

    first_at, last_at = await data_window(session, ctx)
    caveats.extend(coverage_caveats(ctx, period, first_at, last_at))

    return SalesSeries(**_base(ctx, period), grain=grain, points=points, caveats=caveats)


async def breakdown(
    session: AsyncSession,
    ctx: AnalyticsContext,
    period: DateRange,
    dimension: Dimension,
    filters: Filters = NO_FILTERS,
    limit: int = 10,
) -> Breakdown:
    """Sales split by one dimension, biggest first.

    Refunds are subtracted per row only where a row can own them — an order
    knows its location and its channel, but not which item came back. For
    product-shaped splits the rows are before refunds and say so.
    """
    if dimension is Dimension.LOCATION:
        ctx.require("multi_location")

    dim = DIMENSION_SQL[dimension]
    scope = sales_scope(ctx, period, filters, require_product=dim.needs_product)
    # One more than asked for, so "there were exactly ten" and "there were more
    # than ten" do not both come back looking truncated.
    params = {**scope.params, "limit": limit + 1}

    fetched = await fetch_all(
        session,
        scope.statement(
            f"""
            select {dim.key} as key,
                   {dim.label} as label,
                   {GROSS} as gross,
                   {DISCOUNTS} as discounts,
                   {UNITS} as units_sold,
                   {ORDERS} as order_count,
                   {GROSS} - {DISCOUNTS} as net
            {SOLD_LINES}
            {dim.join}
            where {scope.where}
            group by 1, 2
            order by net desc
            limit :limit
            """
        ),
        params,
    )
    truncated = len(fetched) > limit
    rows = fetched[:limit]

    # The universe the rows are a share of, including anything the limit cut.
    totals = await fetch_one(
        session,
        scope.statement(f"select {GROSS} - {DISCOUNTS} as net {SOLD_LINES} where {scope.where}"),
        scope.params,
    )
    total = Decimal(totals.net)

    caveats: list[Caveat] = []
    refunded: dict[str | None, Decimal] = {}
    if dim.refundable and not filters.narrows_to_products:
        rscope = refund_scope(ctx, period, filters)
        refund_rows = await fetch_all(
            session,
            f"""
            select {dim.key} as key, coalesce(sum(r.amount), 0) as refunds
            {REFUND_ROWS}
            where {rscope.where}
            group by 1
            """,
            rscope.params,
        )
        refunded = {row.key: Decimal(row.refunds) for row in refund_rows}
        total -= sum(refunded.values(), Decimal("0"))
    else:
        caveats.append(
            Caveat(
                code="breakdown_before_refunds",
                message=(
                    "These rows are before refunds: a refund is recorded against a whole order, "
                    "so it cannot be charged to one item."
                ),
            )
        )

    out = []
    for row in rows:
        net = Decimal(row.net) - refunded.get(row.key, Decimal("0"))
        out.append(
            BreakdownRow(
                key=row.key,
                label=row.label,
                gross_sales=money(row.gross),
                discounts=money(row.discounts),
                net_sales=money(net),
                units_sold=units(row.units_sold),
                order_count=int(row.order_count),
                share_of_net_sales=share(net, total),
            )
        )
    out.sort(key=lambda r: r.net_sales, reverse=True)

    if dim.needs_product:
        excluded = sales_scope(ctx, period, filters)
        all_lines = await fetch_one(
            session,
            excluded.statement(
                f"""
                select coalesce(sum(l.quantity * l.unit_price - l.discount)
                                filter (where l.variant_id is null), 0) as custom_amount
                {SOLD_LINES}
                where {excluded.where}
                """
            ),
            excluded.params,
        )
        custom = _custom_amount_caveat(Decimal(all_lines.custom_amount), total)
        if custom is not None:
            caveats.append(custom)

    return Breakdown(
        **_base(ctx, period),
        dimension=dimension,
        rows=out,
        total_net_sales=money(total),
        truncated=truncated,
        caveats=caveats,
    )


async def top_products(
    session: AsyncSession,
    ctx: AnalyticsContext,
    period: DateRange,
    filters: Filters = NO_FILTERS,
    limit: int = 10,
) -> Breakdown:
    return await breakdown(session, ctx, period, Dimension.PRODUCT, filters, limit)


async def category_breakdown(
    session: AsyncSession,
    ctx: AnalyticsContext,
    period: DateRange,
    filters: Filters = NO_FILTERS,
    limit: int = 25,
) -> Breakdown:
    return await breakdown(session, ctx, period, Dimension.CATEGORY, filters, limit)


async def channel_breakdown(
    session: AsyncSession,
    ctx: AnalyticsContext,
    period: DateRange,
    filters: Filters = NO_FILTERS,
) -> Breakdown:
    return await breakdown(session, ctx, period, Dimension.CHANNEL, filters, limit=8)


async def location_breakdown(
    session: AsyncSession,
    ctx: AnalyticsContext,
    period: DateRange,
    filters: Filters = NO_FILTERS,
) -> Breakdown:
    """Raises CapabilityUnavailable for a single-location shop."""
    return await breakdown(session, ctx, period, Dimension.LOCATION, filters, limit=25)


# ---------------------------------------------------------------------------
# Margin
# ---------------------------------------------------------------------------


async def margin_report(
    session: AsyncSession,
    ctx: AnalyticsContext,
    period: DateRange,
    filters: Filters = NO_FILTERS,
) -> MarginReport:
    """Cost of goods sold and gross margin, over the sales whose cost we know.

    The denominator is deliberately the covered sales rather than all sales.
    Dividing a known cost by an unknown-cost total would quietly report a margin
    the shop does not have, and it would look plausible.
    """
    ctx.require("has_costs")

    scope = sales_scope(ctx, period, filters)
    row = await fetch_one(
        session,
        scope.statement(
            f"""
            select
              coalesce(sum(l.quantity * l.unit_price - l.discount), 0) as sales,
              coalesce(sum(l.quantity * l.unit_price - l.discount)
                       filter (where l.unit_cost_snapshot is not null), 0) as covered,
              coalesce(sum(l.quantity * l.unit_cost_snapshot)
                       filter (where l.unit_cost_snapshot is not null), 0) as cogs
            {SOLD_LINES}
            where {scope.where}
            """
        ),
        scope.params,
    )

    sales = Decimal(row.sales)
    covered = Decimal(row.covered)
    cogs = Decimal(row.cogs)
    coverage = share(covered, sales)

    if sales > 0 and covered <= 0:
        # Costs exist for this shop, but not for a single thing sold in this
        # window. A zero margin would be a claim; this is the truth.
        raise CapabilityUnavailable(
            "has_costs",
            f"Nothing sold between {period.label} had a cost recorded against it, so there is "
            f"no margin to report for {ctx.name} over that period.",
        )

    caveats: list[Caveat] = []
    if coverage is not None and coverage < 1:
        caveats.append(
            Caveat(
                code="partial_cost_coverage",
                message=(
                    f"Margin covers {coverage:.0%} of sales in this period; the other "
                    f"{1 - coverage:.0%} had no cost recorded and is left out of both the cost "
                    "and the margin. Adding costs in your POS fixes this."
                ),
            )
        )
    caveats.append(
        Caveat(
            code="margin_before_refunds",
            message=(
                "Margin is worked out on what was sold, before refunds: we know the money went "
                "back but not which item came with it."
            ),
        )
    )

    first_at, last_at = await data_window(session, ctx)
    caveats.extend(coverage_caveats(ctx, period, first_at, last_at))

    return MarginReport(
        **_base(ctx, period),
        sales=money(sales),
        covered_sales=money(covered),
        cogs=money(cogs),
        gross_profit=money(covered - cogs),
        gross_margin=share(covered - cogs, covered),
        cost_coverage=coverage,
        caveats=caveats,
    )


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


async def inventory_value(
    session: AsyncSession,
    ctx: AnalyticsContext,
    filters: Filters = NO_FILTERS,
) -> InventoryValue:
    """What is on the shelves right now, at retail and at cost."""
    scope = stock_scope(ctx, filters)
    row = await fetch_one(
        session,
        scope.statement(
            f"""
            select
              count(distinct v.id) as variants,
              coalesce(sum(i.on_hand), 0) as units_on_hand,
              coalesce(sum(i.on_hand * v.price), 0) as retail,
              coalesce(sum(i.on_hand * v.cost), 0) as at_cost,
              coalesce(sum(i.on_hand * v.price) filter (where v.cost is not null), 0) as covered,
              coalesce(sum(i.on_hand) filter (where v.price is null), 0) as unpriced_units,
              max(i.as_of) as as_of
            {STOCK_ROWS}
            where {scope.where}
            """
        ),
        scope.params,
    )

    retail = Decimal(row.retail)
    caveats = list(scope.caveats)
    cost_value: Decimal | None = None
    coverage: Decimal | None = None

    if ctx.has("has_costs"):
        cost_value = money(row.at_cost)
        coverage = share(Decimal(row.covered), retail)
        if coverage is not None and coverage < 1:
            caveats.append(
                Caveat(
                    code="partial_cost_coverage",
                    message=(
                        f"The cost figure covers {coverage:.0%} of what is on the shelves; the "
                        "rest has no cost recorded."
                    ),
                )
            )
    else:
        caveats.append(
            Caveat(
                code="no_costs",
                message=(
                    f"{ctx.name}'s system does not record item costs, so this is what the stock "
                    "would sell for, not what it cost to buy."
                ),
            )
        )

    if Decimal(row.unpriced_units) > 0:
        caveats.append(
            Caveat(
                code="unpriced_stock",
                message=(
                    f"{units(row.unpriced_units)} units have no price recorded (items priced at "
                    "the register), so they add nothing to the retail figure."
                ),
            )
        )

    today = ctx.today()
    return InventoryValue(
        **_base(ctx, DateRange(today, today)),
        variants_counted=int(row.variants),
        units_on_hand=units(row.units_on_hand),
        retail_value=money(retail),
        cost_value=cost_value,
        cost_coverage=coverage,
        as_of=row.as_of,
        caveats=caveats,
    )


async def sell_through(
    session: AsyncSession,
    ctx: AnalyticsContext,
    period: DateRange,
    filters: Filters = NO_FILTERS,
    limit: int = 20,
) -> SellThrough:
    """Of what was available to sell, how much sold.

    "Available" is what sold in the period plus what is still on the shelf now.
    That is an approximation — we know today's stock, not the stock on the first
    day of the period — and it is the one every retail report makes.
    """
    sold = sales_scope(ctx, period, filters, require_product=True)
    stock = stock_scope(ctx, filters)
    cte, params, caveats = combine(sold, stock)
    params["limit"] = limit

    statement = with_ctes(
        cte,
        f"""sold as (
            select l.variant_id as variant_id, sum(l.quantity) as units_sold
            {SOLD_LINES}
            where {sold.where}
            group by 1
        )""",
        f"""stock as (
            select i.variant_id as variant_id, sum(i.on_hand) as on_hand
            {STOCK_ROWS}
            where {stock.where}
            group by 1
        )""",
    )

    overall = await fetch_one(
        session,
        statement
        + """
        select coalesce((select sum(units_sold) from sold), 0) as units_sold,
               coalesce((select sum(on_hand) from stock), 0) as on_hand
        """,
        params,
    )
    rows = await fetch_all(
        session,
        statement
        + """
        select v.id as variant_id,
               v.sku as sku,
               """
        + VARIANT_LABEL
        + """ as label,
               coalesce(sold.units_sold, 0) as units_sold,
               coalesce(stock.on_hand, 0) as on_hand
        from variants v
        join products p on p.id = v.product_id
        left join sold on sold.variant_id = v.id
        left join stock on stock.variant_id = v.id
        where v.tenant_id = :tenant
          and (sold.variant_id is not null or stock.variant_id is not null)
        order by coalesce(sold.units_sold, 0) desc
        limit :limit
        """,
        params,
    )

    sold_units = Decimal(overall.units_sold)
    on_hand = Decimal(overall.on_hand)

    return SellThrough(
        **_base(ctx, period),
        units_sold=units(sold_units),
        units_on_hand=units(on_hand),
        sell_through=share(sold_units, sold_units + on_hand),
        rows=[
            SellThroughRow(
                variant_id=row.variant_id,
                sku=row.sku,
                label=row.label,
                units_sold=units(row.units_sold),
                units_on_hand=units(row.on_hand),
                sell_through=share(
                    Decimal(row.units_sold), Decimal(row.units_sold) + Decimal(row.on_hand)
                ),
            )
            for row in rows
        ],
        caveats=caveats,
    )


async def _stock_rows(
    session: AsyncSession,
    ctx: AnalyticsContext,
    filters: Filters,
    window: DateRange,
    having: str,
    order_by: str,
    limit: int,
    extra_params: dict[str, object] | None = None,
) -> tuple[list[StockRow], int]:
    """Current stock per variant, with how fast it has been moving.

    Shared by low stock, dead stock and days of cover: the three of them differ
    only in which rows they keep and how they sort them.
    """
    scope = stock_scope(ctx, filters)
    window_start, window_end = window.bounds(ctx.tz)
    params: dict[str, object] = {
        **scope.params,
        "window_start": window_start,
        "window_end": window_end,
        "window_days": window.days,
        "limit": limit,
        **(extra_params or {}),
    }

    statement = with_ctes(
        scope.cte,
        """velocity as (
            select l.variant_id as variant_id, sum(l.quantity) as units_sold
            from orders o
            join order_lines l on l.order_id = o.id and l.deleted_at is null
            where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
              and o.placed_at >= :window_start and o.placed_at < :window_end
              and l.variant_id is not null
            group by 1
        )""",
        """last_sale as (
            select l.variant_id as variant_id, max(o.placed_at) as last_at
            from orders o
            join order_lines l on l.order_id = o.id and l.deleted_at is null
            where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
              and l.variant_id is not null
            group by 1
        )""",
    )

    rows = await fetch_all(
        session,
        statement
        + f"""
        select v.id as variant_id,
               p.name as product_name,
               v.name as variant_name,
               v.sku as sku,
               c.name as category,
               sum(i.on_hand) as on_hand,
               max(v.price) as price,
               max(v.cost) as cost,
               coalesce(max(velocity.units_sold), 0) as window_units,
               max(last_sale.last_at) as last_sold_at,
               count(*) over () as total_rows
        {STOCK_ROWS}
        left join categories c on c.id = p.category_id
        left join velocity on velocity.variant_id = v.id
        left join last_sale on last_sale.variant_id = v.id
        where {scope.where}
        group by v.id, p.name, v.name, v.sku, c.name
        having {having}
        order by {order_by}
        limit :limit
        """,
        params,
    )

    now = datetime.now(tz=UTC)
    out = []
    for row in rows:
        on_hand = Decimal(row.on_hand)
        daily = Decimal(row.window_units) / Decimal(window.days)
        cover = (on_hand / daily).quantize(Decimal("0.1")) if daily > 0 else None
        last_sold = row.last_sold_at
        out.append(
            StockRow(
                variant_id=row.variant_id,
                product_name=row.product_name,
                variant_name=row.variant_name,
                sku=row.sku,
                category=row.category,
                units_on_hand=units(on_hand),
                price=money(row.price) if row.price is not None else None,
                cost=money(row.cost) if row.cost is not None else None,
                retail_value=money(on_hand * Decimal(row.price or 0)),
                daily_units=daily.quantize(Decimal("0.001")),
                days_of_cover=cover,
                last_sold_at=last_sold,
                days_since_last_sale=(now - last_sold).days if last_sold else None,
            )
        )
    total = int(rows[0].total_rows) if rows else 0
    return out, total


async def days_of_cover(
    session: AsyncSession,
    ctx: AnalyticsContext,
    filters: Filters = NO_FILTERS,
    limit: int = 25,
    as_of: date | None = None,
) -> StockList:
    """How long the current stock lasts at the last four weeks' rate of selling.

    Items that have not sold in the window are left out rather than given an
    infinite cover: they are a dead-stock question, not a reordering one.
    """
    window = velocity_window(ctx, as_of)
    rows, total = await _stock_rows(
        session,
        ctx,
        filters,
        window,
        having="coalesce(max(velocity.units_sold), 0) > 0 and sum(i.on_hand) > 0",
        order_by="sum(i.on_hand) / (coalesce(max(velocity.units_sold), 0) / :window_days) asc",
        limit=limit,
    )
    for row in rows:
        row.reason = f"{row.days_of_cover} days at {row.daily_units} a day"

    return StockList(
        **_base(ctx, window),
        rows=rows,
        row_count=total,
        truncated=total > len(rows),
        retail_value=money(sum((r.retail_value for r in rows), Decimal("0"))),
        caveats=[
            Caveat(
                code="velocity_window",
                message=(
                    f"Rates of sale are measured over the {VELOCITY_WINDOW_DAYS} days ending "
                    f"{window.end.isoformat()}, so a seasonal item will look faster or slower "
                    "than it will be next month."
                ),
            )
        ],
    )


async def low_stock(
    session: AsyncSession,
    ctx: AnalyticsContext,
    filters: Filters = NO_FILTERS,
    threshold: int | None = None,
    cover_days: int = DEFAULT_COVER_DAYS,
    limit: int = 50,
    as_of: date | None = None,
) -> StockList:
    """What to reorder: nearly out, or selling faster than the shelf can hold.

    Only things that have sold inside the velocity window count. One copy left
    of something that last sold four months ago is not a shop about to run out,
    it is dead stock, and "reorder this" would be exactly the wrong advice. That
    rule is also what keeps this list and the dead-stock list from overlapping,
    as long as the quiet window there is at least as long as the velocity window
    used here.
    """
    limit_value = ctx.low_stock_threshold if threshold is None else threshold
    window = velocity_window(ctx, as_of)
    having = (
        "coalesce(max(velocity.units_sold), 0) > 0 and ("
        "sum(i.on_hand) <= :threshold or "
        "sum(i.on_hand) / (coalesce(max(velocity.units_sold), 0) / :window_days) < :cover_days"
        ")"
    )
    rows, total = await _stock_rows(
        session,
        ctx,
        filters,
        window,
        having=having,
        order_by="sum(i.on_hand) asc, coalesce(max(velocity.units_sold), 0) desc",
        limit=limit,
        extra_params={"threshold": limit_value, "cover_days": cover_days},
    )
    for row in rows:
        if row.units_on_hand <= limit_value:
            row.reason = f"{row.units_on_hand} left"
        else:
            row.reason = f"{row.days_of_cover} days of cover"

    return StockList(
        **_base(ctx, window),
        rows=rows,
        row_count=total,
        truncated=total > len(rows),
        retail_value=money(sum((r.retail_value for r in rows), Decimal("0"))),
        caveats=[
            Caveat(
                code="low_stock_rule",
                message=(
                    f"Low stock here means something that has sold in the last "
                    f"{VELOCITY_WINDOW_DAYS} days and has {limit_value} or fewer left, or under "
                    f"{cover_days} days of cover at that rate."
                ),
            )
        ],
    )


async def dead_stock(
    session: AsyncSession,
    ctx: AnalyticsContext,
    filters: Filters = NO_FILTERS,
    days: int | None = None,
    limit: int = 50,
    as_of: date | None = None,
) -> StockList:
    """Money sitting still: stock on the shelf that has not sold in N days."""
    quiet_days = ctx.dead_stock_days if days is None else days
    end = as_of or ctx.today()
    window = DateRange(end - timedelta(days=quiet_days - 1), end)
    cutoff, _ = window.bounds(ctx.tz)

    rows, total = await _stock_rows(
        session,
        ctx,
        filters,
        window,
        having=(
            "sum(i.on_hand) > 0 and "
            "(max(last_sale.last_at) is null or max(last_sale.last_at) < :cutoff)"
        ),
        order_by="sum(i.on_hand) * coalesce(max(v.price), 0) desc",
        limit=limit,
        extra_params={"cutoff": cutoff},
    )
    for row in rows:
        row.reason = (
            "never sold"
            if row.days_since_last_sale is None
            else f"no sale in {row.days_since_last_sale} days"
        )

    return StockList(
        **_base(ctx, window),
        rows=rows,
        row_count=total,
        truncated=total > len(rows),
        retail_value=money(sum((r.retail_value for r in rows), Decimal("0"))),
        caveats=[
            Caveat(
                code="dead_stock_rule",
                message=(
                    f"Dead stock here means still on the shelf with no sale since "
                    f"{window.start.isoformat()} ({quiet_days} days)."
                ),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


async def customer_stats(
    session: AsyncSession,
    ctx: AnalyticsContext,
    period: DateRange,
    filters: Filters = NO_FILTERS,
) -> CustomerStats:
    """New against returning, and the repeat rate.

    Duplicates are resolved through `merged_into_id` before anything is counted,
    so the person who signed up twice is one customer. Sales with nobody
    attached are counted in neither column, and the share of orders that were
    anonymous is reported so the rate can be read in proportion.
    """
    ctx.require("has_customers")

    scope = order_scope(ctx, period, filters)
    statement = with_ctes(
        scope.cte,
        f"""in_period as (
            select o.id as order_id, coalesce(cu.merged_into_id, cu.id) as customer_id
            from orders o
            left join customers cu on cu.id = o.customer_id
            where {scope.where}
        )""",
        """buyers as (
            select distinct customer_id from in_period where customer_id is not null
        )""",
    )

    row = await fetch_one(
        session,
        statement
        + """
        select
          (select count(*) from in_period) as orders_total,
          (select count(customer_id) from in_period) as orders_with_customer,
          (select count(*) from buyers) as customers_in_period,
          (select count(*) from buyers b where exists (
              select 1
              from orders o2
              left join customers c2 on c2.id = o2.customer_id
              where o2.tenant_id = :tenant
                and o2.deleted_at is null
                and o2.status <> 'canceled'
                and o2.placed_at < :start
                and coalesce(c2.merged_into_id, c2.id) = b.customer_id
          )) as returning_customers
        """,
        scope.params,
    )

    buyers = int(row.customers_in_period)
    returning = int(row.returning_customers)
    orders_total = int(row.orders_total)
    identified = share(row.orders_with_customer, orders_total)

    caveats: list[Caveat] = []
    if identified is not None and identified < 1:
        caveats.append(
            Caveat(
                code="anonymous_sales",
                message=(
                    f"{1 - identified:.0%} of orders in this period had no customer attached "
                    "(cash walk-ins), so these figures describe the customers you do know."
                ),
            )
        )
    return CustomerStats(
        **_base(ctx, period),
        customers_in_period=buyers,
        new_customers=buyers - returning,
        returning_customers=returning,
        repeat_rate=share(returning, buyers),
        orders_with_customer=int(row.orders_with_customer),
        orders_total=orders_total,
        identified_share=identified,
        caveats=caveats,
    )
