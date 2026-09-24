"""Deciding whether to send, then sending, then writing down what happened.

The rules live here rather than in any one feature, because a shop owner does
not experience "the anomaly detector's alert budget" and "the series feature's
alert budget" — they experience how many times their phone buzzed on Sunday.

Four gates, in order, and a message that fails any of them is logged as
`suppressed` with the reason rather than dropped:

1. the recipient is active and has not unsubscribed
2. the channel is one they agreed to
3. it is not quiet hours, unless it is urgent and they allowed that
4. they are under their daily ceiling
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext
from app.canonical.enums import InsightSeverity, MessageStatus, NotifyChannel
from app.config import get_settings
from app.db import table_of
from app.notify.base import Delivery, Message, Notifier
from app.notify.providers import notifier_for
from app.notify.tables import NotificationPref, OutboundMessage

log = logging.getLogger(__name__)

# Message kinds that are never rate-limited or held for quiet hours, because
# the owner losing money tonight is the whole point of sending them.
ALWAYS_KINDS = frozenset({"digest_failure", "sync_stale"})


@dataclass(frozen=True, slots=True)
class Recipient:
    """One person we may contact, with the terms attached."""

    id: uuid.UUID
    name: str | None
    email: str
    phone: str | None
    channels: tuple[NotifyChannel, ...]
    quiet_hours_start: time | None
    quiet_hours_end: time | None
    allow_urgent_in_quiet_hours: bool
    max_per_day: int
    wants_digest: bool
    unsubscribe_token: str

    def address_for(self, channel: NotifyChannel) -> str | None:
        if channel is NotifyChannel.SMS:
            return self.phone
        return self.email


@dataclass(frozen=True, slots=True)
class SendResult:
    """What became of one message."""

    recipient: str
    channel: NotifyChannel
    status: MessageStatus
    detail: str | None = None
    message_id: uuid.UUID | None = None

    @property
    def sent(self) -> bool:
        return self.status is MessageStatus.SENT


# --------------------------------------------------------------------------
# Recipients
# --------------------------------------------------------------------------


async def recipients(
    session: AsyncSession, ctx: AnalyticsContext, *, digest_only: bool = False
) -> list[Recipient]:
    query = select(NotificationPref).where(
        NotificationPref.tenant_id == ctx.tenant_id,
        NotificationPref.is_active.is_(True),
        NotificationPref.unsubscribed_at.is_(None),
    )
    if digest_only:
        query = query.where(NotificationPref.wants_digest.is_(True))
    rows = (await session.execute(query.order_by(NotificationPref.created_at))).scalars().all()
    return [
        Recipient(
            id=row.id,
            name=row.name,
            email=row.email,
            phone=row.phone,
            channels=tuple(
                NotifyChannel(c) for c in (row.channels or []) if c in set(NotifyChannel)
            ),
            quiet_hours_start=row.quiet_hours_start,
            quiet_hours_end=row.quiet_hours_end,
            allow_urgent_in_quiet_hours=row.allow_urgent_in_quiet_hours,
            max_per_day=row.max_per_day,
            wants_digest=row.wants_digest,
            unsubscribe_token=row.unsubscribe_token,
        )
        for row in rows
    ]


async def upsert_recipient(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    email: str,
    name: str | None = None,
    phone: str | None = None,
    channels: list[str] | None = None,
    max_per_day: int = 3,
    wants_digest: bool = True,
    quiet_hours: tuple[time, time] | None = None,
) -> uuid.UUID:
    """Add or update a contact. Matching is on email, which is the identity."""
    table = table_of(NotificationPref)
    now = datetime.now(tz=UTC)
    row_id = uuid.uuid4()
    statement = insert(table).values(
        id=row_id,
        tenant_id=tenant_id,
        name=name,
        email=email.strip().lower(),
        phone=phone,
        channels=channels or [NotifyChannel.EMAIL.value],
        quiet_hours_start=quiet_hours[0] if quiet_hours else None,
        quiet_hours_end=quiet_hours[1] if quiet_hours else None,
        max_per_day=max_per_day,
        wants_digest=wants_digest,
        unsubscribe_token=secrets.token_urlsafe(24),
        is_active=True,
    )
    result = await session.execute(
        statement.on_conflict_do_update(
            index_elements=["tenant_id", "email"],
            set_={
                "name": statement.excluded.name,
                "phone": statement.excluded.phone,
                "channels": statement.excluded.channels,
                "quiet_hours_start": statement.excluded.quiet_hours_start,
                "quiet_hours_end": statement.excluded.quiet_hours_end,
                "max_per_day": statement.excluded.max_per_day,
                "wants_digest": statement.excluded.wants_digest,
                "is_active": True,
                "updated_at": now,
            },
        ).returning(table.c.id)
    )
    # Like every other write in this package. `get_session` hands a route a
    # session and never commits for it, so without this the row is rolled back
    # when the request ends: the API answers 200 with the new id, the screen
    # says the contact was saved, and nothing was.
    await session.commit()
    return uuid.UUID(str(result.scalar_one()))


async def unsubscribe(session: AsyncSession, token: str) -> bool:
    """Honour an unsubscribe link. Returns whether the token matched anything.

    Idempotent on purpose: a mail client that prefetches the link twice must
    not turn a successful unsubscribe into an error page.
    """
    table = table_of(NotificationPref)
    now = datetime.now(tz=UTC)
    result = await session.execute(
        update(table)
        .where(table.c.unsubscribe_token == token)
        .values(unsubscribed_at=now, updated_at=now)
        .returning(table.c.id)
    )
    matched = result.first() is not None
    await session.commit()
    return matched


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------


def in_quiet_hours(recipient: Recipient, local_now: datetime) -> bool:
    """Whether now is inside the recipient's quiet window, shop-local.

    Handles the normal case (22:00 to 08:00, which wraps past midnight) as
    well as a daytime window, because a shop that only wants messages in the
    evening is a perfectly reasonable thing to ask for.
    """
    start, end = recipient.quiet_hours_start, recipient.quiet_hours_end
    if start is None or end is None or start == end:
        return False
    now = local_now.time()
    if start < end:
        return start <= now < end
    return now >= start or now < end


async def _sent_today(
    session: AsyncSession, ctx: AnalyticsContext, recipient: Recipient, day: date
) -> int:
    from app.analytics import DateRange

    first, after = DateRange(day, day).bounds(ctx.tz)
    return int(
        (
            await session.execute(
                select(func.count()).where(
                    OutboundMessage.tenant_id == ctx.tenant_id,
                    OutboundMessage.to_address.in_(
                        [a for a in (recipient.email, recipient.phone) if a]
                    ),
                    OutboundMessage.status == MessageStatus.SENT,
                    OutboundMessage.created_at >= first,
                    OutboundMessage.created_at < after,
                )
            )
        ).scalar_one()
    )


async def _log(
    session: AsyncSession,
    ctx: AnalyticsContext,
    *,
    channel: NotifyChannel,
    kind: str,
    to: str,
    subject: str | None,
    text: str,
    html: str | None,
    status: MessageStatus,
    detail: str | None = None,
    provider_message_id: str | None = None,
    insight_id: uuid.UUID | None = None,
) -> uuid.UUID:
    message_id = uuid.uuid4()
    await session.execute(
        insert(table_of(OutboundMessage)).values(
            id=message_id,
            tenant_id=ctx.tenant_id,
            channel=channel,
            kind=kind,
            to_address=to,
            subject=subject,
            body_text=text,
            body_html=html,
            status=status,
            detail=detail,
            provider_message_id=provider_message_id,
            insight_id=insight_id,
            sent_at=datetime.now(tz=UTC) if status is MessageStatus.SENT else None,
        )
    )
    await session.commit()
    return message_id


async def deliver(
    session: AsyncSession,
    ctx: AnalyticsContext,
    recipient: Recipient,
    message: Message,
    *,
    channel: NotifyChannel = NotifyChannel.EMAIL,
    severity: InsightSeverity = InsightSeverity.INFO,
    insight_id: uuid.UUID | None = None,
    notifier: Notifier | None = None,
) -> SendResult:
    """Send one message to one person, or say why we did not.

    The four gates run before any provider is touched, and every outcome —
    including each refusal — leaves a row in `outbound_messages`.
    """
    address = recipient.address_for(channel)
    if address is None:
        return SendResult(
            recipient=recipient.email,
            channel=channel,
            status=MessageStatus.SUPPRESSED,
            detail=f"No {channel.value} address on file for this contact.",
        )

    urgent = severity is InsightSeverity.URGENT or message.kind in ALWAYS_KINDS

    async def suppressed(detail: str) -> SendResult:
        message_id = await _log(
            session,
            ctx,
            channel=channel,
            kind=message.kind,
            to=address,
            subject=message.subject,
            text=message.text,
            html=message.html,
            status=MessageStatus.SUPPRESSED,
            detail=detail,
            insight_id=insight_id,
        )
        return SendResult(
            recipient=address,
            channel=channel,
            status=MessageStatus.SUPPRESSED,
            detail=detail,
            message_id=message_id,
        )

    if channel not in recipient.channels:
        return await suppressed(f"{recipient.email} has not turned on {channel.value}.")

    local_now = datetime.now(tz=UTC).astimezone(ctx.tz)
    if in_quiet_hours(recipient, local_now) and not (
        urgent and recipient.allow_urgent_in_quiet_hours
    ):
        return await suppressed(
            f"Quiet hours for {recipient.email} "
            f"({recipient.quiet_hours_start}–{recipient.quiet_hours_end} shop time)."
        )

    if not urgent and recipient.max_per_day:
        already = await _sent_today(session, ctx, recipient, local_now.date())
        if already >= recipient.max_per_day:
            return await suppressed(
                f"{recipient.email} has already had {already} messages today "
                f"(their limit is {recipient.max_per_day})."
            )

    notifier = notifier or notifier_for(channel)
    outgoing = Message(
        to=address,
        subject=message.subject,
        text=message.text,
        html=message.html,
        kind=message.kind,
        reply_to=message.reply_to,
        headers=message.headers,
    )
    try:
        delivery = await notifier.send(outgoing)
    except Exception as exc:  # a provider blowing up must still leave a record
        log.exception("notifier %s failed", type(notifier).__name__)
        delivery = Delivery(ok=False, detail=f"{type(exc).__name__}: {exc}")

    status = MessageStatus.SENT if delivery.ok else MessageStatus.FAILED
    message_id = await _log(
        session,
        ctx,
        channel=notifier.channel,
        kind=message.kind,
        to=address,
        subject=message.subject,
        text=message.text,
        html=message.html,
        status=status,
        detail=delivery.detail,
        provider_message_id=delivery.provider_message_id,
        insight_id=insight_id,
    )
    return SendResult(
        recipient=address,
        channel=notifier.channel,
        status=status,
        detail=delivery.detail,
        message_id=message_id,
    )


async def broadcast(
    session: AsyncSession,
    ctx: AnalyticsContext,
    message: Message,
    *,
    channel: NotifyChannel = NotifyChannel.EMAIL,
    severity: InsightSeverity = InsightSeverity.INFO,
    insight_id: uuid.UUID | None = None,
    digest_only: bool = False,
    notifier: Notifier | None = None,
    personalise: bool = True,
) -> list[SendResult]:
    """Send to everyone at a shop who has agreed to hear this.

    Each recipient gets their own unsubscribe link, because a shared one
    unsubscribes whoever clicks it last.
    """
    people = await recipients(session, ctx, digest_only=digest_only)
    results: list[SendResult] = []
    for person in people:
        body = message
        if personalise:
            link = unsubscribe_url(person.unsubscribe_token)
            body = Message(
                to=person.email,
                subject=message.subject,
                text=message.text.replace("{{unsubscribe_url}}", link),
                html=message.html.replace("{{unsubscribe_url}}", link) if message.html else None,
                kind=message.kind,
                reply_to=message.reply_to,
                headers={**message.headers, "List-Unsubscribe": f"<{link}>"},
            )
        results.append(
            await deliver(
                session,
                ctx,
                person,
                body,
                channel=channel,
                severity=severity,
                insight_id=insight_id,
                notifier=notifier,
            )
        )
    return results


def unsubscribe_url(token: str) -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/unsubscribe/{token}"


def app_link(tenant_slug: str, path: str = "") -> str:
    """A deep link into the app for one shop, for use in an email."""
    base = get_settings().app_base_url.rstrip("/")
    return f"{base}/{tenant_slug}/{path.lstrip('/')}" if path else f"{base}/{tenant_slug}"


async def recent_messages(
    session: AsyncSession, ctx: AnalyticsContext, *, limit: int = 50, days: int = 30
) -> list[OutboundMessage]:
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)
    rows = (
        (
            await session.execute(
                select(OutboundMessage)
                .where(
                    OutboundMessage.tenant_id == ctx.tenant_id,
                    OutboundMessage.created_at >= cutoff,
                )
                .order_by(OutboundMessage.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)
