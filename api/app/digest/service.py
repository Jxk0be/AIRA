"""Generating Monday's email, previewing it, and sending it.

One guard sits in front of everything: if the last sale we hold is older than
the configured staleness window, no digest goes out. A cheerful email saying
trade was down 90% when the truth is that an API key expired on Thursday is the
fastest way to lose a shop's trust in every other number we show them. They get
a short "we could not reach your system" instead, which is both true and
actionable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext
from app.analytics.queries import data_window
from app.canonical.enums import InsightSeverity
from app.config import get_settings
from app.digest.build import Digest, build, insight_ids
from app.digest.render import Rendered, render, write_copy
from app.insights import mark_notified
from app.llm import Phraser
from app.notify import Message, Notifier, app_link, broadcast
from app.notify.service import Recipient, SendResult, deliver

log = logging.getLogger(__name__)


class DigestSkipped(Exception):
    """There is a reason not to send this week, and it is worth recording."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True)
class Prepared:
    digest: Digest
    rendered: Rendered

    @property
    def subject(self) -> str:
        return self.rendered.subject


async def prepare(
    session: AsyncSession,
    ctx: AnalyticsContext,
    *,
    as_of: date | None = None,
    phraser: Phraser | None = None,
) -> Prepared:
    """Build and write the digest, without sending anything.

    What the preview page shows, and what the send uses: the owner's "what
    will Monday's email say?" and the actual Monday email are the same code
    path, so the preview cannot be wrong about it.
    """
    digest = await build(session, ctx, as_of=as_of)
    copy, source = await write_copy(digest, phraser)
    return Prepared(digest=digest, rendered=render(digest, copy, source=source))


async def staleness(session: AsyncSession, ctx: AnalyticsContext) -> timedelta | None:
    """How long since the last sale we hold, or None if we hold none."""
    _, last_at = await data_window(session, ctx)
    if last_at is None:
        return None
    return datetime.now(tz=UTC) - last_at


async def send(
    session: AsyncSession,
    ctx: AnalyticsContext,
    *,
    as_of: date | None = None,
    phraser: Phraser | None = None,
    notifier: Notifier | None = None,
    force: bool = False,
) -> list[SendResult]:
    """Send the weekly digest to everyone at this shop who wants it.

    `force` is for the "send me a test" button and skips only the staleness
    guard — never the consent or quiet-hours checks, which belong to the
    recipient rather than to the caller.
    """
    settings = get_settings()
    age = await staleness(session, ctx)
    limit = timedelta(hours=settings.digest_stale_hours)

    if not force and (age is None or age > limit):
        results = await _send_stale_notice(session, ctx, age, notifier=notifier)
        told = sum(1 for result in results if result.sent)
        how_old = "nothing has ever synced" if age is None else f"{age.days} days old"
        raise DigestSkipped(
            f"the last sale we hold is {how_old}; {told} people were told instead"
        )

    prepared = await prepare(session, ctx, as_of=as_of, phraser=phraser)
    results = await broadcast(
        session,
        ctx,
        Message(
            to="",  # filled in per recipient by broadcast
            subject=prepared.subject,
            text=prepared.rendered.text,
            html=prepared.rendered.html,
            kind="digest",
        ),
        digest_only=True,
        notifier=notifier,
    )
    if any(result.sent for result in results):
        await mark_notified(session, ctx.tenant_id, insight_ids(prepared.digest))
        await session.commit()
    return results


async def send_test(
    session: AsyncSession,
    ctx: AnalyticsContext,
    recipient: Recipient,
    *,
    as_of: date | None = None,
    phraser: Phraser | None = None,
    notifier: Notifier | None = None,
) -> SendResult:
    """One copy, to one person, on demand. Does not mark anything as notified."""
    prepared = await prepare(session, ctx, as_of=as_of, phraser=phraser)
    from app.notify import unsubscribe_url

    link = unsubscribe_url(recipient.unsubscribe_token)
    return await deliver(
        session,
        ctx,
        recipient,
        Message(
            to=recipient.email,
            subject=f"[test] {prepared.subject}",
            text=prepared.rendered.text.replace("{{unsubscribe_url}}", link),
            html=prepared.rendered.html.replace("{{unsubscribe_url}}", link),
            kind="digest",
        ),
        notifier=notifier,
    )


async def _send_stale_notice(
    session: AsyncSession,
    ctx: AnalyticsContext,
    age: timedelta | None,
    *,
    notifier: Notifier | None = None,
) -> list[SendResult]:
    """Tell the owner we could not reach their system, instead of the digest."""
    how_old = (
        "We have never managed to read any sales from it."
        if age is None
        else f"The most recent sale we have is {age.days} days old."
    )
    text = (
        f"We could not put together this week's digest for {ctx.name}.\n\n"
        f"{how_old} That usually means the connection to your system needs attention — "
        "an expired key, a changed password, or the system being unreachable.\n\n"
        f"Check the connection: {app_link(ctx.slug, 'data')}\n\n"
        "Nothing is lost: as soon as it reconnects, everything syncs and the figures "
        "fill back in.\n\n"
        "Stop these emails: {{unsubscribe_url}}"
    )
    return await broadcast(
        session,
        ctx,
        Message(
            to="",
            subject=f"{ctx.name}: we could not reach your system",
            text=text,
            kind="digest_failure",
        ),
        severity=InsightSeverity.URGENT,
        digest_only=True,
        notifier=notifier,
    )
