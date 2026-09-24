"""When the shop is actually busy, against when somebody is in.

A small feature that owners like because it replaces a gut feeling with eight
weeks of their own tills. Two rules keep it honest.

The first is that event days come out of the heatmap by default. A shop that
does two conventions a quarter has a couple of Saturdays that say nothing about
a normal Saturday, and leaving them in makes the busiest hour of the week a
weekend the shop was not even in the building.

The second is that everything is phrased as an observation. "Tuesdays 11–1
averaged 1.2 orders an hour over eight weeks" is a fact about the shop.
"Cut a shift on Tuesday" is a decision about somebody's job, and this feature
does not have the standing to make it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext, DateRange
from app.analytics.queries import fetch_all
from app.anomalies.baselines import event_days
from app.canonical import tables as t
from app.db import table_of

WEEKS = 8
WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

# An hour with fewer than this many orders per staff member on the floor is
# quiet enough to be worth mentioning.
QUIET_ORDERS_PER_STAFF = Decimal("1.0")
# And more than this is busy enough that one person is probably stretched.
BUSY_ORDERS_PER_STAFF = Decimal("6.0")
# How many staffed hours a pattern has to cover before it is a pattern.
MIN_HOURS_OBSERVED = 6


@dataclass(slots=True)
class Cell:
    """One weekday-hour, averaged over the window."""

    weekday: int
    hour: int
    orders: Decimal
    net_sales: Decimal
    # How many of that weekday fell inside the window, so an average is honest
    # when a bank holiday took one out.
    observations: int

    @property
    def orders_per_occurrence(self) -> Decimal:
        if not self.observations:
            return Decimal("0")
        return (self.orders / Decimal(self.observations)).quantize(Decimal("0.01"))

    @property
    def sales_per_occurrence(self) -> Decimal:
        if not self.observations:
            return Decimal("0")
        return (self.net_sales / Decimal(self.observations)).quantize(Decimal("0.01"))


@dataclass(slots=True)
class Heatmap:
    location_id: uuid.UUID | None
    location_name: str
    weeks: int
    period: DateRange
    cells: list[Cell] = field(default_factory=list)
    event_days_excluded: int = 0
    # The same grid built from event days only, so the owner can see the con
    # weekend rather than have it quietly dropped.
    event_cells: list[Cell] = field(default_factory=list)

    def cell(self, weekday: int, hour: int) -> Cell | None:
        return next(
            (c for c in self.cells if c.weekday == weekday and c.hour == hour), None
        )

    @property
    def busiest(self) -> Cell | None:
        return max(self.cells, key=lambda c: c.orders_per_occurrence, default=None)


async def heatmap(
    session: AsyncSession,
    ctx: AnalyticsContext,
    *,
    location_id: uuid.UUID | None = None,
    as_of: date | None = None,
    weeks: int = WEEKS,
    include_events: bool = False,
) -> list[Heatmap]:
    """Orders and net sales by weekday and hour, in the shop's own timezone."""
    day = as_of or ctx.today()
    period = DateRange(day - timedelta(days=weeks * 7), day - timedelta(days=1))
    start, end = period.bounds(ctx.tz)
    tagged = event_days(ctx)

    rows = await fetch_all(
        session,
        """
        select o.location_id as location_id,
               coalesce(loc.name, 'Unknown location') as location_name,
               (o.placed_at at time zone :tz)::date as day,
               extract(dow from (o.placed_at at time zone :tz))::int as dow,
               extract(hour from (o.placed_at at time zone :tz))::int as hour,
               count(distinct o.id) as orders,
               sum(l.quantity * l.unit_price - l.discount) as net_sales,
               bool_or(o.channel = 'event') as had_event_sale
        from orders o
        join order_lines l on l.order_id = o.id and l.deleted_at is null
        left join locations loc on loc.id = o.location_id
        where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
          and o.placed_at >= :start and o.placed_at < :end
        group by 1, 2, 3, 4, 5
        """,
        {"tenant": ctx.tenant_id, "tz": ctx.timezone, "start": start, "end": end},
    )

    # Postgres counts Sunday as 0; Python counts Monday as 0, and so does the
    # staff_hours table. Converting here means one convention below this line.
    def weekday_of(dow: int) -> int:
        return (dow + 6) % 7

    buckets: dict[uuid.UUID | None, dict[tuple[int, int], list[tuple[date, Decimal, Decimal]]]]
    buckets = {}
    events: dict[uuid.UUID | None, dict[tuple[int, int], list[tuple[date, Decimal, Decimal]]]]
    events = {}
    names: dict[uuid.UUID | None, str] = {}
    event_day_count: set[date] = set()

    for row in rows:
        if location_id is not None and row.location_id != location_id:
            continue
        names[row.location_id] = row.location_name
        key = (weekday_of(row.dow), int(row.hour))
        entry = (row.day, Decimal(row.orders), Decimal(row.net_sales or 0))
        is_event = row.day in tagged or bool(row.had_event_sale)
        target = events if is_event else buckets
        target.setdefault(row.location_id, {}).setdefault(key, []).append(entry)
        if is_event:
            event_day_count.add(row.day)

    def to_cells(
        grid: dict[tuple[int, int], list[tuple[date, Decimal, Decimal]]],
    ) -> list[Cell]:
        return [
            Cell(
                weekday=weekday,
                hour=hour,
                orders=sum((entry[1] for entry in entries), Decimal("0")),
                net_sales=sum((entry[2] for entry in entries), Decimal("0")),
                observations=len({entry[0] for entry in entries}),
            )
            for (weekday, hour), entries in sorted(grid.items())
        ]

    maps = [
        Heatmap(
            location_id=where,
            location_name=names.get(where, "Unknown location"),
            weeks=weeks,
            period=period,
            cells=to_cells(grid),
            event_days_excluded=len(event_day_count),
            event_cells=to_cells(events.get(where, {})) if include_events else [],
        )
        for where, grid in buckets.items()
    ]
    maps.sort(key=lambda m: sum(c.orders for c in m.cells), reverse=True)
    return maps


