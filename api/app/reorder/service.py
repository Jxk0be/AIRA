"""Suggestions in, draft purchase orders out.

The rule this module exists to enforce: **nothing is ever sent to a vendor.**
A draft is produced, the owner edits it, and the most we do is open their mail
client with the body already written. An AI that emails a distributor an order
for 200 of something is the single worst failure mode this product has, and the
protection against it is that there is no code path that could.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext, Filters
from app.analytics.demand import demand_inputs
from app.canonical import tables as t
from app.canonical.enums import PurchaseOrderStatus
from app.db import table_of
from app.reorder.forecast import Forecast, Suggestion, build
from app.reorder.tables import PurchaseOrder, PurchaseOrderLine

# The group a variant lands in when we do not know who sells it. Named rather
# than left blank so it reads as a task on the screen, not as an error.
UNASSIGNED = "Unassigned — no supplier on file"

NO_FILTERS = Filters()


@dataclass(slots=True)
class VendorGroup:
    """One vendor's worth of a reorder."""

    vendor_id: uuid.UUID | None
    vendor_name: str
    lines: list[Suggestion] = field(default_factory=list)

    @property
    def total_at_cost(self) -> Decimal | None:
        costs = [line.line_cost for line in self.lines if line.line_cost is not None]
        if not costs:
            return None
        return sum(costs, Decimal("0"))

    @property
    def unpriced_lines(self) -> int:
        return sum(1 for line in self.lines if line.unit_cost is None)


async def suggest(
    session: AsyncSession,
    ctx: AnalyticsContext,
    filters: Filters = NO_FILTERS,
    *,
    as_of: date | None = None,
) -> Forecast:
    """What this shop should reorder today, with the reasoning on every line."""
    inputs = await demand_inputs(session, ctx, filters, as_of=as_of)
    on_order = await outstanding_quantities(session, ctx)
    skip = tuple(
        str(word).lower()
        for word in ctx.setting("reorder_skip_categories", None)
        or list(_default_skip())
    )
    return build(inputs, skip_categories=skip, on_order=on_order)


def _default_skip() -> tuple[str, ...]:
    from app.reorder.forecast import DEFAULT_SKIP_CATEGORIES

    return DEFAULT_SKIP_CATEGORIES


def group_by_vendor(forecast: Forecast) -> list[VendorGroup]:
    """Split a forecast into the orders it would actually become.

    Sorted with the unassigned group last: it is a chore, not an order, and
    burying it at the top of the screen would make the real orders harder to
    get through.
    """
    groups: dict[uuid.UUID | None, VendorGroup] = {}
    for line in forecast.suggestions:
        key = line.vendor_id
        if key not in groups:
            groups[key] = VendorGroup(
                vendor_id=key, vendor_name=line.vendor_name or UNASSIGNED
            )
        groups[key].lines.append(line)
    ordered = sorted(
        groups.values(),
        key=lambda g: (g.vendor_id is None, -(g.total_at_cost or Decimal("0"))),
    )
    return ordered


