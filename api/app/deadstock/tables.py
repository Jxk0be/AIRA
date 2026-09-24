"""What the owner did about a stale item, so we can check whether it worked.

A list of stale items is a guilt trip. A plan per item that somebody actually
carried out, with a before and an after, is the thing worth paying for — and
the "after" is only measurable because the action was logged with a date and a
price at the moment it was taken.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.schema import MONEY, QUANTITY, TenantMixin, pk


class RescueAction(TenantMixin, Base):
    """One thing done to shift one stale item."""

    __tablename__ = "rescue_actions"
    __table_args__ = (
        Index("ix_rescue_actions_tenant_taken", "tenant_id", "taken_at"),
        Index("ix_rescue_actions_tenant_variant", "tenant_id", "variant_id"),
    )

    id: Mapped[uuid.UUID] = pk()
    variant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("variants.id", ondelete="CASCADE"), nullable=False
    )
    insight_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("insights.id", ondelete="SET NULL")
    )
    # "markdown", "bundle", "move", "return_to_vendor". Free text like an
    # insight's kind: a new rescue play should not need a migration.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # The shape depends on the kind: a markdown carries the new price, a bundle
    # the partner variant, a move the destination location.
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Captured at the moment of the action, because the variant's own price and
    # stock will have moved on by the time we measure.
    price_before: Mapped[Decimal | None] = mapped_column(MONEY)
    price_after: Mapped[Decimal | None] = mapped_column(MONEY)
    on_hand_before: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False, default=Decimal(0))
    unit_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    taken_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    # Set once the 30-day window has been measured, so the nightly job knows
    # what it has already looked at.
    measured_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
