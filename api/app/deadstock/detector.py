"""One dead-stock insight a week, with the five worth starting on.

Weekly rather than daily because nothing about a shelf that has not moved in
four months changes overnight, and an inbox item that reappears every morning
is an inbox item that gets muted.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext
from app.canonical.enums import InsightSeverity, StaleKind
from app.deadstock.service import plan
from app.insights import register
from app.insights.models import InsightDraft

TOP_ITEMS = 5
SHELF_LIFE_DAYS = 14

# Below this, it is not worth an owner's Monday morning.
MIN_CASH = Decimal("100")


class DeadStockDetector:
    kind = "dead_stock"
    schedule = "weekly"
    requires: tuple[str, ...] = ()

    async def run(
        self, session: AsyncSession, ctx: AnalyticsContext, as_of: date
    ) -> list[InsightDraft]:
        # Worth the model round trip here: nobody is waiting on a page.
        rescue_plan = await plan(session, ctx, as_of=as_of, phrase=True)
        report = rescue_plan.report
        if not report.items or report.total_cash < MIN_CASH:
            return []

        dead = report.of_kind(StaleKind.DEAD)
        stale = report.of_kind(StaleKind.STALE)
        slowing = report.of_kind(StaleKind.SLOWING)
        top = rescue_plan.rescues[:TOP_ITEMS]

        at_cost = report.cost_coverage is not None and report.cost_coverage >= Decimal("0.5")
        basis = "at cost" if at_cost else "at retail — costs are missing for most of these"

        # Headlines keep their own capitalisation: lowercasing them turns
        # "Matte Card Sleeves" into "matte card sleeves" halfway through a
        # sentence, which reads like a bug in an email.
        listed = "; ".join(f"{r.item.label} ({r.headline})" for r in top)
        summary = (
            f"${report.total_cash:,.0f} is sitting in {len(report.items)} items that have "
            f"stopped moving ({len(dead)} dead, {len(stale)} stale, {len(slowing)} slowing), "
            f"{basis}. The five worth starting on: {listed}."
        )

        year, week, _ = as_of.isocalendar()
        return [
            InsightDraft(
                kind=self.kind,
                severity=(
                    InsightSeverity.WARN
                    if report.total_cash >= Decimal("750")
                    else InsightSeverity.INFO
                ),
                title=f"${report.total_cash:,.0f} tied up in {len(report.items)} items",
                summary=summary,
                dedupe_key=f"dead_stock:{year}-W{week:02d}",
                dollar_impact=report.total_cash,
                evidence={
                    "as_of": as_of.isoformat(),
                    "basis": basis,
                    "cost_coverage": (
                        str(report.cost_coverage) if report.cost_coverage is not None else None
                    ),
                    "counts": {
                        "dead": len(dead),
                        "stale": len(stale),
                        "slowing": len(slowing),
                    },
                    "items": [
                        {
                            "variant_id": str(r.item.variant_id),
                            "label": r.item.label,
                            "sku": r.item.sku,
                            "kind": r.item.kind.value,
                            "units_on_hand": str(r.item.on_hand),
                            "days_since_last_sale": r.item.days_since_last_sale,
                            "never_sold": r.item.never_sold,
                            "cash_tied_up": str(r.cash_tied_up),
                            "cash_at_cost": (
                                str(r.item.cash_at_cost)
                                if r.item.cash_at_cost is not None
                                else None
                            ),
                            "play": r.play,
                            "headline": r.headline,
                            "why": r.reason,
                            "detail": r.detail,
                            "ladder": [
                                {
                                    "discount": str(rung.discount),
                                    "price": str(rung.price),
                                    "margin_per_unit": (
                                        str(rung.margin_per_unit)
                                        if rung.margin_per_unit is not None
                                        else None
                                    ),
                                    "clears_cost": rung.clears_cost,
                                }
                                for rung in r.ladder
                            ],
                        }
                        for r in rescue_plan.rescues
                    ],
                },
                suggested_action={
                    "type": "review_dead_stock",
                    "label": "See the plan for each",
                    "route": "dead-stock",
                },
                expires_at=datetime.now(tz=UTC) + timedelta(days=SHELF_LIFE_DAYS),
            )
        ]


register(DeadStockDetector())
