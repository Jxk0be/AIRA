"""Stock that has stopped moving, graded, and what it might be bundled with.

The existing `dead_stock` metric answers "what has not sold in N days". That is
the right answer to a dashboard question and the wrong input to a rescue plan,
which needs to tell apart three different problems:

* **slowing** — still sells, but at half the rate it used to. Catchable.
* **stale** — nothing for two months. A markdown still works.
* **dead** — nothing for four. Only a bundle, a move or a return will clear it.

Cash tied up is the number that makes an owner act, and it is at cost wherever
a cost exists. Where none does, retail is reported instead and labelled, never
silently swapped in: "you have $1,500 sitting still" and "you have $1,500 of
retail tags sitting still" are different claims.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.context import AnalyticsContext, DateRange
from app.analytics.queries import Filters, fetch_all, stock_scope, with_ctes
from app.canonical.enums import StaleKind

NO_FILTERS = Filters()

# The grading thresholds, in days since the last sale.
STALE_AFTER_DAYS = 60
DEAD_AFTER_DAYS = 120
# A "slowing" item sells at less than this share of its own trailing-90 rate.
SLOWING_RATIO = Decimal("0.5")
SLOWING_WINDOW_DAYS = 30
TRAILING_WINDOW_DAYS = 90


@dataclass(slots=True)
class StaleItem:
    variant_id: uuid.UUID
    product_id: uuid.UUID
    label: str
    sku: str | None
    category: str | None
    category_id: uuid.UUID | None
    kind: StaleKind
    on_hand: Decimal
    price: Decimal | None
    cost: Decimal | None
    last_sold_at: datetime | None
    days_since_last_sale: int | None
    # Units a day over the last 30 days and over the trailing 90, so "half the
    # rate it used to be" is checkable rather than asserted.
    recent_daily: Decimal
    trailing_daily: Decimal
    never_sold: bool

    @property
    def cash_at_cost(self) -> Decimal | None:
        if self.cost is None:
            return None
        return (self.on_hand * self.cost).quantize(Decimal("0.01"))

    @property
    def cash_at_retail(self) -> Decimal:
        return (self.on_hand * (self.price or Decimal("0"))).quantize(Decimal("0.01"))

    @property
    def cash_tied_up(self) -> Decimal:
        """The figure to lead with: at cost where we know it, retail otherwise."""
        at_cost = self.cash_at_cost
        return at_cost if at_cost is not None else self.cash_at_retail


@dataclass(slots=True)
class StaleReport:
    as_of: date
    items: list[StaleItem]
    # How much of the money below is at cost rather than at retail tags.
    cost_coverage: Decimal | None

    @property
    def total_cash(self) -> Decimal:
        return sum((item.cash_tied_up for item in self.items), Decimal("0"))

    def of_kind(self, kind: StaleKind) -> list[StaleItem]:
        return [item for item in self.items if item.kind is kind]


async def stale_inventory(
    session: AsyncSession,
    ctx: AnalyticsContext,
    filters: Filters = NO_FILTERS,
    *,
    as_of: date | None = None,
    limit: int = 500,
) -> StaleReport:
    """Everything on the shelf that has stopped earning its space."""
    day = as_of or ctx.today()
    recent = DateRange(day - timedelta(days=SLOWING_WINDOW_DAYS - 1), day)
    trailing = DateRange(day - timedelta(days=TRAILING_WINDOW_DAYS - 1), day)
    recent_start, recent_end = recent.bounds(ctx.tz)
    trailing_start, trailing_end = trailing.bounds(ctx.tz)

    scope = stock_scope(ctx, filters)
    params: dict[str, object] = {
        **scope.params,
        "recent_start": recent_start,
        "recent_end": recent_end,
        "trailing_start": trailing_start,
        "trailing_end": trailing_end,
        "limit": limit,
    }

    statement = with_ctes(
        scope.cte,
        """recent_sales as (
            select l.variant_id as variant_id, sum(l.quantity) as units
            from orders o
            join order_lines l on l.order_id = o.id and l.deleted_at is null
            where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
              and o.placed_at >= :recent_start and o.placed_at < :recent_end
              and l.variant_id is not null
            group by 1
        )""",
        """trailing_sales as (
            select l.variant_id as variant_id, sum(l.quantity) as units
            from orders o
            join order_lines l on l.order_id = o.id and l.deleted_at is null
            where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
              and o.placed_at >= :trailing_start and o.placed_at < :trailing_end
              and l.variant_id is not null
            group by 1
        )""",
        """ever_sold as (
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
               p.id as product_id,
               p.name as product_name,
               v.name as variant_name,
               v.sku as sku,
               c.id as category_id,
               c.name as category,
               sum(i.on_hand) as on_hand,
               max(v.price) as price,
               max(v.cost) as cost,
               coalesce(max(recent_sales.units), 0) as recent_units,
               coalesce(max(trailing_sales.units), 0) as trailing_units,
               max(ever_sold.last_at) as last_sold_at
        {STOCK_ROWS}
        left join categories c on c.id = p.category_id
        left join recent_sales on recent_sales.variant_id = v.id
        left join trailing_sales on trailing_sales.variant_id = v.id
        left join ever_sold on ever_sold.variant_id = v.id
        where {scope.where}
        group by v.id, p.id, p.name, v.name, v.sku, c.id, c.name
        having sum(i.on_hand) > 0
        order by sum(i.on_hand) * coalesce(max(v.cost), max(v.price), 0) desc
        limit :limit
        """,
        params,
    )

    items: list[StaleItem] = []
    for row in rows:
        last_sold = row.last_sold_at
        days_since = (
            (day - last_sold.astimezone(ctx.tz).date()).days if last_sold is not None else None
        )
        recent_daily = (
            Decimal(row.recent_units) / Decimal(SLOWING_WINDOW_DAYS)
        ).quantize(Decimal("0.0001"))
        trailing_daily = (
            Decimal(row.trailing_units) / Decimal(TRAILING_WINDOW_DAYS)
        ).quantize(Decimal("0.0001"))

        kind = _grade(days_since, recent_daily, trailing_daily)
        if kind is None:
            continue

        label = row.product_name
        if row.variant_name and row.variant_name != row.product_name:
            label = f"{row.product_name} — {row.variant_name}"

        items.append(
            StaleItem(
                variant_id=row.variant_id,
                product_id=row.product_id,
                label=label,
                sku=row.sku,
                category=row.category,
                category_id=row.category_id,
                kind=kind,
                on_hand=Decimal(row.on_hand),
                price=Decimal(row.price) if row.price is not None else None,
                cost=Decimal(row.cost) if row.cost is not None else None,
                last_sold_at=last_sold,
                days_since_last_sale=days_since,
                recent_daily=recent_daily,
                trailing_daily=trailing_daily,
                never_sold=last_sold is None,
            )
        )

    priced = sum(1 for item in items if item.cost is not None)
    coverage = (
        (Decimal(priced) / Decimal(len(items))).quantize(Decimal("0.01")) if items else None
    )
    return StaleReport(as_of=day, items=items, cost_coverage=coverage)


def _grade(
    days_since: int | None, recent_daily: Decimal, trailing_daily: Decimal
) -> StaleKind | None:
    """Which of the three problems this is, or None if it is fine.

    Order matters: an item can be both stale and slowing, and the worse grade
    is the one that decides what to do about it.
    """
    if days_since is None or days_since >= DEAD_AFTER_DAYS:
        return StaleKind.DEAD
    if days_since >= STALE_AFTER_DAYS:
        return StaleKind.STALE
    if trailing_daily > 0 and recent_daily < trailing_daily * SLOWING_RATIO:
        return StaleKind.SLOWING
    return None


STOCK_ROWS = """
from inventory_levels i
join variants v on v.id = i.variant_id and v.deleted_at is null
join products p on p.id = v.product_id and p.deleted_at is null
"""


@dataclass(slots=True)
class CoPurchase:
    """Something that sells, bought in the same basket as something that does not."""

    variant_id: uuid.UUID
    label: str
    baskets: int
    units_sold_recently: Decimal
    price: Decimal | None


async def co_purchases(
    session: AsyncSession,
    ctx: AnalyticsContext,
    variant_ids: list[uuid.UUID],
    *,
    as_of: date | None = None,
    window_days: int = 365,
    limit_per_variant: int = 3,
) -> dict[uuid.UUID, list[CoPurchase]]:
    """What each of these was most often bought alongside, and still sells.

    The input to a bundle suggestion. Restricted to partners that are actually
    moving now — pairing dead stock with more dead stock is how a shop ends up
    with a shelf of bundles nobody buys.
    """
    if not variant_ids:
        return {}
    day = as_of or ctx.today()
    window = DateRange(day - timedelta(days=window_days - 1), day)
    recent = DateRange(day - timedelta(days=30), day)
    start, end = window.bounds(ctx.tz)
    recent_start, recent_end = recent.bounds(ctx.tz)

    rows = await fetch_all(
        session,
        """
        with pairs as (
            select mine.variant_id as target_id,
                   theirs.variant_id as partner_id,
                   count(distinct o.id) as baskets
            from orders o
            join order_lines mine on mine.order_id = o.id and mine.deleted_at is null
            join order_lines theirs on theirs.order_id = o.id and theirs.deleted_at is null
            where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
              and o.placed_at >= :start and o.placed_at < :end
              and mine.variant_id = any(:variant_ids)
              and theirs.variant_id is not null
              and theirs.variant_id <> mine.variant_id
            group by 1, 2
        ),
        still_selling as (
            select l.variant_id as variant_id, sum(l.quantity) as units
            from orders o
            join order_lines l on l.order_id = o.id and l.deleted_at is null
            where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
              and o.placed_at >= :recent_start and o.placed_at < :recent_end
              and l.variant_id is not null
            group by 1
            having sum(l.quantity) > 0
        )
        select pairs.target_id as target_id,
               pairs.partner_id as partner_id,
               pairs.baskets as baskets,
               still_selling.units as units,
               p.name as product_name,
               v.name as variant_name,
               v.price as price
        from pairs
        join still_selling on still_selling.variant_id = pairs.partner_id
        join variants v on v.id = pairs.partner_id and v.deleted_at is null
        join products p on p.id = v.product_id and p.deleted_at is null
        order by pairs.target_id, pairs.baskets desc, still_selling.units desc
        """,
        {
            "tenant": ctx.tenant_id,
            "start": start,
            "end": end,
            "recent_start": recent_start,
            "recent_end": recent_end,
            "variant_ids": list(dict.fromkeys(variant_ids)),
        },
    )

    out: dict[uuid.UUID, list[CoPurchase]] = {}
    for row in rows:
        partners = out.setdefault(row.target_id, [])
        if len(partners) >= limit_per_variant:
            continue
        label = row.product_name
        if row.variant_name and row.variant_name != row.product_name:
            label = f"{row.product_name} — {row.variant_name}"
        partners.append(
            CoPurchase(
                variant_id=row.partner_id,
                label=label,
                baskets=int(row.baskets),
                units_sold_recently=Decimal(row.units or 0),
                price=Decimal(row.price) if row.price is not None else None,
            )
        )
    return out


@dataclass(slots=True)
class LocationStrength:
    """How well one category sells at one location."""

    location_id: uuid.UUID
    location_name: str
    units: Decimal
    share: Decimal


async def category_by_location(
    session: AsyncSession,
    ctx: AnalyticsContext,
    *,
    as_of: date | None = None,
    window_days: int = 90,
) -> dict[uuid.UUID | None, list[LocationStrength]]:
    """Where each category actually sells, strongest location first.

    The input to a "move it" suggestion. Shares rather than raw units, because
    the main store outsells the con booth at everything and the interesting
    signal is what the booth outsells the main store *at*.
    """
    day = as_of or ctx.today()
    start, end = DateRange(day - timedelta(days=window_days - 1), day).bounds(ctx.tz)
    rows = await fetch_all(
        session,
        """
        select p.category_id as category_id,
               o.location_id as location_id,
               loc.name as location_name,
               sum(l.quantity) as units
        from orders o
        join order_lines l on l.order_id = o.id and l.deleted_at is null
        join variants v on v.id = l.variant_id
        join products p on p.id = v.product_id
        left join locations loc on loc.id = o.location_id
        where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
          and o.placed_at >= :start and o.placed_at < :end
          and o.location_id is not null
        group by 1, 2, 3
        """,
        {"tenant": ctx.tenant_id, "start": start, "end": end},
    )

    by_category: dict[uuid.UUID | None, list[LocationStrength]] = {}
    totals: dict[uuid.UUID | None, Decimal] = {}
    for row in rows:
        totals[row.category_id] = totals.get(row.category_id, Decimal("0")) + Decimal(row.units)
    for row in rows:
        total = totals[row.category_id] or Decimal("1")
        by_category.setdefault(row.category_id, []).append(
            LocationStrength(
                location_id=row.location_id,
                location_name=row.location_name or "Unknown location",
                units=Decimal(row.units),
                share=(Decimal(row.units) / total).quantize(Decimal("0.01")),
            )
        )
    for strengths in by_category.values():
        strengths.sort(key=lambda s: s.units, reverse=True)
    return by_category
