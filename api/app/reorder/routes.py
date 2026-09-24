"""HTTP for the reorder screen and the purchase order review.

Note what is missing: there is no endpoint that sends an order. The furthest
this goes is handing back a `mailto:` for the owner's own mail client.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.canonical.enums import PurchaseOrderStatus
from app.http import ShopDep, not_found
from app.reorder import (
    create_drafts,
    get_order,
    group_by_vendor,
    list_orders,
    mailto_link,
    purchase_order_csv,
    purchase_order_pdf,
    set_order_status,
    suggest,
    update_line,
)

router = APIRouter(tags=["reorder"])


class SuggestionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: uuid.UUID
    label: str
    sku: str | None
    category: str | None
    location: str | None
    on_hand: Decimal
    velocity_per_day: Decimal
    seasonal_factor: Decimal
    days_of_cover: Decimal | None
    lead_time_days: int
    suggested_qty: Decimal
    unit_cost: Decimal | None
    line_cost: Decimal | None
    # The sentence that makes the number arguable rather than magic.
    why: str
    caveats: list[str]
    urgent: bool


class VendorGroupOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor_id: uuid.UUID | None
    vendor_name: str
    lines: list[SuggestionOut]
    total_at_cost: Decimal | None
    unpriced_lines: int


class ReorderOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant: str
    as_of: date
    groups: list[VendorGroupOut]
    total_at_cost: Decimal
    cost_coverage: Decimal | None
    skipped: dict[str, int]
    caveats: list[str]


class LineOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    variant_id: uuid.UUID
    name: str
    sku: str | None
    quantity: Decimal
    suggested_qty: Decimal
    unit_cost: Decimal | None
    line_cost: Decimal | None
    on_hand_at_draft: Decimal
    why: str
    caveats: list[str]
    received_qty: Decimal | None


class OrderOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    reference: str
    vendor_id: uuid.UUID | None
    vendor_name: str
    vendor_email: str | None
    status: str
    note: str | None
    expected_at: date | None
    units: Decimal
    total_at_cost: Decimal | None
    unpriced_lines: int
    lines: list[LineOut]
    mailto: str | None = None


class LineUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantity: Decimal | None = Field(default=None, ge=0)
    remove: bool = False


class StatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["draft", "sent", "received", "canceled"]


@router.get("/tenants/{tenant}/reorder", response_model=ReorderOut)
async def reorder(shop: ShopDep, as_of: date | None = None) -> ReorderOut:
    forecast = await suggest(shop.session, shop.ctx, as_of=as_of)
    groups = group_by_vendor(forecast)
    return ReorderOut(
        tenant=shop.slug,
        as_of=as_of or shop.ctx.today(),
        groups=[
            VendorGroupOut(
                vendor_id=group.vendor_id,
                vendor_name=group.vendor_name,
                total_at_cost=group.total_at_cost,
                unpriced_lines=group.unpriced_lines,
                lines=[
                    SuggestionOut(
                        variant_id=line.variant_id,
                        label=line.label,
                        sku=line.sku,
                        category=line.category,
                        location=line.location_name,
                        on_hand=line.on_hand,
                        velocity_per_day=line.velocity,
                        seasonal_factor=line.seasonal_factor,
                        days_of_cover=line.days_of_cover,
                        lead_time_days=line.lead_time_days,
                        suggested_qty=line.suggested_qty,
                        unit_cost=line.unit_cost,
                        line_cost=line.line_cost,
                        why=line.explanation,
                        caveats=list(line.caveats),
                        urgent=line.stocks_out_before_delivery,
                    )
                    for line in group.lines
                ],
            )
            for group in groups
        ],
        total_at_cost=forecast.total_at_cost,
        cost_coverage=forecast.cost_coverage,
        skipped=forecast.skipped,
        caveats=forecast.caveats,
    )


@router.post("/tenants/{tenant}/reorder/drafts", response_model=list[uuid.UUID])
async def make_drafts(
    shop: ShopDep,
    as_of: date | None = None,
    vendor_id: uuid.UUID | None = Query(default=None),
) -> list[uuid.UUID]:
    """Turn the suggestions into draft POs, one per vendor."""
    forecast = await suggest(shop.session, shop.ctx, as_of=as_of)
    groups = group_by_vendor(forecast)
    if vendor_id is not None:
        groups = [group for group in groups if group.vendor_id == vendor_id]
    return await create_drafts(shop.session, shop.ctx, groups, as_of=as_of)


@router.get("/tenants/{tenant}/purchase-orders", response_model=list[OrderOut])
async def orders(shop: ShopDep, status: str | None = None) -> list[OrderOut]:
    return [
        _order_out(shop, order)
        for order in await list_orders(shop.session, shop.ctx, status=status)
    ]


@router.patch("/tenants/{tenant}/purchase-orders/lines/{line_id}", status_code=204)
async def edit_line(shop: ShopDep, line_id: uuid.UUID, body: LineUpdate) -> None:
    await update_line(shop.session, shop.ctx, line_id, quantity=body.quantity, remove=body.remove)


@router.post("/tenants/{tenant}/purchase-orders/{order_id}/status", response_model=OrderOut)
async def change_status(shop: ShopDep, order_id: uuid.UUID, body: StatusUpdate) -> OrderOut:
    await set_order_status(shop.session, shop.ctx, order_id, PurchaseOrderStatus(body.status))
    order = await get_order(shop.session, shop.ctx, order_id)
    if order is None:
        raise not_found("purchase order")
    return _order_out(shop, order, with_mailto=True)


@router.get("/tenants/{tenant}/purchase-orders/{order_id}.pdf")
async def order_pdf(shop: ShopDep, order_id: uuid.UUID) -> Response:
    order = await get_order(shop.session, shop.ctx, order_id)
    if order is None:
        raise not_found("purchase order")
    return Response(
        content=purchase_order_pdf(shop.ctx, order),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{order.reference}.pdf"'},
    )


@router.get("/tenants/{tenant}/purchase-orders/{order_id}.csv")
async def order_csv(shop: ShopDep, order_id: uuid.UUID) -> Response:
    order = await get_order(shop.session, shop.ctx, order_id)
    if order is None:
        raise not_found("purchase order")
    return Response(
        content=purchase_order_csv(order),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{order.reference}.csv"'},
    )


# Declared *after* the `.pdf` and `.csv` routes on purpose. FastAPI matches
# in declaration order and a path parameter happily swallows a dotted suffix,
# so with this one first every download request arrived here instead, as
# `order_id="<uuid>.pdf"`, and came back 422 instead of a file.
@router.get("/tenants/{tenant}/purchase-orders/{order_id}", response_model=OrderOut)
async def one_order(shop: ShopDep, order_id: uuid.UUID) -> OrderOut:
    order = await get_order(shop.session, shop.ctx, order_id)
    if order is None:
        raise not_found("purchase order")
    return _order_out(shop, order, with_mailto=True)


def _order_out(shop: ShopDep, order: object, *, with_mailto: bool = False) -> OrderOut:
    from app.reorder.service import PurchaseOrderView

    assert isinstance(order, PurchaseOrderView)
    return OrderOut(
        id=order.id,
        reference=order.reference,
        vendor_id=order.vendor_id,
        vendor_name=order.vendor_name,
        vendor_email=order.vendor_email,
        status=order.status,
        note=order.note,
        expected_at=order.expected_at,
        units=order.units,
        total_at_cost=order.total_at_cost,
        unpriced_lines=order.unpriced_lines,
        lines=[
            LineOut(
                id=line.id,
                variant_id=line.variant_id,
                name=line.name,
                sku=line.sku,
                quantity=line.quantity,
                suggested_qty=line.suggested_qty,
                unit_cost=line.unit_cost,
                line_cost=line.line_cost,
                on_hand_at_draft=line.on_hand_at_draft,
                why=line.explanation,
                caveats=line.caveats,
                received_qty=line.received_qty,
            )
            for line in order.lines
        ],
        mailto=mailto_link(shop.ctx, order) if with_mailto else None,
    )
