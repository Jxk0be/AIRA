"""The reorder detector: what will run out before a van could get here.

Not every suggestion is worth an insight. A shop with four hundred lines on its
weekly order does not need four hundred inbox items — it needs one sentence
saying which handful will be gone before the next delivery, and what that costs.

The dollar figure is the margin on the sales we expect to miss, over the days
between the shelf emptying and stock arriving. Where costs are missing it falls
back to lost sales and says so in as many words, because a margin number quietly
computed as if cost were zero is worse than no number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext
from app.canonical.enums import InsightSeverity
from app.insights import register
from app.insights.models import InsightDraft
from app.reorder.forecast import Suggestion
from app.reorder.service import suggest

# How many items an insight names before it stops naming them and starts
# counting them. Five fits in a digest line and on a phone screen.
NAMED_ITEMS = 5

# A finding stays actionable for a week. After that the velocity behind it is
# stale and the next Monday's run has replaced it anyway.
SHELF_LIFE_DAYS = 7

CENTS = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class _Shortfall:
    """One item's worth of running out, priced."""

    line: Suggestion
    days_short: Decimal
    units_missed: Decimal
    lost_sales: Decimal
    lost_margin: Decimal | None


def _shortfall(line: Suggestion) -> _Shortfall | None:
    """What running out of this costs, between empty shelf and delivery."""
    if line.days_of_cover is None or not line.stocks_out_before_delivery:
        return None
    days_short = Decimal(line.lead_time_days) - line.days_of_cover
    if days_short <= 0:
        return None
    units = (line.velocity * days_short).quantize(Decimal("0.01"))
    price = line.unit_price
    if price is None:
        # A variable-priced item has no shelf price to miss out on. Counting it
        # as zero would be a lie in the other direction, so it is left out of
        # the money entirely and only counted in the item list.
        return _Shortfall(
            line=line,
            days_short=days_short.quantize(Decimal("0.1")),
            units_missed=units,
            lost_sales=Decimal("0"),
            lost_margin=None,
        )
    lost_sales = (units * price).quantize(CENTS)
    lost_margin = (
        (units * (price - line.unit_cost)).quantize(CENTS) if line.unit_cost is not None else None
    )
    return _Shortfall(
        line=line,
        days_short=days_short.quantize(Decimal("0.1")),
        units_missed=units,
        lost_sales=lost_sales,
        lost_margin=lost_margin,
    )


class ReorderDetector:
    kind = "reorder"
    schedule = "daily"
    requires: tuple[str, ...] = ()

    async def run(
        self, session: AsyncSession, ctx: AnalyticsContext, as_of: date
    ) -> list[InsightDraft]:
        forecast = await suggest(session, ctx, as_of=as_of)
        shortfalls = [s for s in (_shortfall(line) for line in forecast.suggestions) if s]
        if not shortfalls:
            return []

        shortfalls.sort(key=lambda s: s.lost_margin or s.lost_sales, reverse=True)
        priced = [s for s in shortfalls if s.lost_margin is not None]
        using_margin = len(priced) >= len(shortfalls) / 2

        impact = sum(
            (
                (s.lost_margin if using_margin and s.lost_margin is not None else s.lost_sales)
                for s in shortfalls
            ),
            Decimal("0"),
        ).quantize(CENTS, rounding=ROUND_HALF_UP)

        named = shortfalls[:NAMED_ITEMS]
        listed = ", ".join(f"{s.line.label} ({_plain(s.line.on_hand)} left)" for s in named)
        rest = len(shortfalls) - len(named)
        basis = "lost gross margin" if using_margin else "lost sales — no costs on file"

        summary = (
            f"{len(shortfalls)} item{'s' if len(shortfalls) != 1 else ''} will be gone before a "
            f"reorder could arrive: {listed}"
            + (f", and {rest} more" if rest > 0 else "")
            + f". Roughly ${impact} of {basis} if nothing is ordered."
        )

        # One insight a week per shop: the dedupe key carries the ISO week, so
        # re-running on Tuesday updates Monday's finding rather than adding one.
        year, week, _ = as_of.isocalendar()
        return [
            InsightDraft(
                kind=self.kind,
                severity=(
                    InsightSeverity.URGENT if impact >= Decimal("250") else InsightSeverity.WARN
                ),
                title=f"{len(shortfalls)} items will run out before a reorder lands",
                summary=summary,
                dedupe_key=f"reorder:{year}-W{week:02d}",
                dollar_impact=impact,
                evidence={
                    "basis": basis,
                    "as_of": as_of.isoformat(),
                    "item_count": len(shortfalls),
                    "cost_coverage": (
                        str(forecast.cost_coverage) if forecast.cost_coverage is not None else None
                    ),
                    "total_at_cost": str(forecast.total_at_cost),
                    "forecast_caveats": forecast.caveats,
                    "items": [
                        {
                            "variant_id": str(s.line.variant_id),
                            "label": s.line.label,
                            "sku": s.line.sku,
                            "on_hand": str(s.line.on_hand),
                            "velocity_per_day": str(s.line.velocity),
                            "days_of_cover": str(s.line.days_of_cover),
                            "lead_time_days": s.line.lead_time_days,
                            "days_short": str(s.days_short),
                            "units_missed": str(s.units_missed),
                            "lost_sales": str(s.lost_sales),
                            "lost_margin": (
                                str(s.lost_margin) if s.lost_margin is not None else None
                            ),
                            "suggested_qty": str(s.line.suggested_qty),
                            "vendor": s.line.vendor_name,
                            "why": s.line.explanation,
                        }
                        for s in shortfalls[:50]
                    ],
                },
                suggested_action={
                    "type": "review_purchase_orders",
                    "label": "Review the draft order",
                    "route": "reorder",
                },
                expires_at=datetime.now(tz=UTC) + timedelta(days=SHELF_LIFE_DAYS),
            )
        ]


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


register(ReorderDetector())
