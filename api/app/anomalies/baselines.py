"""What a normal Tuesday looks like here.

Everything in this package compares a day against its own weekday's history,
not against an average day: a shop where Saturday is four times Tuesday would
otherwise raise an alert every Tuesday morning and none on the Saturday it
actually lost money.

Robust statistics throughout — median and median absolute deviation rather than
mean and standard deviation. One convention weekend inside the window would
drag a mean up and inflate a standard deviation so far that nothing would ever
look unusual again. The median does not notice it, which is the point.

Event days are excluded from the baseline and reported separately. A shop that
does four conventions a year has four days that are not evidence about what a
normal Saturday is, and pretending otherwise makes every real Saturday look
like a collapse.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.context import AnalyticsContext, DateRange
from app.analytics.queries import fetch_all

# How many same-weekday observations sit behind a baseline. Eight weeks reaches
# back two months without crossing a season.
PERIODS = 8
# Below this, there is not enough history to say anything, and saying nothing
# is the correct output.
MIN_OBSERVATIONS = 4

# MAD scaled by this estimates a standard deviation for a normal distribution.
# The constant is what lets a robust z-score be read like an ordinary one.
MAD_TO_SIGMA = Decimal("1.4826")

# A floor on the spread, in dollars. Without it, a shop with four identical
# Tuesdays has a MAD of zero and the fifth Tuesday is infinitely unusual.
MIN_SPREAD = Decimal("25")


@dataclass(slots=True)
class DayObservation:
    """One shop-local day at one location."""

    day: date
    location_id: uuid.UUID | None
    location_name: str
    net_sales: Decimal
    orders: int
    units: Decimal
    refunds: Decimal
    discounts: Decimal
    is_event: bool = False


@dataclass(slots=True)
class Baseline:
    """What this weekday at this location usually looks like.

    The spread is kept per side because a shop's daily takings are skewed
    right. A Saturday can be double the usual; it cannot be less than nothing.
    So the days above the median are spread much wider than the days below it,
    and one figure averaged across both is the wrong yardstick for either.

    In practice this mostly buys quiet on the upside. Measured against a
    symmetric spread, an ordinary good Saturday at a shop like this reads as a
    two-sigma spike and gets reported every few weeks; measured against the
    other good Saturdays it is unremarkable, which is what it is. On the
    downside the two are close, because the low half is where a symmetric
    median-of-deviations tends to land anyway.

    Worth being clear about what this is *not* for: outliers. A median absolute
    deviation already ignores those. This is about the distribution not being
    symmetric in the first place.
    """

    location_id: uuid.UUID | None
    location_name: str
    weekday: int
    observations: int
    median_net_sales: Decimal
    # Spread of the days below the median, and of the days above it.
    spread_below: Decimal
    spread_above: Decimal
    median_orders: Decimal

    @property
    def usable(self) -> bool:
        return self.observations >= MIN_OBSERVATIONS

    @property
    def spread(self) -> Decimal:
        """The wider of the two sides, for anything that wants one number."""
        return max(self.spread_below, self.spread_above)

    def z(self, value: Decimal) -> Decimal:
        """How unusual a figure is, in robust deviations on its own side."""
        side = self.spread_below if value < self.median_net_sales else self.spread_above
        return ((value - self.median_net_sales) / max(side, MIN_SPREAD)).quantize(Decimal("0.01"))

    def difference(self, value: Decimal) -> Decimal:
        return (value - self.median_net_sales).quantize(Decimal("0.01"))


@dataclass(slots=True)
class BaselineSet:
    """Every baseline for one shop, plus the days they were built from."""

    as_of: date
    observations: list[DayObservation] = field(default_factory=list)
    baselines: dict[tuple[uuid.UUID | None, int], Baseline] = field(default_factory=dict)
    event_days: set[date] = field(default_factory=set)

    def for_day(self, day: date, location_id: uuid.UUID | None) -> Baseline | None:
        return self.baselines.get((location_id, day.weekday()))

    def observation(self, day: date, location_id: uuid.UUID | None) -> DayObservation | None:
        return next(
            (o for o in self.observations if o.day == day and o.location_id == location_id),
            None,
        )


def median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        return Decimal("0")
    middle = count // 2
    if count % 2:
        return ordered[middle]
    return ((ordered[middle - 1] + ordered[middle]) / 2).quantize(Decimal("0.01"))


def mad(values: list[Decimal], centre: Decimal | None = None) -> Decimal:
    """Median absolute deviation, scaled to read like a standard deviation."""
    if not values:
        return Decimal("0")
    middle = centre if centre is not None else median(values)
    return (median([abs(value - middle) for value in values]) * MAD_TO_SIGMA).quantize(
        Decimal("0.01")
    )


def one_sided_mad(values: list[Decimal], centre: Decimal, *, below: bool) -> Decimal:
    """The spread of just the days on one side of the median.

    Falls back to the two-sided figure when a side has almost nothing in it,
    which happens on a weekday the shop barely trades.
    """
    side = [value for value in values if (value < centre if below else value > centre)]
    if len(side) < 2:
        return mad(values, centre)
    return mad(side, centre)


async def build(
    session: AsyncSession,
    ctx: AnalyticsContext,
    *,
    as_of: date | None = None,
    weeks: int = PERIODS,
    judged_from: date | None = None,
) -> BaselineSet:
    """Pull the trailing history and reduce it to one baseline per weekday.

    `judged_from` is the first day a caller intends to *judge*. Those days are
    still observed, but they are kept out of the evidence, because a day that
    helps set the median it is then compared against cannot look unusual. With
    only eight observations behind a weekday, one bad Saturday inside its own
    baseline moves the median far enough to hide itself.
    """
    day = as_of or ctx.today()
    window = DateRange(day - timedelta(days=weeks * 7), day)
    start, end = window.bounds(ctx.tz)

    rows = await fetch_all(
        session,
        """
        with daily as (
            select (o.placed_at at time zone :tz)::date as day,
                   o.location_id as location_id,
                   sum(l.quantity * l.unit_price - l.discount) as net_sales,
                   sum(l.discount) as discounts,
                   sum(l.quantity) as units,
                   count(distinct o.id) as orders,
                   bool_or(o.channel = 'event') as had_event_sale
            from orders o
            join order_lines l on l.order_id = o.id and l.deleted_at is null
            where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
              and o.placed_at >= :start and o.placed_at < :end
            group by 1, 2
        ),
        refunded as (
            select (r.occurred_at at time zone :tz)::date as day,
                   o.location_id as location_id,
                   sum(r.amount) as refunds
            from refunds r
            join orders o on o.id = r.order_id
            where r.tenant_id = :tenant and r.deleted_at is null
              and r.occurred_at >= :start and r.occurred_at < :end
            group by 1, 2
        )
        select daily.day as day,
               daily.location_id as location_id,
               coalesce(loc.name, 'Unknown location') as location_name,
               daily.net_sales as net_sales,
               daily.discounts as discounts,
               daily.units as units,
               daily.orders as orders,
               daily.had_event_sale as had_event_sale,
               coalesce(refunded.refunds, 0) as refunds
        from daily
        left join locations loc on loc.id = daily.location_id
        left join refunded
               on refunded.day = daily.day
              and refunded.location_id is not distinct from daily.location_id
        order by daily.day
        """,
        {"tenant": ctx.tenant_id, "tz": ctx.timezone, "start": start, "end": end},
    )

    tagged = event_days(ctx)
    observations = [
        DayObservation(
            day=row.day,
            location_id=row.location_id,
            location_name=row.location_name,
            net_sales=Decimal(row.net_sales or 0),
            orders=int(row.orders or 0),
            units=Decimal(row.units or 0),
            refunds=Decimal(row.refunds or 0),
            discounts=Decimal(row.discounts or 0),
            is_event=row.day in tagged or bool(row.had_event_sale),
        )
        for row in rows
    ]

    evidence_ends = judged_from or day
    grouped: dict[tuple[uuid.UUID | None, int], list[DayObservation]] = {}
    for observation in observations:
        # Anything being judged is not evidence about itself.
        if observation.day >= evidence_ends or observation.is_event:
            continue
        grouped.setdefault((observation.location_id, observation.day.weekday()), []).append(
            observation
        )

    baselines: dict[tuple[uuid.UUID | None, int], Baseline] = {}
    for (location_id, weekday), days in grouped.items():
        sales = [o.net_sales for o in days]
        centre = median(sales)
        baselines[(location_id, weekday)] = Baseline(
            location_id=location_id,
            location_name=days[0].location_name,
            weekday=weekday,
            observations=len(days),
            median_net_sales=centre,
            spread_below=one_sided_mad(sales, centre, below=True),
            spread_above=one_sided_mad(sales, centre, below=False),
            median_orders=median([Decimal(o.orders) for o in days]),
        )

    return BaselineSet(
        as_of=day,
        observations=observations,
        baselines=baselines,
        event_days={o.day for o in observations if o.is_event},
    )


def event_days(ctx: AnalyticsContext) -> set[date]:
    """Days the owner has told us not to treat as normal.

    A list of ISO dates in tenant settings — conventions, the day the street
    was closed, the free comic book day. Deliberately owner-entered: no POS
    knows why a Saturday was strange, and a detector that guesses gets it
    wrong in both directions.
    """
    raw = ctx.setting("event_days", []) or []
    out: set[date] = set()
    for value in raw:
        try:
            out.add(date.fromisoformat(str(value)))
        except ValueError:
            continue
    return out