async def outstanding_quantities(
    session: AsyncSession, ctx: AnalyticsContext
) -> dict[uuid.UUID, Decimal]:
    """What is already on an open order and has not arrived.

    Subtracted before anything new is suggested. Without it, reviewing the
    reorder twice in one week orders everything twice, which is the fastest
    way to lose a shop's trust in the feature.
    """
    rows = await session.execute(
        select(
            PurchaseOrderLine.variant_id,
            func.sum(PurchaseOrderLine.quantity - func.coalesce(PurchaseOrderLine.received_qty, 0)),
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
        .where(
            PurchaseOrderLine.tenant_id == ctx.tenant_id,
            PurchaseOrder.status.in_(
                [PurchaseOrderStatus.DRAFT.value, PurchaseOrderStatus.SENT.value]
            ),
        )
        .group_by(PurchaseOrderLine.variant_id)
    )
    return {variant_id: Decimal(quantity or 0) for variant_id, quantity in rows}


async def _next_reference(session: AsyncSession, ctx: AnalyticsContext, when: date) -> str:
    count = (
        await session.execute(
            select(func.count()).where(PurchaseOrder.tenant_id == ctx.tenant_id)
        )
    ).scalar_one()
    return f"PO-{when:%Y-%m}-{int(count) + 1:04d}"


async def create_drafts(
    session: AsyncSession,
    ctx: AnalyticsContext,
    groups: list[VendorGroup],
    *,
    insight_id: uuid.UUID | None = None,
    as_of: date | None = None,
) -> list[uuid.UUID]:
    """Turn vendor groups into draft POs. Returns the new order ids.

    Drafts are created, never sent. An existing open draft for the same vendor
    is replaced rather than duplicated, so pressing the button twice leaves one
    order rather than two.
    """
    day = as_of or ctx.today()
    created: list[uuid.UUID] = []

    for group in groups:
        if not group.lines:
            continue
        await _discard_open_draft(session, ctx, group.vendor_id)

        order_id = uuid.uuid4()
        lead_times = [line.lead_time_days for line in group.lines]
        await session.execute(
            insert(table_of(PurchaseOrder)).values(
                id=order_id,
                tenant_id=ctx.tenant_id,
                vendor_id=group.vendor_id,
                vendor_name_snapshot=group.vendor_name,
                location_id=group.lines[0].location_id,
                status=PurchaseOrderStatus.DRAFT,
                reference=await _next_reference(session, ctx, day),
                insight_id=insight_id,
                expected_at=day + timedelta(days=max(lead_times)) if lead_times else None,
            )
        )
        await session.execute(
            insert(table_of(PurchaseOrderLine)).values(
                [
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": ctx.tenant_id,
                        "purchase_order_id": order_id,
                        "variant_id": line.variant_id,
                        "name_snapshot": line.label,
                        "sku_snapshot": line.sku,
                        "suggested_qty": line.suggested_qty,
                        "quantity": line.suggested_qty,
                        "unit_cost": line.unit_cost,
                        "on_hand_at_draft": line.on_hand,
                        "reasoning": {
                            "explanation": line.explanation,
                            "velocity_per_day": str(line.velocity),
                            "seasonal_factor": str(line.seasonal_factor),
                            "horizon_days": line.horizon_days,
                            "demand": str(line.demand),
                            "safety_stock": str(line.safety_stock),
                            "lead_time_days": line.lead_time_days,
                            "lead_time_assumed": line.lead_time_assumed,
                            "days_of_cover": (
                                str(line.days_of_cover) if line.days_of_cover is not None else None
                            ),
                            "caveats": list(line.caveats),
                        },
                    }
                    for line in group.lines
                ]
            )
        )
        created.append(order_id)

    await session.commit()
    return created


async def _discard_open_draft(
    session: AsyncSession, ctx: AnalyticsContext, vendor_id: uuid.UUID | None
) -> None:
    table = table_of(PurchaseOrder)
    condition = (
        table.c.vendor_id == vendor_id if vendor_id is not None else table.c.vendor_id.is_(None)
    )
    await session.execute(
        update(table)
        .where(
            table.c.tenant_id == ctx.tenant_id,
            table.c.status == PurchaseOrderStatus.DRAFT,
            condition,
        )
        .values(status=PurchaseOrderStatus.CANCELED, updated_at=datetime.now(tz=UTC))
    )


# --------------------------------------------------------------------------
# Reading and editing a draft
# --------------------------------------------------------------------------


@dataclass(slots=True)
class PurchaseOrderView:
    id: uuid.UUID
    reference: str
    vendor_id: uuid.UUID | None
    vendor_name: str
    vendor_email: str | None
    status: str
    note: str | None
    expected_at: date | None
    created_at: datetime
    sent_at: datetime | None
    received_at: datetime | None
    lines: list[PurchaseOrderLineView] = field(default_factory=list)

    @property
    def total_at_cost(self) -> Decimal | None:
        priced = [line.line_cost for line in self.lines if line.line_cost is not None]
        return sum(priced, Decimal("0")) if priced else None

    @property
    def unpriced_lines(self) -> int:
        return sum(1 for line in self.lines if line.unit_cost is None)

    @property
    def units(self) -> Decimal:
        return sum((line.quantity for line in self.lines), Decimal("0"))


@dataclass(slots=True)
class PurchaseOrderLineView:
    id: uuid.UUID
    variant_id: uuid.UUID
    name: str
    sku: str | None
    quantity: Decimal
    suggested_qty: Decimal
    unit_cost: Decimal | None
    on_hand_at_draft: Decimal
    explanation: str
    caveats: list[str]
    received_qty: Decimal | None

    @property
    def line_cost(self) -> Decimal | None:
        if self.unit_cost is None:
            return None
        return (self.quantity * self.unit_cost).quantize(Decimal("0.01"))


