"""Generating, storing, listing and emailing a month-end packet."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext, DateRange
from app.db import table_of
from app.monthend.packet import Packet, build, month_before
from app.monthend.render import packet_pdf, packet_workbook
from app.monthend.tables import MonthEndPacket
from app.notify import Message, Notifier, app_link, broadcast


@dataclass(slots=True)
class StoredPacket:
    id: uuid.UUID
    period_start: date
    period_end: date
    generated_at: datetime
    figures: dict[str, object]
    notes: list[str]
    emailed_to: str | None

    @property
    def label(self) -> str:
        return self.period_start.strftime("%B %Y")

    @property
    def filename(self) -> str:
        return f"month-end-{self.period_start:%Y-%m}"


async def generate(
    session: AsyncSession,
    ctx: AnalyticsContext,
    period: DateRange | None = None,
    *,
    as_of: date | None = None,
) -> StoredPacket:
    """Assemble a month and store its figures.

    Re-running for a month that already has a packet replaces it. That is what
    makes the monthly job safe to retry, and it is also the right behaviour
    when a late sync fills in the last two days of the month.
    """
    span = period or month_before(as_of or ctx.today())
    packet = await build(session, ctx, span)
    return await store(session, ctx, packet)


async def store(session: AsyncSession, ctx: AnalyticsContext, packet: Packet) -> StoredPacket:
    now = datetime.now(tz=UTC)
    figures = packet.figures()
    table = table_of(MonthEndPacket)
    statement = insert(table).values(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        period_start=packet.period.start,
        period_end=packet.period.end,
        generated_at=now,
        figures=figures,
        notes=packet.notes,
    )
    result = await session.execute(
        statement.on_conflict_do_update(
            index_elements=["tenant_id", "period_start"],
            set_={
                "period_end": statement.excluded.period_end,
                "generated_at": now,
                "figures": statement.excluded.figures,
                "notes": statement.excluded.notes,
                "updated_at": now,
            },
        ).returning(table.c.id)
    )
    packet_id = uuid.UUID(str(result.scalar_one()))
    await session.commit()
    return StoredPacket(
        id=packet_id,
        period_start=packet.period.start,
        period_end=packet.period.end,
        generated_at=now,
        figures=figures,
        notes=packet.notes,
        emailed_to=None,
    )


async def list_packets(
    session: AsyncSession, ctx: AnalyticsContext, *, limit: int = 24
) -> list[StoredPacket]:
    rows = (
        (
            await session.execute(
                select(MonthEndPacket)
                .where(MonthEndPacket.tenant_id == ctx.tenant_id)
                .order_by(MonthEndPacket.period_start.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [_view(row) for row in rows]


async def get_packet(
    session: AsyncSession, ctx: AnalyticsContext, packet_id: uuid.UUID
) -> StoredPacket | None:
    row = (
        await session.execute(
            select(MonthEndPacket).where(
                MonthEndPacket.id == packet_id, MonthEndPacket.tenant_id == ctx.tenant_id
            )
        )
    ).scalar_one_or_none()
    return _view(row) if row else None


def _view(row: MonthEndPacket) -> StoredPacket:
    return StoredPacket(
        id=row.id,
        period_start=row.period_start,
        period_end=row.period_end,
        generated_at=row.generated_at,
        figures=dict(row.figures or {}),
        notes=list(row.notes or []),
        emailed_to=row.emailed_to,
    )


def pdf_of(packet: StoredPacket) -> bytes:
    return packet_pdf(
        dict(packet.figures),
        generated=packet.generated_at.strftime("%d %b %Y"),
    )


def workbook_of(packet: StoredPacket) -> bytes:
    return packet_workbook(dict(packet.figures))


async def email_packet(
    session: AsyncSession,
    ctx: AnalyticsContext,
    packet: StoredPacket,
    *,
    bookkeeper: str | None = None,
    notifier: Notifier | None = None,
) -> int:
    """Tell the owner (and optionally the bookkeeper) the packet is ready.

    A link rather than an attachment. A month-end packet is a shop's financial
    summary, and mailing one as a file to an address typed into a settings box
    is a data-loss incident waiting for a typo.
    """
    link = app_link(ctx.slug, f"month-end/{packet.id}")
    failures = [note for note in packet.notes if "gap of" in note]
    caveat = "\n\nWorth reading first: " + failures[0] if failures else ""
    text = (
        f"{packet.label} is ready for {ctx.name}.\n\n"
        f"Download the PDF and the spreadsheet here: {link}"
        f"{caveat}\n\n"
        "Figures are as recorded in your POS. Tax shown is what was collected, not what "
        "is owed.\n\n"
        "Stop these emails: {{unsubscribe_url}}"
    )
    results = await broadcast(
        session,
        ctx,
        Message(
            to="",
            subject=f"{ctx.name}: {packet.label} month-end packet",
            text=text,
            kind="month_end",
        ),
        notifier=notifier,
    )
    sent = sum(1 for result in results if result.sent)

    if bookkeeper:
        from app.canonical.enums import NotifyChannel
        from app.notify.service import Recipient, deliver

        result = await deliver(
            session,
            ctx,
            Recipient(
                id=uuid.uuid4(),
                name="Bookkeeper",
                email=bookkeeper,
                phone=None,
                channels=(NotifyChannel.EMAIL,),
                quiet_hours_start=None,
                quiet_hours_end=None,
                allow_urgent_in_quiet_hours=False,
                max_per_day=0,
                wants_digest=False,
                unsubscribe_token="",
            ),
            Message(
                to=bookkeeper,
                subject=f"{ctx.name}: {packet.label} month-end packet",
                text=text.replace("Stop these emails: {{unsubscribe_url}}", ""),
                kind="month_end",
            ),
            notifier=notifier,
        )
        sent += 1 if result.sent else 0

    if sent:
        table = table_of(MonthEndPacket)
        await session.execute(
            update(table)
            .where(table.c.id == packet.id)
            .values(emailed_to=bookkeeper, emailed_at=datetime.now(tz=UTC))
        )
        await session.commit()
    return sent
