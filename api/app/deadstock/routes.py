"""HTTP for the Dead Stock screen.

Sorted by cash tied up, because that is the order an owner should work through
it in, and every row carries one specific thing to do rather than a status.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.deadstock import log_action, plan, recent_actions
from app.http import ShopDep

router = APIRouter(tags=["dead stock"])


class RungOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discount: Decimal
    price: Decimal
    margin_per_unit: Decimal | None
    clears_cost: bool


class StaleOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: uuid.UUID
    label: str
    sku: str | None
    category: str | None
    kind: str
    units_on_hand: Decimal
    days_since_last_sale: int | None
    never_sold: bool
    cash_tied_up: Decimal
    cash_at_cost: Decimal | None
    price: Decimal | None
    cost: Decimal | None
    play: str
    headline: str
    why: str
    detail: dict[str, Any]
    ladder: list[RungOut]


class DeadStockOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant: str
    as_of: date
    total_cash: Decimal
    planned_cash: Decimal
    item_count: int
    # How much of the money above is at cost rather than at retail tags.
    cost_coverage: Decimal | None
    counts: dict[str, int]
    items: list[StaleOut]


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: uuid.UUID
    kind: Literal["markdown", "bundle", "move", "return_to_vendor"]
    detail: dict[str, Any] = Field(default_factory=dict)
    price_after: Decimal | None = None
    insight_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=500)


class ActionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    variant_id: uuid.UUID
    kind: str
    detail: dict[str, Any]
    price_before: Decimal | None
    price_after: Decimal | None
    on_hand_before: Decimal
    taken_at: datetime
    measured_at: datetime | None
    note: str | None


@router.get("/tenants/{tenant}/dead-stock", response_model=DeadStockOut)
async def dead_stock(shop: ShopDep, as_of: date | None = None) -> DeadStockOut:
    rescue_plan = await plan(shop.session, shop.ctx, as_of=as_of)
    report = rescue_plan.report
    counts: dict[str, int] = {}
    for item in report.items:
        counts[item.kind.value] = counts.get(item.kind.value, 0) + 1

    return DeadStockOut(
        tenant=shop.slug,
        as_of=report.as_of,
        total_cash=report.total_cash,
        planned_cash=rescue_plan.planned_cash,
        item_count=len(report.items),
        cost_coverage=report.cost_coverage,
        counts=counts,
        items=[
            StaleOut(
                variant_id=rescue.item.variant_id,
                label=rescue.item.label,
                sku=rescue.item.sku,
                category=rescue.item.category,
                kind=rescue.item.kind.value,
                units_on_hand=rescue.item.on_hand,
                days_since_last_sale=rescue.item.days_since_last_sale,
                never_sold=rescue.item.never_sold,
                cash_tied_up=rescue.cash_tied_up,
                cash_at_cost=rescue.item.cash_at_cost,
                price=rescue.item.price,
                cost=rescue.item.cost,
                play=rescue.play,
                headline=rescue.headline,
                why=rescue.reason,
                detail=rescue.detail,
                ladder=[
                    RungOut(
                        discount=rung.discount,
                        price=rung.price,
                        margin_per_unit=rung.margin_per_unit,
                        clears_cost=rung.clears_cost,
                    )
                    for rung in rescue.ladder
                ],
            )
            for rescue in rescue_plan.rescues
        ],
    )


@router.post("/tenants/{tenant}/dead-stock/actions", response_model=ActionOut, status_code=201)
async def log(shop: ShopDep, body: ActionIn) -> ActionOut:
    """"I did this." The one click that makes the outcome measurable."""
    action_id = await log_action(
        shop.session,
        shop.ctx,
        variant_id=body.variant_id,
        kind=body.kind,
        detail=body.detail,
        price_after=body.price_after,
        insight_id=body.insight_id,
        note=body.note,
    )
    actions = await recent_actions(shop.session, shop.ctx, limit=1)
    row = next((a for a in actions if a.id == action_id), None)
    assert row is not None
    return _action_out(row)


@router.get("/tenants/{tenant}/dead-stock/actions", response_model=list[ActionOut])
async def actions(shop: ShopDep, limit: int = 50) -> list[ActionOut]:
    return [_action_out(row) for row in await recent_actions(shop.session, shop.ctx, limit=limit)]


def _action_out(row: Any) -> ActionOut:
    return ActionOut(
        id=row.id,
        variant_id=row.variant_id,
        kind=row.kind,
        detail=dict(row.detail or {}),
        price_before=row.price_before,
        price_after=row.price_after,
        on_hand_before=row.on_hand_before,
        taken_at=row.taken_at,
        measured_at=row.measured_at,
        note=row.note,
    )
