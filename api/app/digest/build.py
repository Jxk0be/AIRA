"""Everything Monday's email says, as data.

The digest is the feature that keeps a subscription, and it keeps it by being
short and true. Most owners will never open the dashboard; they will read four
lines on a phone before the shop opens.

Every figure here comes from the semantic layer or from an insight's stored
evidence. Nothing is computed in this module and nothing is computed in the
renderer, which is what lets the number validator be absolute: if a figure is
not in this payload, it cannot appear in the email.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import analytics as an
from app.analytics import AnalyticsContext, DateRange
from app.insights import StoredInsight, ValueLedger, top_open, value_ledger

# How many actions a digest carries. Three is what fits before somebody stops
# reading, and a fourth would cost the first three their weight.
TOP_ACTIONS = 3
TOP_PRODUCTS = 5


@dataclass(slots=True)
class Comparison:
    """One figure against what it was before."""

    label: str
    value: Decimal
    previous: Decimal | None
    # None when there is no prior figure to compare against, which is a
    # different statement from "flat".
    change: Decimal | None = None
    change_share: Decimal | None = None

    @classmethod
    def of(cls, label: str, value: Decimal, previous: Decimal | None) -> Comparison:
        if previous is None:
            return cls(label=label, value=value, previous=None)
        change = value - previous
        share = (change / previous).quantize(Decimal("0.001")) if previous else None
        return cls(label=label, value=value, previous=previous, change=change, change_share=share)

    @property
    def direction(self) -> str:
        if self.change is None:
            return "flat"
        if self.change > 0:
            return "up"
        if self.change < 0:
            return "down"
        return "flat"


@dataclass(slots=True)
class Digest:
    """A whole digest, before anybody has written a sentence about it."""

    tenant_slug: str
    shop_name: str
    week: DateRange
    currency: str
    net_sales: Comparison
    orders: Comparison
    average_order_value: Comparison
    units: Decimal
    year_ago: Comparison | None
    actions: list[StoredInsight] = field(default_factory=list)
    top_products: list[dict[str, Any]] = field(default_factory=list)
    notable: list[str] = field(default_factory=list)
    ledger: ValueLedger | None = None
    caveats: list[str] = field(default_factory=list)

    @property
    def has_value_to_report(self) -> bool:
        return self.ledger is not None and self.ledger.has_anything_to_show

    def payload(self) -> dict[str, Any]:
        """Exactly the facts a model may write from. Nothing else exists to it."""
        return {
            "shop": self.shop_name,
            "week_start": self.week.start.isoformat(),
            "week_end": self.week.end.isoformat(),
            "net_sales": str(self.net_sales.value),
            "net_sales_last_week": (
                str(self.net_sales.previous) if self.net_sales.previous is not None else None
            ),
            "net_sales_change": (
                str(self.net_sales.change) if self.net_sales.change is not None else None
            ),
            "net_sales_change_percent": (
                str((self.net_sales.change_share * 100).quantize(Decimal("0.1")))
                if self.net_sales.change_share is not None
                else None
            ),
            "net_sales_year_ago": (
                str(self.year_ago.previous)
                if self.year_ago is not None and self.year_ago.previous is not None
                else None
            ),
            "orders": str(self.orders.value),
            "orders_last_week": (
                str(self.orders.previous) if self.orders.previous is not None else None
            ),
            "average_order_value": str(self.average_order_value.value),
            "units": str(self.units),
            "actions": [
                {
                    "title": insight.title,
                    "summary": insight.summary,
                    "dollar_impact": (
                        str(insight.dollar_impact) if insight.dollar_impact is not None else None
                    ),
                    "kind": insight.kind,
                }
                for insight in self.actions
            ],
            "top_products": self.top_products,
            "notable": self.notable,
            "value_this_month": (
                {
                    "attributed_revenue": str(self.ledger.attributed_revenue),
                    "cash_recovered": str(self.ledger.cash_recovered),
                    "flagged": str(self.ledger.flagged_impact),
                    "acted_on": self.ledger.insights_acted,
                }
                if self.has_value_to_report and self.ledger
                else None
            ),
            "caveats": self.caveats,
        }


async def build(
    session: AsyncSession, ctx: AnalyticsContext, *, as_of: date | None = None
) -> Digest:
    """Gather last week's figures, the open actions, and what moved."""
    day = as_of or ctx.today()
    week = last_full_week(day)
    previous = week.previous()
    year_ago = DateRange(week.start - timedelta(days=364), week.end - timedelta(days=364))

    summary = await an.sales_summary(session, ctx, week)
    before = await an.sales_summary(session, ctx, previous)
    a_year_ago = await an.sales_summary(session, ctx, year_ago)

    products = await an.top_products(session, ctx, week, limit=TOP_PRODUCTS)
    actions = await top_open(session, ctx, limit=TOP_ACTIONS)

    month_start = day.replace(day=1)
    ledger = await value_ledger(session, ctx, month_start, day)

    digest = Digest(
        tenant_slug=ctx.slug,
        shop_name=ctx.name,
        week=week,
        currency=ctx.currency,
        net_sales=Comparison.of("Net sales", summary.net_sales, before.net_sales),
        orders=Comparison.of("Orders", Decimal(summary.order_count), Decimal(before.order_count)),
        average_order_value=Comparison.of(
            "Average order", summary.average_order_value, before.average_order_value
        ),
        units=summary.units_sold,
        year_ago=(
            Comparison.of("A year ago", summary.net_sales, a_year_ago.net_sales)
            if a_year_ago.order_count
            else None
        ),
        actions=actions,
        top_products=[
            {
                "name": row.label,
                "net_sales": str(row.net_sales),
                "units": str(row.units_sold),
            }
            for row in products.rows
        ],
        ledger=ledger,
        caveats=[caveat.message for caveat in summary.caveats],
    )
    digest.notable = _notable(digest, summary, before)
    return digest


def last_full_week(day: date) -> DateRange:
    """Monday to Sunday, the week that has just finished.

    A digest sent on Monday morning is about the week that ended yesterday, and
    a week that includes the morning it was sent would be a week with six and a
    half days in it.
    """
    monday_this_week = day - timedelta(days=day.weekday())
    return DateRange(monday_this_week - timedelta(days=7), monday_this_week - timedelta(days=1))


def _notable(digest: Digest, summary: Any, before: Any) -> list[str]:
    """Things worth a sentence that the headline figures do not say.

    Written from the numbers already in the payload, so the renderer has
    nothing to add and the validator has nothing to reject.
    """
    out: list[str] = []
    top = digest.top_products[0] if digest.top_products else None
    if top:
        out.append(f"{top['name']} led the week at ${top['net_sales']}.")
    if summary.refunds and summary.refunds > (before.refunds or 0) * 2:
        out.append(f"Refunds came to ${summary.refunds}, more than double the week before.")
    if summary.discounts and summary.net_sales:
        share = (summary.discounts / (summary.net_sales + summary.discounts)).quantize(
            Decimal("0.001")
        )
        if share >= Decimal("0.1"):
            out.append(f"Discounts were {share:.0%} of what was rung up.")
    return out


def deep_link(tenant_slug: str, insight: StoredInsight) -> str:
    """Where an action's button goes, straight to the thing it is about."""
    from app.notify import app_link

    route = str(insight.suggested_action.get("route") or "insights")
    return app_link(tenant_slug, route)


def insight_ids(digest: Digest) -> list[uuid.UUID]:
    return [insight.id for insight in digest.actions]
