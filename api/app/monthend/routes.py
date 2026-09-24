"""HTTP for the month-end packets: list, generate, download, email."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field

from app.analytics import DateRange
from app.http import ShopDep, not_found
from app.monthend import (
    email_packet,
    generate,
    get_packet,
    list_packets,
    pdf_of,
    workbook_of,
)

router = APIRouter(tags=["month end"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class PacketOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    label: str
    period_start: date
    period_end: date
    generated_at: datetime
    notes: list[str]
    emailed_to: str | None
    # Set on the detail response only; the list stays small.
    figures: dict[str, Any] | None = None


class GenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Omit both for last month, which is what the 1st-of-the-month job does.
    year: int | None = Field(default=None, ge=2000, le=2100)
    month: int | None = Field(default=None, ge=1, le=12)


class EmailIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bookkeeper: str | None = Field(default=None, max_length=320)


@router.get("/tenants/{tenant}/month-end", response_model=list[PacketOut])
async def packets(shop: ShopDep) -> list[PacketOut]:
    return [
        PacketOut(
            id=packet.id,
            label=packet.label,
            period_start=packet.period_start,
            period_end=packet.period_end,
            generated_at=packet.generated_at,
            notes=packet.notes,
            emailed_to=packet.emailed_to,
        )
        for packet in await list_packets(shop.session, shop.ctx)
    ]


@router.post("/tenants/{tenant}/month-end", response_model=PacketOut, status_code=201)
async def make_packet(shop: ShopDep, body: GenerateIn) -> PacketOut:
    period = None
    if body.year and body.month:
        period = DateRange(*_month_bounds(body.year, body.month))
    packet = await generate(shop.session, shop.ctx, period)
    return PacketOut(
        id=packet.id,
        label=packet.label,
        period_start=packet.period_start,
        period_end=packet.period_end,
        generated_at=packet.generated_at,
        notes=packet.notes,
        emailed_to=packet.emailed_to,
        figures=packet.figures,  # type: ignore[arg-type]
    )


@router.get("/tenants/{tenant}/month-end/{packet_id}", response_model=PacketOut)
async def one_packet(shop: ShopDep, packet_id: uuid.UUID) -> PacketOut:
    packet = await get_packet(shop.session, shop.ctx, packet_id)
    if packet is None:
        raise not_found("packet")
    return PacketOut(
        id=packet.id,
        label=packet.label,
        period_start=packet.period_start,
        period_end=packet.period_end,
        generated_at=packet.generated_at,
        notes=packet.notes,
        emailed_to=packet.emailed_to,
        figures=packet.figures,  # type: ignore[arg-type]
    )


@router.get("/tenants/{tenant}/month-end/{packet_id}.pdf")
async def packet_pdf_download(shop: ShopDep, packet_id: uuid.UUID) -> Response:
    packet = await get_packet(shop.session, shop.ctx, packet_id)
    if packet is None:
        raise not_found("packet")
    return Response(
        content=pdf_of(packet),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{packet.filename}.pdf"'},
    )


@router.get("/tenants/{tenant}/month-end/{packet_id}.xlsx")
async def packet_workbook_download(shop: ShopDep, packet_id: uuid.UUID) -> Response:
    packet = await get_packet(shop.session, shop.ctx, packet_id)
    if packet is None:
        raise not_found("packet")
    return Response(
        content=workbook_of(packet),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{packet.filename}.xlsx"'},
    )


@router.post("/tenants/{tenant}/month-end/{packet_id}/email")
async def email(shop: ShopDep, packet_id: uuid.UUID, body: EmailIn) -> dict[str, int]:
    packet = await get_packet(shop.session, shop.ctx, packet_id)
    if packet is None:
        raise not_found("packet")
    bookkeeper = body.bookkeeper or shop.ctx.setting("bookkeeper_email", None)
    sent = await email_packet(
        shop.session,
        shop.ctx,
        packet,
        bookkeeper=str(bookkeeper) if bookkeeper else None,
    )
    return {"sent": sent}


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    return start, date.fromordinal(end.toordinal() - 1)