# --------------------------------------------------------------------------
# Staff hours
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Shift:
    id: uuid.UUID | None
    location_id: uuid.UUID | None
    weekday: int
    start_time: time
    end_time: time
    staff_count: int
    note: str | None = None

    def covers(self, hour: int) -> bool:
        return self.start_time.hour <= hour < self.end_time.hour


async def shifts(
    session: AsyncSession, ctx: AnalyticsContext, *, location_id: uuid.UUID | None = None
) -> list[Shift]:
    query = select(t.StaffHours).where(t.StaffHours.tenant_id == ctx.tenant_id)
    if location_id is not None:
        query = query.where(t.StaffHours.location_id == location_id)
    rows = (
        (await session.execute(query.order_by(t.StaffHours.weekday, t.StaffHours.start_time)))
        .scalars()
        .all()
    )
    return [
        Shift(
            id=row.id,
            location_id=row.location_id,
            weekday=row.weekday,
            start_time=row.start_time,
            end_time=row.end_time,
            staff_count=row.staff_count,
            note=row.note,
        )
        for row in rows
    ]


async def set_shift(
    session: AsyncSession,
    ctx: AnalyticsContext,
    *,
    weekday: int,
    start_time: time,
    end_time: time,
    staff_count: int,
    location_id: uuid.UUID | None = None,
    note: str | None = None,
) -> uuid.UUID:
    """Add or replace one slot in the week's rota."""
    table = table_of(t.StaffHours)
    row_id = uuid.uuid4()
    statement = insert(table).values(
        id=row_id,
        tenant_id=ctx.tenant_id,
        location_id=location_id,
        weekday=weekday,
        start_time=start_time,
        end_time=end_time,
        staff_count=staff_count,
        note=note,
    )
    result = await session.execute(
        statement.on_conflict_do_update(
            index_elements=["tenant_id", "location_id", "weekday", "start_time"],
            set_={
                "end_time": statement.excluded.end_time,
                "staff_count": statement.excluded.staff_count,
                "note": statement.excluded.note,
            },
        ).returning(table.c.id)
    )
    await session.commit()
    return uuid.UUID(str(result.scalar_one()))


async def delete_shift(session: AsyncSession, ctx: AnalyticsContext, shift_id: uuid.UUID) -> None:
    table = table_of(t.StaffHours)
    await session.execute(
        table.delete().where(table.c.id == shift_id, table.c.tenant_id == ctx.tenant_id)
    )
    await session.commit()


# --------------------------------------------------------------------------
# Observations
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Observation:
    """One thing worth noticing about the week. Never an instruction."""

    kind: str  # "quiet", "busy", "dead_open", "dead_close"
    weekday: int
    hours: tuple[int, ...]
    orders_per_hour: Decimal
    staff_count: int | None
    sentence: str
    # Only when the owner has entered an hourly labour cost.
    labour_cost: Decimal | None = None

    @property
    def weekday_name(self) -> str:
        return WEEKDAY_NAMES[self.weekday]


