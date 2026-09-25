"""Assembling the month, and checking it against itself.

Every figure comes from the semantic layer. Nothing is added up here — the
module's whole job is to ask for the right things, in the right order, and to
be honest about the gaps.

The two reconciliations at the bottom are the reason a bookkeeper will trust
this. Net sales in the packet must equal net sales from the semantic layer to
the cent, because the day those two drift is the day the packet becomes a
second opinion rather than a report. And takings less refunds should land
within a few dollars of net sales plus tax plus tips; where they do not, the
gap is printed in the data notes instead of being absorbed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import analytics as an
from app.analytics import AnalyticsContext, DateRange
from app.analytics.finance import (
    FinancialSummary,
    InventorySnapshot,
    MonthEndChecks,
    Reconciliation,
    financial_summary,
    inventory_at,
    inventory_snapshot,
    last_sale_at,
)
from app.analytics.stale import stale_inventory

RANKED = 10

# Payments, tax and tips are recorded by different subsystems of a POS and
# rounded at different moments. A few dollars across a month is normal; a few
# hundred is a missing day.
RECONCILE_TOLERANCE = Decimal("5.00")


@dataclass(slots=True)
class Packet:
    """One month, assembled."""

    tenant_slug: str
    shop_name: str
    currency: str
    period: DateRange
    summary: an.SalesSummary
    finance: FinancialSummary
    margin: an.MarginReport | None
    by_category: an.Breakdown
    by_location: an.Breakdown | None
    by_channel: an.Breakdown
    opening_inventory: InventorySnapshot | None
    closing_inventory: InventorySnapshot
    best: an.Breakdown
    worst: list[dict[str, Any]] = field(default_factory=list)
    dead_stock_value: Decimal = Decimal("0")
    dead_stock_items: int = 0
    checks: MonthEndChecks = field(default_factory=MonthEndChecks)
    notes: list[str] = field(default_factory=list)
    # Net sales for just the registers whose takings we hold, when that is not
    # all of them. The takings reconciliation compares against this instead of the
    # shop total, so a shop running a till plus a marketplace export is not told
    # its books are short by the whole marketplace.
    takings_net_sales: Decimal | None = None
    # Per-register split of the month. One row for a single-register shop, which
    # is why the section can print unconditionally.
    by_source: an.Breakdown | None = None
    # Why there is no margin section, when the shop's setup says there should be.
    # Written for the reader, and printed in the notes in place of the coverage
    # line.
    margin_refusal: str | None = None

    @property
    def title(self) -> str:
        return f"{self.period.start:%B %Y} — {self.shop_name}"

    def figures(self) -> dict[str, Any]:
        """Everything the documents are built from, as plain JSON."""
        return {
            "shop": self.shop_name,
            "currency": self.currency,
            "period_start": self.period.start.isoformat(),
            "period_end": self.period.end.isoformat(),
            "sales": {
                "gross_sales": str(self.summary.gross_sales),
                "discounts": str(self.summary.discounts),
                "refunds": str(self.summary.refunds),
                "net_sales": str(self.summary.net_sales),
                "units": str(self.summary.units_sold),
                "orders": self.summary.order_count,
                "average_order_value": str(self.summary.average_order_value),
            },
            "tax_and_tips": {
                "tax_collected": str(self.finance.tax_collected),
                "tips": str(self.finance.tips),
            },
            "tenders": (
                [
                    {
                        "tender": line.tender,
                        "amount": str(line.amount),
                        "tips": str(line.tips),
                        "payments": line.payments,
                    }
                    for line in self.finance.tenders
                ]
                if self.finance.tenders is not None
                else None
            ),
            "margin": (
                {
                    "cogs": str(self.margin.cogs),
                    "covered_sales": str(self.margin.covered_sales),
                    "gross_profit": str(self.margin.gross_profit),
                    "gross_margin": (
                        str(self.margin.gross_margin)
                        if self.margin.gross_margin is not None
                        else None
                    ),
                    "cost_coverage": (
                        str(self.margin.cost_coverage)
                        if self.margin.cost_coverage is not None
                        else None
                    ),
                }
                if self.margin
                else None
            ),
            "by_category": _rows(self.by_category),
            "by_channel": _rows(self.by_channel),
            "by_location": _rows(self.by_location) if self.by_location else None,
            "by_source": _rows(self.by_source) if self.by_source else None,
            "inventory": {
                "opening": _snapshot(self.opening_inventory),
                "closing": _snapshot(self.closing_inventory),
            },
            "best_sellers": _rows(self.best),
            "worst_sellers": self.worst,
            "dead_stock": {
                "value": str(self.dead_stock_value),
                "items": self.dead_stock_items,
            },
            "reconciliation": [
                {
                    "name": check.name,
                    "left": str(check.left),
                    "right": str(check.right),
                    "gap": str(check.gap),
                    "ok": check.ok,
                    "note": check.note,
                }
                for check in self.checks.checks
            ],
            "notes": self.notes,
        }


def _rows(breakdown: an.Breakdown | None) -> list[dict[str, Any]]:
    if breakdown is None:
        return []
    return [
        {
            "label": row.label,
            "net_sales": str(row.net_sales),
            "units": str(row.units_sold),
            "orders": row.order_count,
            "share": str(row.share_of_net_sales) if row.share_of_net_sales else None,
        }
        for row in breakdown.rows
    ]


def _snapshot(snapshot: InventorySnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "at": snapshot.at.isoformat(),
        "units": str(snapshot.units),
        "at_cost": str(snapshot.at_cost) if snapshot.at_cost is not None else None,
        "at_retail": str(snapshot.at_retail),
        "cost_coverage": (
            str(snapshot.cost_coverage) if snapshot.cost_coverage is not None else None
        ),
        "reconstructed": snapshot.reconstructed,
        "note": snapshot.note,
    }


async def build(session: AsyncSession, ctx: AnalyticsContext, period: DateRange) -> Packet:
    """Assemble one month."""
    summary = await an.sales_summary(session, ctx, period)
    finance = await financial_summary(session, ctx, period)

    margin = None
    margin_refusal: str | None = None
    if ctx.has("has_costs"):
        try:
            margin = await an.margin_report(session, ctx, period)
        except an.CapabilityUnavailable as refused:
            # Declared but unusable *for this month*: costs exist somewhere in the
            # shop's setup, and nothing that actually sold in these weeks had one.
            #
            # A two-register shop makes this ordinary rather than exotic.
            # `has_costs` is the union across registers, so it is true because of
            # the till even in a month whose sales all came through a marketplace
            # export that has never heard of a cost. Letting the refusal escape
            # would fail the whole packet — every reconciliation, every section —
            # over a missing optional figure.
            margin_refusal = refused.message

    by_category = await an.category_breakdown(session, ctx, period, limit=50)
    by_channel = await an.channel_breakdown(session, ctx, period)
    by_location = (
        await an.location_breakdown(session, ctx, period) if ctx.has("multi_location") else None
    )
    # Unconditional: a one-register shop gets one row that equals its net sales,
    # and a bookkeeper looking at a two-register shop gets the split they would
    # otherwise assemble by hand from two portals.
    by_source = await an.source_breakdown(session, ctx, period)

    # When takings only cover some of the registers, the reconciliation needs the
    # matching subset of sales rather than the shop total.
    takings_net_sales = None
    if finance.payment_sources and not finance.payments_cover_everything:
        covered = await an.sales_summary(
            session, ctx, period, an.Filters(sources=finance.payment_sources)
        )
        takings_net_sales = covered.net_sales

    # One ranked pull, cut two ways, so "best" and "worst" cannot disagree
    # about what anything sold.
    ranked = await an.top_products(session, ctx, period, limit=200)
    best = await an.top_products(session, ctx, period, limit=RANKED)
    worst = _worst(ranked)

    closing = await inventory_snapshot(session, ctx, at=period.end)
    opening = await inventory_at(session, ctx, period.start)

    stale = await stale_inventory(session, ctx, as_of=period.end)

    packet = Packet(
        tenant_slug=ctx.slug,
        shop_name=ctx.name,
        currency=ctx.currency,
        period=period,
        summary=summary,
        finance=finance,
        margin=margin,
        by_category=by_category,
        by_location=by_location,
        by_channel=by_channel,
        opening_inventory=opening,
        closing_inventory=closing,
        best=best,
        worst=worst,
        dead_stock_value=stale.total_cash,
        dead_stock_items=len(stale.items),
        takings_net_sales=takings_net_sales,
        by_source=by_source,
        margin_refusal=margin_refusal,
    )
    packet.checks = _reconcile(packet)
    packet.notes = await _notes(session, ctx, packet)
    return packet


def _worst(ranked: an.Breakdown) -> list[dict[str, Any]]:
    """The ten weakest products that still sold something.

    Products that sold nothing at all are a dead-stock question and have their
    own section; this is about what sold badly, which is a different problem
    with a different answer.
    """
    tail = sorted(
        (row for row in ranked.rows if row.units_sold > 0), key=lambda row: row.net_sales
    )[:RANKED]
    return [
        {"label": row.label, "net_sales": str(row.net_sales), "units": str(row.units_sold)}
        for row in tail
    ]


def _reconcile(packet: Packet) -> MonthEndChecks:
    """The two checks that decide whether this packet can be trusted."""
    checks = MonthEndChecks()

    # Gross - discounts - refunds must be net sales, to the cent. This is the
    # packet agreeing with the semantic layer about its own headline figure.
    derived = (
        packet.summary.gross_sales - packet.summary.discounts - packet.summary.refunds
    ).quantize(Decimal("0.01"))
    checks.checks.append(
        Reconciliation(
            name="Net sales adds up",
            left=packet.summary.net_sales,
            right=derived,
            tolerance=Decimal("0.01"),
            note="gross sales less discounts less refunds",
        )
    )

    # Takings less refunds against net sales plus tax plus tips. Only possible
    # where the source reports payments separately from orders.
    if packet.finance.payments_total is not None:
        # Against the registers the takings actually cover, which is the whole
        # shop unless one of its systems does not report payments at all.
        against = (
            packet.summary.net_sales
            if packet.takings_net_sales is None
            else packet.takings_net_sales
        )
        expected = (against + packet.finance.tax_collected + packet.finance.tips).quantize(
            Decimal("0.01")
        )
        # Tips are added back: a payment's `amount` is the sale without its
        # tip, so takings that leave them out fall short by exactly the
        # month's tips and the packet reports a shop's books as not balancing
        # when they do.
        taken = (
            packet.finance.payments_total
            + (packet.finance.tips_taken or Decimal("0"))
            - packet.summary.refunds
        ).quantize(Decimal("0.01"))
        checks.checks.append(
            Reconciliation(
                name="Takings match sales",
                left=taken,
                right=expected,
                tolerance=RECONCILE_TOLERANCE,
                note="payments and tips less refunds, against net sales plus tax plus tips",
            )
        )
    return checks


async def _notes(session: AsyncSession, ctx: AnalyticsContext, packet: Packet) -> list[str]:
    """Everything a reader has to know for these figures to mean what they say."""
    notes: list[str] = [caveat.message for caveat in packet.summary.caveats]

    if packet.margin is None:
        notes.append(
            packet.margin_refusal
            or (
                "No costs are recorded in this shop's system, so there is no cost of goods "
                "or margin in this packet."
            )
        )
    elif packet.margin.cost_coverage is not None and packet.margin.cost_coverage < 1:
        notes.append(
            f"Costs are recorded for {packet.margin.cost_coverage:.0%} of the month's sales. "
            "Margin is worked out over that part only, never spread across the rest."
        )

    if packet.finance.tenders is None:
        notes.append(
            "This shop's system does not report payments separately from sales, so takings "
            "cannot be split into card and cash, and the takings reconciliation was not run."
        )

    if packet.opening_inventory is None:
        notes.append(
            "No stock history is available, so only the closing inventory value is shown. "
            "The opening value would have to be reconstructed from movements this system "
            "does not keep."
        )
    elif packet.opening_inventory.note:
        notes.append(f"Opening inventory: {packet.opening_inventory.note}")

    if packet.closing_inventory.note:
        notes.append(f"Closing inventory: {packet.closing_inventory.note}")

    last = await last_sale_at(session, ctx)
    if last is not None:
        last_day = last.astimezone(ctx.tz).date()
        if last_day < packet.period.end:
            notes.append(
                f"The most recent sale we hold is from {last_day.isoformat()}, which is "
                f"before the end of this period. Anything after that is missing, not zero."
            )

    for failure in packet.checks.failures:
        notes.append(
            f"{failure.name}: {failure.note} came to ${failure.right:,.2f} against "
            f"${failure.left:,.2f} — a gap of ${abs(failure.gap):,.2f}. Left in rather than "
            "adjusted out."
        )

    notes.append(
        "Figures are as recorded in the shop's point-of-sale system. Tax shown is what was "
        "collected, not what is owed."
    )
    return notes


def month_before(day: date) -> DateRange:
    """The month that has just finished. Called on the 1st, means last month."""
    end = day.replace(day=1) - timedelta(days=1)
    return DateRange(end.replace(day=1), end)
