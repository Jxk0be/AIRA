"""HTTP for the Staffing screen: the heatmap, the rota editor, the observations."""

from __future__ import annotations

import uuid
from datetime import date, time
from decimal import Decimal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.http import ShopDep
from app.staffing import (
    WEEKDAY_NAMES,
    busiest_hours,
    delete_shift,
    heatmap,
    observe,
    set_shift,
    shifts,
)

router = APIRouter(tags=["staffing"])


class CellOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: int
    hour: int
    orders_per_week: Decimal
    net_sales_per_week: Decimal
    weeks_observed: int


class ObservationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    weekday: int
    weekday_name: str
    hours: list[int]
    orders_per_hour: Decimal
    staff_count: int | None
    sentence: str
    labour_cost: Decimal | None


class ShiftOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID | None
    location_id: uuid.UUID | None
    weekday: int
    start_time: time
    end_time: time
    staff_count: int
    note: str | None


class HeatmapOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_id: uuid.UUID | None
    location_name: str
    weeks: int
    period_start: date
    period_end: date
    weekday_names: list[str]
    cells: list[CellOut]
    event_cells: list[CellOut]
    event_days_excluded: int


class StaffingOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant: str
    heatmaps: list[HeatmapOut]
    shifts: list[ShiftOut]
    observations: list[ObservationOut]
    hourly_labour_cost: Decimal | None


class ShiftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    staff_count: int = Field(ge=1, le=50)
    location_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _ends_after_it_starts(self) -> ShiftIn:
        if self.end_time <= self.start_time:
            raise ValueError("a shift has to end after it starts")
        return self


@router.get("/tenants/{tenant}/staffing", response_model=StaffingOut)
async def staffing(
    shop: ShopDep, as_of: date | None = None, include_events: bool = False
) -> StaffingOut:
    grids = await heatmap(shop.session, shop.ctx, as_of=as_of, include_events=include_events)
    rota = await shifts(shop.session, shop.ctx)

    hourly = shop.ctx.setting("hourly_labour_cost", None)
    cost = Decimal(str(hourly)) if hourly else None
    observations = observe(grids[0], rota, hourly_labour_cost=cost) if grids else []

    return StaffingOut(
        tenant=shop.slug,
        heatmaps=[
            HeatmapOut(
                location_id=grid.location_id,
                location_name=grid.location_name,
                weeks=grid.weeks,
                period_start=grid.period.start,
                period_end=grid.period.end,
                weekday_names=list(WEEKDAY_NAMES),
                cells=[_cell(cell) for cell in grid.cells],
                event_cells=[_cell(cell) for cell in grid.event_cells],
                event_days_excluded=grid.event_days_excluded,
            )
            for grid in grids
        ],
        shifts=[
            ShiftOut(
                id=shift.id,
                location_id=shift.location_id,
                weekday=shift.weekday,
                start_time=shift.start_time,
                end_time=shift.end_time,
                staff_count=shift.staff_count,
                note=shift.note,
            )
            for shift in rota
        ],
        observations=[
            ObservationOut(
                kind=o.kind,
                weekday=o.weekday,
                weekday_name=o.weekday_name,
                hours=list(o.hours),
                orders_per_hour=o.orders_per_hour,
                staff_count=o.staff_count,
                sentence=o.sentence,
                labour_cost=o.labour_cost,
            )
            for o in observations
        ],
        hourly_labour_cost=cost,
    )


@router.put("/tenants/{tenant}/staffing/shifts", response_model=uuid.UUID)
async def upsert_shift(shop: ShopDep, body: ShiftIn) -> uuid.UUID:
    return await set_shift(
        shop.session,
        shop.ctx,
        weekday=body.weekday,
        start_time=body.start_time,
        end_time=body.end_time,
        staff_count=body.staff_count,
        location_id=body.location_id,
        note=body.note,
    )


@router.delete("/tenants/{tenant}/staffing/shifts/{shift_id}", status_code=204)
async def remove_shift(shop: ShopDep, shift_id: uuid.UUID) -> None:
    await delete_shift(shop.session, shop.ctx, shift_id)


@router.get("/tenants/{tenant}/staffing/busiest")
async def busiest(
    shop: ShopDep, weekday: int | None = None, limit: int = 8
) -> list[dict[str, object]]:
    return await busiest_hours(shop.session, shop.ctx, weekday=weekday, limit=limit)


def _cell(cell: object) -> CellOut:
    from app.staffing.service import Cell

    assert isinstance(cell, Cell)
    return CellOut(
        weekday=cell.weekday,
        hour=cell.hour,
        orders_per_week=cell.orders_per_occurrence,
        net_sales_per_week=cell.sales_per_occurrence,
        weeks_observed=cell.observations,
    )
