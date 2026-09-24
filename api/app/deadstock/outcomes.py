"""Did the markdown work?

Thirty days either side of the action, on units and on money. The comparison is
blunt and it is honest about being blunt: a shop's whole trade might have been
up that month, and nothing here can separate that from the markdown. What it
can say without qualification is how many units moved and what came in at the
till for them, which is what the owner wanted to know.

Cash recovered is counted at **cost**, not at the marked-down price. Clearing
$40 of retail on something that cost $22 recovers $22 of the money that was
stuck; the other $18 was never stuck in the first place. That is the figure
that belongs in the value ledger.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext, DateRange
from app.analytics.demand import variant_sales
from app.deadstock.service import mark_measured
from app.deadstock.tables import RescueAction
from app.insights import METRIC_CASH_RECOVERED, add_outcome

log = logging.getLogger(__name__)

WINDOW_DAYS = 30
METRIC_UNITS = "units_after_rescue"
METRIC_REVENUE = "rescue_sales"


@dataclass(slots=True)
class MeasuredRescue:
    action_id: object
    variant_id: object
    kind: str
    units_before: Decimal
    units_after: Decimal
    sales_after: Decimal
    cash_recovered: Decimal


async def measure(
    session: AsyncSession, ctx: AnalyticsContext, *, as_of: date | None = None
) -> list[MeasuredRescue]:
    """Measure every rescue whose thirty days have run out.

    Waits for the whole window rather than reporting a part of it: a markdown
    measured after nine days looks like a failure every time.
    """
    day = as_of or ctx.today()
    actions = (
        (
            await session.execute(
                select(RescueAction).where(
                    RescueAction.tenant_id == ctx.tenant_id,
                    RescueAction.measured_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    measured: list[MeasuredRescue] = []
    for action in actions:
        taken_on = action.taken_at.astimezone(ctx.tz).date()
        if taken_on + timedelta(days=WINDOW_DAYS) > day:
            continue

        after = DateRange(taken_on, taken_on + timedelta(days=WINDOW_DAYS - 1))
        before = DateRange(taken_on - timedelta(days=WINDOW_DAYS), taken_on - timedelta(days=1))
        sales_after = (await variant_sales(session, ctx, [action.variant_id], after)).get(
            action.variant_id
        )
        sales_before = (await variant_sales(session, ctx, [action.variant_id], before)).get(
            action.variant_id
        )

        units_after = sales_after.units if sales_after else Decimal("0")
        units_before = sales_before.units if sales_before else Decimal("0")
        money_after = sales_after.net_sales if sales_after else Decimal("0")
        recovered = (
            (units_after * action.unit_cost).quantize(Decimal("0.01"))
            if action.unit_cost is not None
            else Decimal("0")
        )

        if action.insight_id is not None:
            await add_outcome(
                session,
                ctx.tenant_id,
                action.insight_id,
                metric=METRIC_UNITS,
                before_value=units_before,
                after_value=units_after,
                notes=(
                    f"{action.kind} on {taken_on.isoformat()}: {units_before} units in the "
                    f"{WINDOW_DAYS} days before, {units_after} in the {WINDOW_DAYS} after"
                ),
            )
            await add_outcome(
                session,
                ctx.tenant_id,
                action.insight_id,
                metric=METRIC_REVENUE,
                after_value=money_after.quantize(Decimal("0.01")),
                attributed_revenue=None,  # trade might have been up anyway
                notes=f"${money_after:,.2f} rang through in the {WINDOW_DAYS} days after",
            )
            if recovered > 0:
                await add_outcome(
                    session,
                    ctx.tenant_id,
                    action.insight_id,
                    metric=METRIC_CASH_RECOVERED,
                    after_value=recovered,
                    notes=(
                        f"{units_after} units cleared at a recorded cost of "
                        f"${action.unit_cost} each"
                    ),
                )

        await mark_measured(session, action.id)
        measured.append(
            MeasuredRescue(
                action_id=action.id,
                variant_id=action.variant_id,
                kind=action.kind,
                units_before=units_before,
                units_after=units_after,
                sales_after=money_after,
                cash_recovered=recovered,
            )
        )

    await session.commit()
    return measured