def observe(
    grid: Heatmap, rota: list[Shift], *, hourly_labour_cost: Decimal | None = None
) -> list[Observation]:
    """Compare the tills against the rota and say what stands out."""
    out: list[Observation] = []
    by_weekday: dict[int, list[Shift]] = {}
    for shift in rota:
        by_weekday.setdefault(shift.weekday, []).append(shift)

    for weekday, day_shifts in sorted(by_weekday.items()):
        for shift in day_shifts:
            hours = tuple(range(shift.start_time.hour, shift.end_time.hour))
            covered = [grid.cell(weekday, hour) for hour in hours]
            observed = [cell for cell in covered if cell is not None]
            if len(hours) < 1 or not observed:
                continue
            occurrences = max((cell.observations for cell in observed), default=0)
            if occurrences < MIN_HOURS_OBSERVED // 2:
                continue

            total_orders = sum((cell.orders_per_occurrence for cell in observed), Decimal("0"))
            per_hour = (total_orders / Decimal(len(hours))).quantize(Decimal("0.1"))
            per_staff = (per_hour / Decimal(shift.staff_count)).quantize(Decimal("0.1"))

            span = f"{shift.start_time:%H}–{shift.end_time:%H}"
            if per_staff <= QUIET_ORDERS_PER_STAFF:
                cost = (
                    (hourly_labour_cost * Decimal(shift.staff_count) * Decimal(len(hours)))
                    .quantize(Decimal("0.01"))
                    if hourly_labour_cost
                    else None
                )
                sentence = (
                    f"{WEEKDAY_NAMES[weekday]}s {span} averaged {per_hour} orders an hour over "
                    f"{grid.weeks} weeks, with {shift.staff_count} on."
                )
                if cost is not None:
                    sentence += f" That stretch costs about ${cost} in wages each week."
                out.append(
                    Observation(
                        kind="quiet",
                        weekday=weekday,
                        hours=hours,
                        orders_per_hour=per_hour,
                        staff_count=shift.staff_count,
                        sentence=sentence,
                        labour_cost=cost,
                    )
                )
            elif per_staff >= BUSY_ORDERS_PER_STAFF:
                out.append(
                    Observation(
                        kind="busy",
                        weekday=weekday,
                        hours=hours,
                        orders_per_hour=per_hour,
                        staff_count=shift.staff_count,
                        sentence=(
                            f"{WEEKDAY_NAMES[weekday]}s {span} averaged {per_hour} orders an "
                            f"hour with {shift.staff_count} on — {per_staff} each."
                        ),
                    )
                )

    out.extend(_edge_hours(grid, rota))
    out.sort(key=lambda o: (o.kind != "busy", -(o.labour_cost or Decimal("0"))))
    return out


def _edge_hours(grid: Heatmap, rota: list[Shift]) -> list[Observation]:
    """Opening and closing hours that consistently see almost nothing."""
    out: list[Observation] = []
    by_weekday: dict[int, list[Shift]] = {}
    for shift in rota:
        by_weekday.setdefault(shift.weekday, []).append(shift)

    for weekday, day_shifts in sorted(by_weekday.items()):
        opening = min(day_shifts, key=lambda s: s.start_time)
        closing = max(day_shifts, key=lambda s: s.end_time)
        for label, hour in (
            ("dead_open", opening.start_time.hour),
            ("dead_close", closing.end_time.hour - 1),
        ):
            cell = grid.cell(weekday, hour)
            if cell is None or cell.observations < MIN_HOURS_OBSERVED:
                continue
            if cell.orders_per_occurrence > Decimal("0.5"):
                continue
            when = "first hour" if label == "dead_open" else "last hour"
            out.append(
                Observation(
                    kind=label,
                    weekday=weekday,
                    hours=(hour,),
                    orders_per_hour=cell.orders_per_occurrence,
                    staff_count=None,
                    sentence=(
                        f"The {when} on {WEEKDAY_NAMES[weekday]}s ({hour:02d}:00) averaged "
                        f"{cell.orders_per_occurrence} orders across "
                        f"{cell.observations} weeks."
                    ),
                )
            )
    return out


async def busiest_hours(
    session: AsyncSession,
    ctx: AnalyticsContext,
    *,
    location_id: uuid.UUID | None = None,
    weekday: int | None = None,
    as_of: date | None = None,
    limit: int = 8,
) -> list[dict[str, object]]:
    """The busiest weekday-hours, for the agent and the screen alike."""
    maps = await heatmap(session, ctx, location_id=location_id, as_of=as_of)
    cells: list[tuple[Heatmap, Cell]] = [
        (grid, cell)
        for grid in maps
        for cell in grid.cells
        if weekday is None or cell.weekday == weekday
    ]
    cells.sort(key=lambda pair: pair[1].orders_per_occurrence, reverse=True)
    return [
        {
            "location": grid.location_name,
            "weekday": WEEKDAY_NAMES[cell.weekday],
            "hour": f"{cell.hour:02d}:00",
            "orders_per_week": str(cell.orders_per_occurrence),
            "net_sales_per_week": str(cell.sales_per_occurrence),
            "weeks_observed": cell.observations,
        }
        for grid, cell in cells[:limit]
    ]
