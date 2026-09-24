"""The figures a bookkeeper asks for, which a dashboard never does.

Tax as recorded, tips, takings split by tender, and what the shelves were worth
at the start and the end of a month. None of it is interesting to a shop owner
day to day and all of it is the reason they will keep the subscription through
a quiet quarter: it is the thing they otherwise spend the first of the month
assembling by hand.

Two rules run through the whole module.

**Report, never advise.** `tax_collected` is what the POS recorded, full stop.
Whether that is what is owed is a question for somebody licensed to answer it.

**Reconstruct honestly or not at all.** Opening inventory value is worked
backwards from today's shelf through the movement history, which is only
possible where a source keeps one. Where it does not, the opening value is
None and the packet says so rather than showing a plausible number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.context import AnalyticsContext, DateRange
from app.analytics.queries import Filters, fetch_all, fetch_one
from app.canonical.enums import Tender

NO_FILTERS = Filters()


@dataclass(slots=True)
class TenderLine:
    tender: str
    amount: Decimal
    tips: Decimal
    payments: int


@dataclass(slots=True)
class FinancialSummary:
    """A month, as the books see it."""

    period: DateRange
    tax_collected: Decimal
    tips: Decimal
    # Null when the source does not report payments separately from orders.
    tenders: list[TenderLine] | None = None
    payments_total: Decimal | None = None

    @property
    def tenders_available(self) -> bool:
        return self.tenders is not None


async def financial_summary(
    session: AsyncSession,
    ctx: AnalyticsContext,
    period: DateRange,
    filters: Filters = NO_FILTERS,
) -> FinancialSummary:
    """Tax, tips and takings by tender, all as the POS recorded them."""
    start, end = period.bounds(ctx.tz)
    params = {"tenant": ctx.tenant_id, "start": start, "end": end}

    row = await fetch_one(
        session,
        """
        select coalesce(sum(o.tax_total), 0) as tax,
               coalesce(sum(o.tip_total), 0) as tips
        from orders o
        where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
          and o.placed_at >= :start and o.placed_at < :end
        """,
        params,
    )
    summary = FinancialSummary(
        period=period,
        tax_collected=Decimal(row.tax or 0),
        tips=Decimal(row.tips or 0),
    )

    if not ctx.has("has_payments"):
        return summary

    tenders = await fetch_all(
        session,
        """
        select p.tender as tender,
               coalesce(sum(p.amount), 0) as amount,
               coalesce(sum(p.tip), 0) as tips,
               count(*) as payments
        from payments p
        join orders o on o.id = p.order_id
        where p.tenant_id = :tenant and p.deleted_at is null
          and o.deleted_at is null and o.status <> 'canceled'
          and p.occurred_at >= :start and p.occurred_at < :end
        group by 1
        order by 2 desc
        """,
        params,
    )
    summary.tenders = [
        TenderLine(
            tender=str(row.tender),
            amount=Decimal(row.amount or 0),
            tips=Decimal(row.tips or 0),
            payments=int(row.payments),
        )
        for row in tenders
    ]
    summary.payments_total = sum(
        (line.amount for line in summary.tenders), Decimal("0")
    )
    return summary


@dataclass(slots=True)
class InventorySnapshot:
    """What the shelves were worth at one instant."""

    at: date
    units: Decimal
    at_cost: Decimal | None
    at_retail: Decimal
    cost_coverage: Decimal | None
    # True when this was reconstructed from movement history rather than read
    # off a current stock level.
    reconstructed: bool = False
    note: str | None = None


async def inventory_snapshot(
    session: AsyncSession, ctx: AnalyticsContext, *, at: date | None = None
) -> InventorySnapshot:
    """The current shelf, valued. The "closing" half of a month-end packet."""
    day = at or ctx.today()
    row = await fetch_one(
        session,
        """
        select coalesce(sum(i.on_hand), 0) as units,
               coalesce(sum(i.on_hand * v.price), 0) as retail,
               coalesce(sum(case when v.cost is not null then i.on_hand * v.cost end), 0) as cost,
               coalesce(sum(case when v.cost is not null then i.on_hand * v.price end), 0)
                 as retail_with_cost,
               count(*) filter (where v.cost is not null) as priced,
               count(*) as rows_counted
        from inventory_levels i
        join variants v on v.id = i.variant_id and v.deleted_at is null
        join products p on p.id = v.product_id and p.deleted_at is null
        where i.tenant_id = :tenant and i.deleted_at is null
        """,
        {"tenant": ctx.tenant_id},
    )
    counted = int(row.rows_counted or 0)
    coverage = (
        (Decimal(int(row.priced)) / Decimal(counted)).quantize(Decimal("0.01"))
        if counted
        else None
    )
    return InventorySnapshot(
        at=day,
        units=Decimal(row.units or 0),
        at_cost=Decimal(row.cost or 0) if row.priced else None,
        at_retail=Decimal(row.retail or 0),
        cost_coverage=coverage,
        reconstructed=False,
        note="Stock as of the last sync, not as of midnight on the date shown.",
    )


async def inventory_at(
    session: AsyncSession, ctx: AnalyticsContext, at: date
) -> InventorySnapshot | None:
    """The shelf as it stood on a past date, if the history supports it.

    Worked backwards from today through every recorded movement. Returns None
    — rather than a guess — when the shop's system keeps no movement history,
    which is the common case and is reported as a data note.
    """
    if not ctx.has("has_inventory_history"):
        return None

    since, _ = DateRange(at, ctx.today()).bounds(ctx.tz)
    row = await fetch_one(
        session,
        """
        with moves as (
            select m.variant_id as variant_id,
                   sum(case
                         when m.kind in ('received', 'returned') then m.quantity
                         when m.kind in ('sold', 'damaged') then -m.quantity
                         else 0
                       end) as net_change,
                   bool_or(m.kind in ('adjusted', 'counted', 'transferred')) as unsigned_moves
            from inventory_movements m
            where m.tenant_id = :tenant and m.deleted_at is null and m.occurred_at >= :since
            group by 1
        ),
        rewound as (
            select v.id as variant_id,
                   v.price as price,
                   v.cost as cost,
                   coalesce(sum(i.on_hand), 0) - coalesce(max(moves.net_change), 0) as on_hand,
                   bool_or(coalesce(moves.unsigned_moves, false)) as approximate
            from variants v
            join products p on p.id = v.product_id and p.deleted_at is null
            left join inventory_levels i
                   on i.variant_id = v.id and i.deleted_at is null
            left join moves on moves.variant_id = v.id
            where v.tenant_id = :tenant and v.deleted_at is null
            group by v.id, v.price, v.cost
        )
        select coalesce(sum(greatest(on_hand, 0)), 0) as units,
               coalesce(sum(greatest(on_hand, 0) * price), 0) as retail,
               coalesce(sum(case when cost is not null
                                 then greatest(on_hand, 0) * cost end), 0) as cost,
               count(*) filter (where cost is not null) as priced,
               count(*) as rows_counted,
               bool_or(approximate) as approximate
        from rewound
        """,
        {"tenant": ctx.tenant_id, "since": since},
    )

    counted = int(row.rows_counted or 0)
    coverage = (
        (Decimal(int(row.priced)) / Decimal(counted)).quantize(Decimal("0.01"))
        if counted
        else None
    )
    note = (
        "Rebuilt from stock movements. Some movements in the period have no reliable "
        "direction (counts and transfers), so treat this as close rather than exact."
        if row.approximate
        else "Rebuilt from stock movements since this date."
    )
    return InventorySnapshot(
        at=at,
        units=Decimal(row.units or 0),
        at_cost=Decimal(row.cost or 0) if row.priced else None,
        at_retail=Decimal(row.retail or 0),
        cost_coverage=coverage,
        reconstructed=True,
        note=note,
    )


@dataclass(slots=True)
class Reconciliation:
    """Whether the packet's own figures agree with each other.

    A gap that is listed is a gap the bookkeeper can chase. A gap that is
    quietly absorbed is a gap that turns up in an audit.
    """

    name: str
    left: Decimal
    right: Decimal
    tolerance: Decimal
    note: str = ""

    @property
    def gap(self) -> Decimal:
        return (self.left - self.right).quantize(Decimal("0.01"))

    @property
    def ok(self) -> bool:
        return abs(self.gap) <= self.tolerance


@dataclass(slots=True)
class MonthEndChecks:
    checks: list[Reconciliation] = field(default_factory=list)

    @property
    def failures(self) -> list[Reconciliation]:
        return [check for check in self.checks if not check.ok]

    @property
    def clean(self) -> bool:
        return not self.failures


async def last_sale_at(session: AsyncSession, ctx: AnalyticsContext) -> datetime | None:
    row = await fetch_one(
        session,
        """
        select max(o.placed_at) as last_at
        from orders o
        where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
        """,
        {"tenant": ctx.tenant_id},
    )
    return row.last_at


def tender_label(tender: str) -> str:
    return {
        Tender.CARD.value: "Card",
        Tender.CASH.value: "Cash",
        Tender.OTHER.value: "Other",
    }.get(tender, tender.title())
