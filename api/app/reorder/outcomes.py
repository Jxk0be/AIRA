"""Did the reorder actually help?

The honest version of a hard question. We cannot know what would have happened
if the shop had not ordered, so we do not claim to. What we claim is narrow and
checkable: of the units that sold in the month after a delivery, the ones that
could not have sold without it — because there were not that many on the shelf
when we suggested the order — are attributed to it.

    attributed units = min(units sold after, quantity received) - on hand at draft

Everything else about the delivery goes unclaimed. That undersells the feature,
which is the right direction to be wrong in when the number is going in front
of the person deciding whether to keep paying for it.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext, DateRange
from app.analytics.demand import variant_sales
from app.canonical import tables as t
from app.canonical.enums import PurchaseOrderStatus
from app.insights import add_outcome, outcome_exists
from app.reorder.tables import PurchaseOrder, PurchaseOrderLine

log = logging.getLogger(__name__)

# How long after a delivery we keep watching it.
MEASURE_DAYS = 30
# How far back the nightly job looks for deliveries to measure.
LOOKBACK_DAYS = 120

METRIC_UNITS = "units_after_reorder"
METRIC_REVENUE = "reorder_attributed_sales"


@dataclass(slots=True)
class MeasuredOrder:
    reference: str
    order_id: uuid.UUID
    lines_measured: int
    attributed_units: Decimal
    attributed_revenue: Decimal


async def measure(
    session: AsyncSession, ctx: AnalyticsContext, *, as_of: date | None = None
) -> list[MeasuredOrder]:
    """Measure every delivery whose watching window has just closed.

    Idempotent by the outcome log: a delivery already measured today is left
    alone, so running the nightly job twice does not double a shop's numbers.
    """
    day = as_of or ctx.today()
    window_opens = day - timedelta(days=LOOKBACK_DAYS)

    orders = (
        (
            await session.execute(
                select(PurchaseOrder).where(
                    PurchaseOrder.tenant_id == ctx.tenant_id,
                    PurchaseOrder.status == PurchaseOrderStatus.RECEIVED,
                    PurchaseOrder.received_at.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )

    measured: list[MeasuredOrder] = []
    for order in orders:
        assert order.received_at is not None
        received_on = order.received_at.astimezone(ctx.tz).date()
        if received_on < window_opens:
            continue
        # Only measure once the full month has actually elapsed: a half-measured
        # month read as a whole one understates every delivery.
        if received_on + timedelta(days=MEASURE_DAYS) > day:
            continue
        if order.insight_id and await outcome_exists(
            session, order.insight_id, METRIC_REVENUE, day
        ):
            continue

        result = await _measure_order(session, ctx, order, received_on)
        if result is not None:
            measured.append(result)

    await session.commit()
    return measured


async def _measure_order(
    session: AsyncSession, ctx: AnalyticsContext, order: PurchaseOrder, received_on: date
) -> MeasuredOrder | None:
    lines = (
        (
            await session.execute(
                select(PurchaseOrderLine).where(
                    PurchaseOrderLine.purchase_order_id == order.id,
                    PurchaseOrderLine.tenant_id == ctx.tenant_id,
                )
            )
        )
        .scalars()
        .all()
    )
    if not lines:
        return None

    variant_ids = [line.variant_id for line in lines]
    after = DateRange(received_on, received_on + timedelta(days=MEASURE_DAYS - 1))
    before = DateRange(received_on - timedelta(days=MEASURE_DAYS), received_on - timedelta(days=1))
    sales_after = await variant_sales(session, ctx, variant_ids, after)
    sales_before = await variant_sales(session, ctx, variant_ids, before)

    prices = dict(
        (
            await session.execute(
                select(t.Variant.id, t.Variant.price).where(t.Variant.id.in_(variant_ids))
            )
        ).all()
    )

    attributed_units = Decimal("0")
    attributed_revenue = Decimal("0")
    for line in lines:
        received = line.received_qty if line.received_qty is not None else line.quantity
        sold_after = sales_after.get(line.variant_id)
        if sold_after is None or received <= 0:
            continue
        covered_by_shelf = line.on_hand_at_draft
        units = min(sold_after.units, received) - covered_by_shelf
        if units <= 0:
            continue
        attributed_units += units
        price = prices.get(line.variant_id)
        if price is not None:
            attributed_revenue += (units * Decimal(price)).quantize(Decimal("0.01"))

    if order.insight_id is None:
        # A draft the owner built by hand has no finding behind it. Worth
        # measuring on screen, but it does not belong in the value ledger,
        # which is specifically about what our suggestions were worth.
        log.debug("purchase order %s has no insight to attribute to", order.reference)
        return MeasuredOrder(
            reference=order.reference,
            order_id=order.id,
            lines_measured=len(lines),
            attributed_units=attributed_units,
            attributed_revenue=attributed_revenue,
        )

    units_before = sum((s.units for s in sales_before.values()), Decimal("0"))
    units_after = sum((s.units for s in sales_after.values()), Decimal("0"))
    await add_outcome(
        session,
        ctx.tenant_id,
        order.insight_id,
        metric=METRIC_UNITS,
        before_value=units_before,
        after_value=units_after,
        notes=(
            f"{order.reference}: {len(lines)} lines received {received_on.isoformat()}; "
            f"{MEASURE_DAYS} days either side"
        ),
    )
    await add_outcome(
        session,
        ctx.tenant_id,
        order.insight_id,
        metric=METRIC_REVENUE,
        after_value=attributed_revenue,
        attributed_revenue=attributed_revenue,
        notes=(
            f"{attributed_units} units sold in the month after {order.reference} arrived that "
            f"the shelf could not have covered"
        ),
    )
    return MeasuredOrder(
        reference=order.reference,
        order_id=order.id,
        lines_measured=len(lines),
        attributed_units=attributed_units,
        attributed_revenue=attributed_revenue,
    )