async def list_orders(
    session: AsyncSession, ctx: AnalyticsContext, *, status: str | None = None, limit: int = 25
) -> list[PurchaseOrderView]:
    query = (
        select(PurchaseOrder, t.Vendor.email)
        .join(t.Vendor, t.Vendor.id == PurchaseOrder.vendor_id, isouter=True)
        .where(PurchaseOrder.tenant_id == ctx.tenant_id)
    )
    if status:
        query = query.where(PurchaseOrder.status == status)
    else:
        query = query.where(PurchaseOrder.status != PurchaseOrderStatus.CANCELED)
    rows = (
        await session.execute(query.order_by(PurchaseOrder.created_at.desc()).limit(limit))
    ).all()

    orders = [_order_view(order, email) for order, email in rows]
    if not orders:
        return []

    lines = (
        (
            await session.execute(
                select(PurchaseOrderLine)
                .where(PurchaseOrderLine.purchase_order_id.in_([o.id for o in orders]))
                .order_by(PurchaseOrderLine.name_snapshot)
            )
        )
        .scalars()
        .all()
    )
    by_order: dict[uuid.UUID, list[PurchaseOrderLineView]] = {}
    for line in lines:
        by_order.setdefault(line.purchase_order_id, []).append(_line_view(line))
    for order in orders:
        order.lines = by_order.get(order.id, [])
    return orders


async def get_order(
    session: AsyncSession, ctx: AnalyticsContext, order_id: uuid.UUID
) -> PurchaseOrderView | None:
    row = (
        await session.execute(
            select(PurchaseOrder, t.Vendor.email)
            .join(t.Vendor, t.Vendor.id == PurchaseOrder.vendor_id, isouter=True)
            .where(PurchaseOrder.id == order_id, PurchaseOrder.tenant_id == ctx.tenant_id)
        )
    ).first()
    if row is None:
        return None
    view = _order_view(row[0], row[1])
    lines = (
        (
            await session.execute(
                select(PurchaseOrderLine)
                .where(PurchaseOrderLine.purchase_order_id == order_id)
                .order_by(PurchaseOrderLine.name_snapshot)
            )
        )
        .scalars()
        .all()
    )
    view.lines = [_line_view(line) for line in lines]
    return view


def _order_view(order: PurchaseOrder, vendor_email: str | None) -> PurchaseOrderView:
    return PurchaseOrderView(
        id=order.id,
        reference=order.reference,
        vendor_id=order.vendor_id,
        vendor_name=order.vendor_name_snapshot or UNASSIGNED,
        vendor_email=vendor_email,
        status=str(order.status),
        note=order.note,
        expected_at=order.expected_at,
        created_at=order.created_at,
        sent_at=order.sent_at,
        received_at=order.received_at,
    )


def _line_view(line: PurchaseOrderLine) -> PurchaseOrderLineView:
    reasoning = dict(line.reasoning or {})
    return PurchaseOrderLineView(
        id=line.id,
        variant_id=line.variant_id,
        name=line.name_snapshot,
        sku=line.sku_snapshot,
        quantity=line.quantity,
        suggested_qty=line.suggested_qty,
        unit_cost=line.unit_cost,
        on_hand_at_draft=line.on_hand_at_draft,
        explanation=str(reasoning.get("explanation", "")),
        caveats=list(reasoning.get("caveats", [])),
        received_qty=line.received_qty,
    )


async def update_line(
    session: AsyncSession,
    ctx: AnalyticsContext,
    line_id: uuid.UUID,
    *,
    quantity: Decimal | None = None,
    remove: bool = False,
) -> None:
    table = table_of(PurchaseOrderLine)
    if remove:
        await session.execute(
            table.delete().where(table.c.id == line_id, table.c.tenant_id == ctx.tenant_id)
        )
    elif quantity is not None:
        await session.execute(
            update(table)
            .where(table.c.id == line_id, table.c.tenant_id == ctx.tenant_id)
            .values(quantity=max(quantity, Decimal("0")), updated_at=datetime.now(tz=UTC))
        )
    await session.commit()


async def set_order_status(
    session: AsyncSession,
    ctx: AnalyticsContext,
    order_id: uuid.UUID,
    status: PurchaseOrderStatus,
) -> None:
    """Move a draft along. `sent` means the owner said they sent it."""
    now = datetime.now(tz=UTC)
    values: dict[str, object] = {"status": status, "updated_at": now}
    if status is PurchaseOrderStatus.SENT:
        values["sent_at"] = now
    elif status is PurchaseOrderStatus.RECEIVED:
        values["received_at"] = now

    table = table_of(PurchaseOrder)
    await session.execute(
        update(table)
        .where(table.c.id == order_id, table.c.tenant_id == ctx.tenant_id)
        .values(values)
    )
    if status is PurchaseOrderStatus.RECEIVED:
        # Nothing arrived short unless the owner says so, and marking the whole
        # order received is the common case by a long way.
        lines = table_of(PurchaseOrderLine)
        await session.execute(
            update(lines)
            .where(
                lines.c.purchase_order_id == order_id,
                lines.c.tenant_id == ctx.tenant_id,
                lines.c.received_qty.is_(None),
            )
            .values(received_qty=lines.c.quantity, updated_at=now)
        )
    await session.commit()
