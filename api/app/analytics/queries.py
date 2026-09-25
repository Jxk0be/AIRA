"""The SQL every metric shares.

One place decides what counts as a sale, how a filter narrows it, and how a
timestamp becomes a shop-local day. Metrics compose these fragments instead of
writing their own WHERE clauses, because the moment two metrics disagree about
whether an open order counts, the dashboard and the agent start contradicting
each other and the shop stops believing either.

The SQL here is written by us, not by a model (CLAUDE.md rule 4). Everything a
caller supplies arrives as a bound parameter; the only text interpolated into a
statement comes from the fixed fragments in this module.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.context import AnalyticsContext, DateRange
from app.analytics.results import Caveat, Dimension, Grain
from app.canonical.enums import Channel

# How far back "how fast is this selling?" looks. Four weeks smooths out the
# weekend/weekday swing a shop lives by without reaching back to a different
# season.
VELOCITY_WINDOW_DAYS = 28


@dataclass(frozen=True, slots=True)
class Filters:
    """Optional narrowing, shared by every metric that can honour it.

    A filter that a metric cannot apply is never ignored silently: the metric
    either refuses or says so in a caveat.
    """

    location_ids: tuple[uuid.UUID, ...] = ()
    channels: tuple[Channel, ...] = ()
    category_ids: tuple[uuid.UUID, ...] = ()
    product_ids: tuple[uuid.UUID, ...] = ()
    variant_ids: tuple[uuid.UUID, ...] = ()
    # Source slugs, as stamped on every synced row. Narrows to one or more of the
    # tenant's registers: "how did the booth do?" on a shop whose booth is a
    # different system from the shop floor.
    sources: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (
            self.location_ids
            or self.channels
            or self.category_ids
            or self.product_ids
            or self.variant_ids
            or self.sources
        )

    @property
    def narrows_to_products(self) -> bool:
        """True when the filter picks out particular merchandise.

        Refunds are recorded against a whole order, so nothing that narrows to
        products can subtract them honestly.
        """
        return bool(self.category_ids or self.product_ids or self.variant_ids)


@dataclass
class Scope:
    """A ready-to-paste WHERE clause plus the parameters it binds."""

    where: str
    params: dict[str, Any] = field(default_factory=dict)
    cte: str = ""
    caveats: list[Caveat] = field(default_factory=list)

    def statement(self, body: str) -> str:
        return f"{self.cte}{body}"


# Every sales metric reads from exactly this shape, so a product filter and a
# category breakdown never need a different notion of "a sold line".
SOLD_LINES = """
from orders o
join order_lines l on l.order_id = o.id and l.deleted_at is null
left join variants v on v.id = l.variant_id
left join products p on p.id = v.product_id
"""

REFUND_ROWS = """
from refunds r
join orders o on o.id = r.order_id
"""

STOCK_ROWS = """
from inventory_levels i
join variants v on v.id = i.variant_id and v.deleted_at is null
join products p on p.id = v.product_id and p.deleted_at is null
"""

# A category filter means the category and everything filed under it: asking
# about "Trading Cards" has to include "TCG Singles" sitting beneath it.
CATEGORY_TREE = """
with recursive wanted_categories(id) as (
    select id from categories
    where tenant_id = :tenant and id = any(:category_ids)
  union all
    select c.id from categories c
    join wanted_categories w on c.parent_id = w.id
    where c.tenant_id = :tenant
)
"""

# Money lines. `gross` is before discounts, matching how a POS reports it.
GROSS = "coalesce(sum(l.quantity * l.unit_price), 0)"
DISCOUNTS = "coalesce(sum(l.discount), 0)"
UNITS = "coalesce(sum(l.quantity), 0)"
ORDERS = "count(distinct o.id)"


def _merchandise_filters(filters: Filters, params: dict[str, Any]) -> list[str]:
    clauses: list[str] = []
    if filters.category_ids:
        params["category_ids"] = list(filters.category_ids)
        clauses.append("p.category_id in (select id from wanted_categories)")
    if filters.product_ids:
        params["product_ids"] = list(filters.product_ids)
        clauses.append("p.id = any(:product_ids)")
    if filters.variant_ids:
        params["variant_ids"] = list(filters.variant_ids)
        clauses.append("v.id = any(:variant_ids)")
    return clauses


def sales_scope(
    ctx: AnalyticsContext,
    period: DateRange,
    filters: Filters,
    *,
    require_product: bool = False,
) -> Scope:
    """What counts as a sale in this period, for this shop, under these filters.

    Canceled orders are excluded outright and open ones are kept: an order that
    is still open on a Saturday night has been paid for and taken home, and the
    shop's own report counts it.
    """
    start, end = period.bounds(ctx.tz)
    params: dict[str, Any] = {"tenant": ctx.tenant_id, "start": start, "end": end}
    clauses = [
        "o.tenant_id = :tenant",
        "o.deleted_at is null",
        "o.status <> 'canceled'",
        "o.placed_at >= :start",
        "o.placed_at < :end",
    ]

    if filters.location_ids:
        params["location_ids"] = list(filters.location_ids)
        clauses.append("o.location_id = any(:location_ids)")
    if filters.sources:
        params["sources"] = list(filters.sources)
        clauses.append("o.source = any(:sources)")
    if filters.channels:
        params["channels"] = [c.value for c in filters.channels]
        clauses.append("o.channel = any(:channels)")
    clauses.extend(_merchandise_filters(filters, params))

    if require_product or filters.narrows_to_products:
        # A custom-amount line has no variant, so it cannot belong to any
        # product row and cannot be tested against a product filter.
        clauses.append("l.variant_id is not null")

    return Scope(
        where=" and ".join(clauses),
        params=params,
        cte=CATEGORY_TREE if filters.category_ids else "",
    )


def order_scope(ctx: AnalyticsContext, period: DateRange, filters: Filters) -> Scope:
    """The same period and filters, but counted per order rather than per line.

    A merchandise filter becomes "this order contained one of those", which is
    what a question like "how many people who bought manga came back?" means.
    """
    start, end = period.bounds(ctx.tz)
    params: dict[str, Any] = {"tenant": ctx.tenant_id, "start": start, "end": end}
    clauses = [
        "o.tenant_id = :tenant",
        "o.deleted_at is null",
        "o.status <> 'canceled'",
        "o.placed_at >= :start",
        "o.placed_at < :end",
    ]
    if filters.location_ids:
        params["location_ids"] = list(filters.location_ids)
        clauses.append("o.location_id = any(:location_ids)")
    if filters.sources:
        params["sources"] = list(filters.sources)
        clauses.append("o.source = any(:sources)")
    if filters.channels:
        params["channels"] = [c.value for c in filters.channels]
        clauses.append("o.channel = any(:channels)")

    merchandise = _merchandise_filters(filters, params)
    if merchandise:
        clauses.append(
            "exists (select 1 from order_lines l "
            "join variants v on v.id = l.variant_id "
            "join products p on p.id = v.product_id "
            "where l.order_id = o.id and l.deleted_at is null and "
            + " and ".join(merchandise)
            + ")"
        )

    return Scope(
        where=" and ".join(clauses),
        params=params,
        cte=CATEGORY_TREE if filters.category_ids else "",
    )


def refund_scope(ctx: AnalyticsContext, period: DateRange, filters: Filters) -> Scope:
    """Refunds landing in this period, by the day they were given back.

    Refunds attach to an order, not to a line, so the only filters that can
    apply are the ones an order carries: location and channel.
    """
    start, end = period.bounds(ctx.tz)
    params: dict[str, Any] = {"tenant": ctx.tenant_id, "start": start, "end": end}
    clauses = [
        "r.tenant_id = :tenant",
        "r.deleted_at is null",
        "o.deleted_at is null",
        "r.occurred_at >= :start",
        "r.occurred_at < :end",
    ]
    if filters.location_ids:
        params["location_ids"] = list(filters.location_ids)
        clauses.append("o.location_id = any(:location_ids)")
    if filters.sources:
        params["sources"] = list(filters.sources)
        clauses.append("o.source = any(:sources)")
    if filters.channels:
        params["channels"] = [c.value for c in filters.channels]
        clauses.append("o.channel = any(:channels)")

    caveats: list[Caveat] = []
    if filters.narrows_to_products:
        caveats.append(
            Caveat(
                code="refunds_not_attributable",
                message=(
                    "Refunds are recorded against a whole order rather than an item, so this "
                    "figure is before refunds."
                ),
            )
        )

    return Scope(where=" and ".join(clauses), params=params, caveats=caveats)


def stock_scope(ctx: AnalyticsContext, filters: Filters) -> Scope:
    """Current stock, under whichever filters make sense for a shelf.

    A channel filter does not: a copy of a book on the shelf is not online or
    in-store stock, it is just stock. Saying so beats quietly ignoring it.
    """
    params: dict[str, Any] = {"tenant": ctx.tenant_id}
    clauses = ["i.tenant_id = :tenant", "i.deleted_at is null"]

    if filters.location_ids:
        params["location_ids"] = list(filters.location_ids)
        clauses.append("i.location_id = any(:location_ids)")
    if filters.sources:
        params["sources"] = list(filters.sources)
        clauses.append("i.source = any(:sources)")
    clauses.extend(_merchandise_filters(filters, params))

    caveats: list[Caveat] = []
    if filters.channels:
        caveats.append(
            Caveat(
                code="channel_filter_ignored_for_stock",
                message=(
                    "Stock is counted per location, not per channel, so the channel filter "
                    "does not apply to these numbers."
                ),
            )
        )

    return Scope(
        where=" and ".join(clauses),
        params=params,
        cte=CATEGORY_TREE if filters.category_ids else "",
        caveats=caveats,
    )


def combine(*scopes: Scope) -> tuple[str, dict[str, Any], list[Caveat]]:
    """Merge scopes that will appear in one statement.

    They are built from the same tenant, period and filters, so identical
    parameter names always carry identical values; the category CTE is declared
    once however many scopes asked for it.
    """
    params: dict[str, Any] = {}
    caveats: list[Caveat] = []
    cte = ""
    for scope in scopes:
        params.update(scope.params)
        caveats.extend(scope.caveats)
        cte = cte or scope.cte
    return cte, params, caveats


def with_ctes(cte: str, *extra: str) -> str:
    """Add more CTEs to whatever a scope already declared.

    Appending to a `with recursive` list is legal and keeps every metric to a
    single statement, which is what makes a metric one round trip.
    """
    joined = ",\n".join(extra)
    if not joined:
        return cte
    if cte:
        return f"{cte.rstrip()},\n{joined}\n"
    return f"with {joined}\n"


@dataclass(frozen=True, slots=True)
class DimensionSql:
    key: str
    label: str
    join: str = ""
    # True when a line without a product cannot appear in this breakdown.
    needs_product: bool = False
    # True when refunds can be attributed to a row: an order carries its
    # location and channel, but not which item came back.
    refundable: bool = False


CHANNEL_LABEL = """
case o.channel
  when 'in_store' then 'In store'
  when 'online' then 'Online'
  when 'event' then 'Events and pop-ups'
  else 'Other'
