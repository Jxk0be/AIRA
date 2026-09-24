"""The inputs a reorder decision needs, and nothing more.

Kept in the semantic layer with every other number (CLAUDE.md rule 4) so the
arithmetic on top of it — `app.reorder.forecast` — is pure Python that can be
tested with a list of dataclasses and no database.

The split is deliberate. Everything here is a measurement: how many of this
went out each of the last four weeks, how many are on the shelf, what the
vendor's lead time is, what the same weeks looked like a year ago. Everything
in the forecast is a judgement made from those measurements, and a shop owner
who disagrees with a suggestion is nearly always disagreeing with a judgement,
which is much easier to explain when the two are not tangled together.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.context import AnalyticsContext, DateRange
from app.analytics.queries import Filters, fetch_all, stock_scope, with_ctes
from app.canonical.enums import MovementKind

# Four weeks of history, as four separate weeks rather than one average: the
# whole point is to notice that last week was double the week before.
WEEKS = 4
WEEK_DAYS = 7
HORIZON_DAYS = WEEKS * WEEK_DAYS

NO_FILTERS = Filters()

# Which way a movement pushes the shelf. `adjusted`, `counted` and
# `transferred` have no reliable sign across sources — a count sets an absolute
# figure, a transfer is a minus at one location and a plus at another — so a
# variant whose window contains one is reconstructed as approximate rather than
# guessed at.
MOVEMENT_SIGN: dict[MovementKind, int] = {
    MovementKind.RECEIVED: 1,
    MovementKind.RETURNED: 1,
    MovementKind.SOLD: -1,
    MovementKind.DAMAGED: -1,
}


@dataclass(slots=True)
class DemandRow:
    """Everything measured about one variant at one location."""

    variant_id: uuid.UUID
    location_id: uuid.UUID | None
    location_name: str | None
    product_name: str
    variant_name: str | None
    sku: str | None
    category: str | None
    on_hand: Decimal
    price: Decimal | None
    cost: Decimal | None
    is_active: bool
    # Units sold in each of the last four weeks, most recent first.
    weekly_units: tuple[Decimal, ...] = ()
    last_sold_at: datetime | None = None
    first_sold_at: datetime | None = None
    # The same four weeks, one year earlier, and the trailing year's average
    # over the same span. Both None unless a year of history exists.
    last_year_units: Decimal | None = None
    trailing_year_span_units: Decimal | None = None
    # Days in the window the shelf was empty, so velocity is not divided by
    # days the item could not possibly have sold on.
    stockout_days: int = 0
    stockout_days_approximate: bool = False
    # Vendor terms, best (primary) first.
    vendor_id: uuid.UUID | None = None
    vendor_name: str | None = None
    unit_cost: Decimal | None = None
    pack_size: Decimal = Decimal("1")
    min_order_qty: Decimal = Decimal("0")
    lead_time_days: int | None = None

    @property
    def units_in_window(self) -> Decimal:
        return sum(self.weekly_units, Decimal("0"))

    @property
    def label(self) -> str:
        if self.variant_name and self.variant_name != self.product_name:
            return f"{self.product_name} — {self.variant_name}"
        return self.product_name

    @property
    def best_cost(self) -> Decimal | None:
        """What one unit costs to buy: the vendor's price, else the catalog's."""
        return self.unit_cost if self.unit_cost is not None else self.cost


@dataclass(slots=True)
class DemandInputs:
    """One pull of everything a reorder run needs."""

    as_of: date
    window: DateRange
    rows: list[DemandRow] = field(default_factory=list)
    # True when the shop's source keeps no stock history, so stockout days
    # could not be excluded. Every suggestion built from this carries the
    # caveat rather than pretending the exclusion happened.
    stockouts_unknown: bool = False
    # True when there is less than a year of sales, so no seasonal factor is
    # applied to anything.
    seasonality_unavailable: bool = True


async def demand_inputs(
    session: AsyncSession,
    ctx: AnalyticsContext,
    filters: Filters = NO_FILTERS,
    *,
    as_of: date | None = None,
    limit: int = 2000,
) -> DemandInputs:
    """Measure every variant that could plausibly need reordering.

    "Plausibly" means it sold at least once in the last four weeks. Something
    that has not sold in a month is a dead-stock question, and suggesting a
    reorder for it would be exactly the wrong advice.
    """
    day = as_of or ctx.today()
    window = DateRange(day - timedelta(days=HORIZON_DAYS - 1), day)
    scope = stock_scope(ctx, filters)
    window_start, window_end = window.bounds(ctx.tz)

    # The same four weeks a year ago, plus the trailing year, for seasonality.
    last_year = DateRange(window.start - timedelta(days=365), window.end - timedelta(days=365))
    ly_start, ly_end = last_year.bounds(ctx.tz)
    year_start, year_end = DateRange(day - timedelta(days=364), day).bounds(ctx.tz)

    params: dict[str, object] = {
        **scope.params,
        "window_start": window_start,
        "window_end": window_end,
        "ly_start": ly_start,
        "ly_end": ly_end,
        "year_start": year_start,
        "year_end": year_end,
        "week_days": WEEK_DAYS,
        "limit": limit,
    }

    statement = with_ctes(
        scope.cte,
        # Week 0 is the seven days ending `as_of`, week 3 the oldest.
        """weekly as (
            select l.variant_id as variant_id,
                   floor(extract(epoch from (:window_end - o.placed_at))
                         / (:week_days * 86400))::int as week_index,
                   sum(l.quantity) as units
            from orders o
            join order_lines l on l.order_id = o.id and l.deleted_at is null
            where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
              and o.placed_at >= :window_start and o.placed_at < :window_end
              and l.variant_id is not null
            group by 1, 2
        )""",
        """sale_span as (
            select l.variant_id as variant_id,
                   min(o.placed_at) as first_at,
                   max(o.placed_at) as last_at
            from orders o
            join order_lines l on l.order_id = o.id and l.deleted_at is null
            where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
              and l.variant_id is not null
            group by 1
        )""",
        """last_year as (
            select l.variant_id as variant_id, sum(l.quantity) as units
            from orders o
            join order_lines l on l.order_id = o.id and l.deleted_at is null
            where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
              and o.placed_at >= :ly_start and o.placed_at < :ly_end
              and l.variant_id is not null
            group by 1
        )""",
        """trailing_year as (
            select l.variant_id as variant_id, sum(l.quantity) as units
            from orders o
            join order_lines l on l.order_id = o.id and l.deleted_at is null
            where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
              and o.placed_at >= :year_start and o.placed_at < :year_end
              and l.variant_id is not null
            group by 1
        )""",
        # The primary vendor, or any vendor if none is marked primary.
        """terms as (
            select distinct on (vv.variant_id)
                   vv.variant_id as variant_id,
                   vv.vendor_id as vendor_id,
                   ven.name as vendor_name,
                   vv.unit_cost as unit_cost,
                   vv.pack_size as pack_size,
                   vv.min_order_qty as min_order_qty,
                   vv.lead_time_days as lead_time_days
            from variant_vendors vv
            join vendors ven on ven.id = vv.vendor_id and ven.deleted_at is null
            where vv.tenant_id = :tenant and vv.deleted_at is null
            order by vv.variant_id, vv.is_primary desc, vv.lead_time_days asc
        )""",
    )

    rows = await fetch_all(
        session,
        statement
        + f"""
        select v.id as variant_id,
               i.location_id as location_id,
               loc.name as location_name,
               p.name as product_name,
               v.name as variant_name,
               v.sku as sku,
               c.name as category,
               v.is_active as is_active,
               i.on_hand as on_hand,
               v.price as price,
               v.cost as cost,
               sale_span.first_at as first_at,
               sale_span.last_at as last_at,
               last_year.units as last_year_units,
               trailing_year.units as trailing_year_units,
               terms.vendor_id as vendor_id,
               terms.vendor_name as vendor_name,
               terms.unit_cost as unit_cost,
               terms.pack_size as pack_size,
               terms.min_order_qty as min_order_qty,
               terms.lead_time_days as lead_time_days,
               coalesce(
                 (select sum(w.units) from weekly w where w.variant_id = v.id), 0
               ) as window_units
        {STOCK_ROWS_WITH_LOCATION}
        left join categories c on c.id = p.category_id
        left join sale_span on sale_span.variant_id = v.id
        left join last_year on last_year.variant_id = v.id
        left join trailing_year on trailing_year.variant_id = v.id
        left join terms on terms.variant_id = v.id
        where {scope.where}
          and v.tracks_inventory
          and exists (select 1 from weekly w where w.variant_id = v.id)
        order by window_units desc
        limit :limit
        """,
        params,
    )

    weeks = await _weekly_units(
        session, ctx, [row.variant_id for row in rows], window_start, window_end
    )
    # A variant with no sale at a location still has a shelf there; it just has
    # no demand to measure, which is the correct answer for that shelf.
    out: list[DemandRow] = []
    for row in rows:
        buckets = weeks.get((row.variant_id, row.location_id), {})
        out.append(
            DemandRow(
                variant_id=row.variant_id,
                location_id=row.location_id,
                location_name=row.location_name,
                product_name=row.product_name,
                variant_name=row.variant_name,
                sku=row.sku,
                category=row.category,
                on_hand=Decimal(row.on_hand or 0),
                price=Decimal(row.price) if row.price is not None else None,
                cost=Decimal(row.cost) if row.cost is not None else None,
                is_active=bool(row.is_active),
                weekly_units=tuple(Decimal(buckets.get(index, 0)) for index in range(WEEKS)),
                last_sold_at=row.last_at,
                first_sold_at=row.first_at,
                last_year_units=(
                    Decimal(row.last_year_units) if row.last_year_units is not None else None
                ),
                trailing_year_span_units=_year_span(row.trailing_year_units),
                vendor_id=row.vendor_id,
                vendor_name=row.vendor_name,
                unit_cost=Decimal(row.unit_cost) if row.unit_cost is not None else None,
                pack_size=Decimal(row.pack_size or 1),
                min_order_qty=Decimal(row.min_order_qty or 0),
                lead_time_days=row.lead_time_days,
            )
        )

    inputs = DemandInputs(as_of=day, window=window, rows=out)

    # A shelf with no sales of its own has nothing to forecast from.
    inputs.rows = [row for row in inputs.rows if row.units_in_window > 0]

    if ctx.has("has_inventory_history"):
        await _apply_stockout_days(session, ctx, inputs)
    else:
        inputs.stockouts_unknown = True

    earliest = min((r.first_sold_at for r in out if r.first_sold_at), default=None)
    inputs.seasonality_unavailable = earliest is None or earliest > datetime.combine(
        day - timedelta(days=365), datetime.min.time(), tzinfo=UTC
    )
    return inputs


# `_stock_rows` in metrics.py sums across locations; this one keeps the
# location column so the row can say which shelf most of it is on.
STOCK_ROWS_WITH_LOCATION = """
from inventory_levels i
join variants v on v.id = i.variant_id and v.deleted_at is null
join products p on p.id = v.product_id and p.deleted_at is null
left join locations loc on loc.id = i.location_id
"""


def _year_span(trailing_year_units: Decimal | int | None) -> Decimal | None:
    """The trailing year's units rescaled to a window-length span.

    The seasonal factor compares like with like: four weeks last year against
    the average four weeks of the year, not against the whole year.
    """
    if trailing_year_units is None:
        return None
    return Decimal(trailing_year_units) * Decimal(HORIZON_DAYS) / Decimal(365)


async def _weekly_units(
    session: AsyncSession,
    ctx: AnalyticsContext,
    variant_ids: list[uuid.UUID],
    window_start: datetime,
    window_end: datetime,
) -> dict[tuple[uuid.UUID, uuid.UUID | None], dict[int, Decimal]]:
    """Units per shelf per week, keyed by variant and the location that sold it.

    A second, narrow query rather than a widening of the first. Joining the two
    would multiply the weeks by the locations; keeping them apart also means
    the answer is per *shelf*, which is what a reorder decision is about — a
    copy sitting at a convention booth two weekends a year is not cover for the
    shop that is about to run out of it.
    """
    if not variant_ids:
        return {}
    rows = await fetch_all(
        session,
        """
        select l.variant_id as variant_id,
               o.location_id as location_id,
               floor(extract(epoch from (:window_end - o.placed_at))
                     / (:week_days * 86400))::int as week_index,
               sum(l.quantity) as units
        from orders o
        join order_lines l on l.order_id = o.id and l.deleted_at is null
        where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
          and o.placed_at >= :window_start and o.placed_at < :window_end
          and l.variant_id = any(:variant_ids)
        group by 1, 2, 3
        """,
        {
            "tenant": ctx.tenant_id,
            "window_start": window_start,
            "window_end": window_end,
            "week_days": WEEK_DAYS,
            "variant_ids": list(dict.fromkeys(variant_ids)),
        },
    )
    out: dict[tuple[uuid.UUID, uuid.UUID | None], dict[int, Decimal]] = defaultdict(dict)
    for row in rows:
        index = int(row.week_index)
        if 0 <= index < WEEKS:
            out[(row.variant_id, row.location_id)][index] = Decimal(row.units)
    return out


async def _apply_stockout_days(
    session: AsyncSession, ctx: AnalyticsContext, inputs: DemandInputs
) -> None:
    """Count the days in the window each item spent at zero.

    Worked backwards from today's shelf through the movement history, because
    that is the only direction the data supports: we know what is there now and
    what moved, not what was there four weeks ago. A window containing a
    stock count or a transfer — neither of which has a reliable direction — is
    marked approximate rather than guessed at.
    """
    if not inputs.rows:
        return
    variant_ids = list({row.variant_id for row in inputs.rows})
    start, end = inputs.window.bounds(ctx.tz)
    rows = await fetch_all(
        session,
        """
        select m.variant_id as variant_id,
               m.location_id as location_id,
               m.kind as kind,
               m.quantity as quantity,
               m.occurred_at as occurred_at
        from inventory_movements m
        where m.tenant_id = :tenant and m.deleted_at is null
          and m.occurred_at >= :start and m.occurred_at < :end
          and m.variant_id = any(:variant_ids)
        order by m.occurred_at desc
        """,
        {"tenant": ctx.tenant_id, "start": start, "end": end, "variant_ids": variant_ids},
    )

    moves: dict[tuple[uuid.UUID, uuid.UUID | None], list[tuple[datetime, MovementKind, Decimal]]]
    moves = defaultdict(list)
    for row in rows:
        moves[(row.variant_id, row.location_id)].append(
            (row.occurred_at, MovementKind(row.kind), Decimal(row.quantity))
        )

    for item in inputs.rows:
        history = moves.get((item.variant_id, item.location_id)) or moves.get(
            (item.variant_id, None), []
        )
        if not history:
            continue
        level = item.on_hand
        empty_days: set[date] = set()
        approximate = False
        for occurred_at, kind, quantity in history:
            sign = MOVEMENT_SIGN.get(kind)
            if sign is None:
                approximate = True
                break
            # Walking backwards: undo the movement to get the level before it.
            level -= sign * quantity
            if level <= 0:
                empty_days.add(occurred_at.astimezone(ctx.tz).date())
        item.stockout_days = min(len(empty_days), inputs.window.days - 1)
        item.stockout_days_approximate = approximate


@dataclass(frozen=True, slots=True)
class VariantSales:
    """Units and money for one variant over one window."""

    variant_id: uuid.UUID
    units: Decimal
    net_sales: Decimal
    orders: int


async def variant_sales(
    session: AsyncSession,
    ctx: AnalyticsContext,
    variant_ids: list[uuid.UUID],
    period: DateRange,
) -> dict[uuid.UUID, VariantSales]:
    """How much of each of these sold in a window.

    The measurement behind every outcome in the value ledger: "before the
    markdown" and "after the markdown" are two calls to this with two windows.
    Refunds are not netted off here because a refund is recorded against an
    order rather than a line, so subtracting one would be attributing it to an
    item we cannot know it belongs to.
    """
    if not variant_ids:
        return {}
    start, end = period.bounds(ctx.tz)
    rows = await fetch_all(
        session,
        """
        select l.variant_id as variant_id,
               sum(l.quantity) as units,
               sum(l.quantity * l.unit_price - l.discount) as net_sales,
               count(distinct o.id) as orders
        from orders o
        join order_lines l on l.order_id = o.id and l.deleted_at is null
        where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
          and o.placed_at >= :start and o.placed_at < :end
          and l.variant_id = any(:variant_ids)
        group by 1
        """,
        {
            "tenant": ctx.tenant_id,
            "start": start,
            "end": end,
            "variant_ids": list(dict.fromkeys(variant_ids)),
        },
    )
    return {
        row.variant_id: VariantSales(
            variant_id=row.variant_id,
            units=Decimal(row.units or 0),
            net_sales=Decimal(row.net_sales or 0),
            orders=int(row.orders or 0),
        )
        for row in rows
    }
