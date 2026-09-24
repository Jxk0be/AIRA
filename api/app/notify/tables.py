"""Who hears from us, how, and what we actually sent.

Every outbound message is logged before it leaves, including the ones we
decide not to send. That log is the answer to three questions that all arrive
eventually: "did you email me about that?", "why did I get four texts on
Sunday?", and — the one that matters legally — "prove this person agreed to be
contacted".

There is no user table yet, so a recipient is a contact row on the tenant. When
real auth lands these become a join onto it; nothing else about the shape
changes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Time

from app.canonical.enums import MessageStatus, NotifyChannel
from app.db import Base
from app.schema import TenantMixin, enum_column, pk


class NotificationPref(TenantMixin, Base):
    """One person at one shop, and the terms on which we may contact them."""

    __tablename__ = "notification_prefs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_notification_prefs_tenant_email"),
        Index("ix_notification_prefs_tenant", "tenant_id"),
        CheckConstraint("max_per_day >= 0", name="max_per_day_non_negative"),
    )

    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(64))
    # Which channels this person has agreed to, e.g. ["email"]. A channel that
    # is not in this list is never used, whatever a caller asks for.
    channels: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    # Shop-local wall clock. Nothing but an urgent alert may arrive between
    # these, and even then only if `allow_urgent_in_quiet_hours` is set.
    quiet_hours_start: Mapped[Any | None] = mapped_column(Time)
    quiet_hours_end: Mapped[Any | None] = mapped_column(Time)
    allow_urgent_in_quiet_hours: Mapped[bool] = mapped_column(nullable=False, default=False)
    # The fatigue ceiling. Alerts above it are suppressed and logged as such,
    # never silently dropped.
    max_per_day: Mapped[int] = mapped_column(nullable=False, default=3)
    wants_digest: Mapped[bool] = mapped_column(nullable=False, default=True)
    # Set when they use the unsubscribe link. Kept rather than deleted: proof
    # that we stopped, and when.
    unsubscribed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # Opaque token in the unsubscribe URL, so one-click works without a login.
    unsubscribe_token: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)

    @property
    def subscribed(self) -> bool:
        return self.is_active and self.unsubscribed_at is None


class OutboundMessage(TenantMixin, Base):
    """Everything we sent, tried to send, or decided not to send.

    `suppressed` is a first-class outcome rather than an absence. "We had an
    urgent alert for you at 11pm and held it until morning" is a thing the
    support conversation needs to be able to show.
    """

    __tablename__ = "outbound_messages"
    __table_args__ = (
        Index("ix_outbound_messages_tenant_created", "tenant_id", "created_at"),
        Index("ix_outbound_messages_tenant_kind", "tenant_id", "kind"),
    )

    id: Mapped[uuid.UUID] = pk()
    channel: Mapped[NotifyChannel] = mapped_column(
        enum_column(NotifyChannel, "notify_channel"), nullable=False
    )
    # "digest", "alert", "series_release", "month_end". Free text, like an
    # insight's kind, so a new message type needs no migration.
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    to_address: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(500))
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_html: Mapped[str | None] = mapped_column(Text)
    status: Mapped[MessageStatus] = mapped_column(
        enum_column(MessageStatus, "message_status"), nullable=False, default=MessageStatus.QUEUED
    )
    # Why it was suppressed, or why sending failed. Written for a human.
    detail: Mapped[str | None] = mapped_column(Text)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    insight_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("insights.id", ondelete="SET NULL")
    )
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