end
"""

# A single-variant product usually names its one variant after itself, and
# "Harborline — Harborline" on a dashboard looks like a bug.
VARIANT_LABEL = """
case when v.name is null or v.name = p.name then p.name else p.name || ' — ' || v.name end
"""

# The shop's own name for the register, falling back to the slug we stamped. The
# join is on (tenant_id, source), which `integrations` is unique on.
SOURCE_LABEL = "coalesce(si.display_name, o.source)"

SOURCE_JOIN = "left join integrations si on si.tenant_id = o.tenant_id and si.source = o.source"

DIMENSION_SQL: dict[Dimension, DimensionSql] = {
    Dimension.PRODUCT: DimensionSql(key="p.id::text", label="p.name", needs_product=True),
    Dimension.VARIANT: DimensionSql(key="v.id::text", label=VARIANT_LABEL, needs_product=True),
    Dimension.CATEGORY: DimensionSql(
        key="c.id::text",
        label="coalesce(c.name, 'Uncategorised')",
        join="left join categories c on c.id = p.category_id",
        needs_product=True,
    ),
    Dimension.CHANNEL: DimensionSql(key="o.channel", label=CHANNEL_LABEL, refundable=True),
    Dimension.LOCATION: DimensionSql(
        key="o.location_id::text",
        label="coalesce(loc.name, 'Unknown location')",
        join="left join locations loc on loc.id = o.location_id",
        refundable=True,
    ),
    # Refundable, and this one is exact rather than approximate: a refund belongs
    # to an order, and an order came out of exactly one system. Consolidated net
    # sales split by register therefore adds up to the total, which is the whole
    # promise of the screen built on it.
    Dimension.SOURCE: DimensionSql(
        key="o.source", label=SOURCE_LABEL, join=SOURCE_JOIN, refundable=True
    ),
}

# date_trunc gives the first instant of the bucket in shop-local time; ::date
# then hands back the calendar day a human would name it by.
GRAIN_SQL: dict[Grain, str] = {
    Grain.DAY: "(o.placed_at at time zone :tz)::date",
    Grain.WEEK: "date_trunc('week', o.placed_at at time zone :tz)::date",
    Grain.MONTH: "date_trunc('month', o.placed_at at time zone :tz)::date",
}

REFUND_GRAIN_SQL: dict[Grain, str] = {
    Grain.DAY: "(r.occurred_at at time zone :tz)::date",
    Grain.WEEK: "date_trunc('week', r.occurred_at at time zone :tz)::date",
    Grain.MONTH: "date_trunc('month', r.occurred_at at time zone :tz)::date",
}


async def fetch_one(session: AsyncSession, sql: str, params: dict[str, Any]) -> Row[Any]:
    row = (await session.execute(text(sql), params)).first()
    assert row is not None  # every query here is an aggregate: always one row
    return row


async def fetch_all(session: AsyncSession, sql: str, params: dict[str, Any]) -> list[Row[Any]]:
    return list((await session.execute(text(sql), params)).all())


async def data_window(
    session: AsyncSession, ctx: AnalyticsContext
) -> tuple[datetime | None, datetime | None]:
    """First and last sale we hold for this shop, whatever the question was.

    Used to tell the difference between "you sold nothing last week" and "we
    have not synced anything since March".
    """
    row = await fetch_one(
        session,
        """
        select min(o.placed_at) as first_at, max(o.placed_at) as last_at
        from orders o
        where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
        """,
        {"tenant": ctx.tenant_id},
    )
    return row.first_at, row.last_at


def coverage_caveats(
    ctx: AnalyticsContext,
    period: DateRange,
    first_at: datetime | None,
    last_at: datetime | None,
) -> list[Caveat]:
    """Say so when the question reaches past the data we actually hold."""
    caveats: list[Caveat] = []
    if first_at is None or last_at is None:
        return [
            Caveat(
                code="no_sales_data",
                message=f"No sales have been synced for {ctx.name} yet.",
            )
        ]

    first_day = first_at.astimezone(ctx.tz).date()
    last_day = last_at.astimezone(ctx.tz).date()
    if period.end > last_day:
        caveats.append(
            Caveat(
                code="period_past_data",
                message=(
                    f"The most recent sale we hold is from {last_day.isoformat()}, so anything "
                    "after that is missing rather than zero."
                ),
            )
        )
    if period.start < first_day:
        caveats.append(
            Caveat(
                code="period_before_data",
                message=(
                    f"Our data starts on {first_day.isoformat()}; nothing before that was "
                    "part of this figure."
                ),
            )
        )
    return caveats


def velocity_window(ctx: AnalyticsContext, as_of: date | None = None) -> DateRange:
    """The window "how fast is this selling?" is measured over."""
    end = as_of or ctx.today()
    return DateRange(end - timedelta(days=VELOCITY_WINDOW_DAYS - 1), end)
