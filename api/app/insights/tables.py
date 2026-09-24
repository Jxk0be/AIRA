"""Where a proactive finding lives, and what happened to it afterwards.

An insight is the one shape every feature in this half of the product produces:
something worth doing, with the numbers behind it and, where we can work one
out honestly, a dollar figure. The digest, the alerts and the "value this
month" card are all views over this table, which is why the reorder assistant
and the anomaly detectors did not each need their own.

Two columns carry most of the weight.

`dedupe_key` is what stops the same problem being announced every hour. A
detector builds one per finding — the variant and the week it is about, the
month a packet covers — and re-running the detector updates the existing row
instead of adding another.

`evidence` is the exact numbers and ids the finding was made from. Everything
downstream, including the sentence the model writes for the digest, is checked
against it, so a figure that never appeared here can never reach the owner.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.canonical.enums import InsightSeverity, InsightStatus
from app.db import Base
from app.schema import MONEY, TenantMixin, enum_column, pk


class Insight(TenantMixin, Base):
    __tablename__ = "insights"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_insights_tenant_dedupe"),
        Index("ix_insights_tenant_status", "tenant_id", "status"),
        Index("ix_insights_tenant_kind_created", "tenant_id", "kind", "created_at"),
        CheckConstraint(
            "dollar_impact is null or dollar_impact >= 0", name="dollar_impact_non_negative"
        ),
    )

    id: Mapped[uuid.UUID] = pk()
    # Which detector made it: "reorder", "dead_stock", "sales_drop". Free text
    # rather than an enum because a new detector must not need a migration.
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[InsightSeverity] = mapped_column(
        enum_column(InsightSeverity, "insight_severity"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    # What acting on this is worth, or None when no honest figure exists. None
    # is common and is never rendered as $0.
    dollar_impact: Mapped[Decimal | None] = mapped_column(MONEY)
    # How the figure was reached: ids, counts and the intermediate numbers.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # What the app should offer to do about it — a route to open, a draft PO to
    # review, a markdown to apply.
    suggested_action: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[InsightStatus] = mapped_column(
        enum_column(InsightStatus, "insight_status"), nullable=False, default=InsightStatus.NEW
    )
    dedupe_key: Mapped[str] = mapped_column(String(300), nullable=False)
    # The shop-local day the detector was reasoning about. Two runs on the same
    # day produce the same finding; a week later is a new one.
    as_of: Mapped[date] = mapped_column(nullable=False)
    # After this, the finding is stale whether or not anyone looked: a reorder
    # suggestion from three weeks ago is not advice, it is noise.
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    acted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # Set by Snooze. While it is in the future the insight stays out of the
    # inbox and out of the digest, but it is not dismissed.
    snoozed_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # When this went out in a digest or an alert, so nothing is sent twice.
    notified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # "Useful" / "Not useful". None means nobody said. This is the only input
    # we have for tuning a detector's thresholds, so it is kept per insight
    # rather than aggregated away.
    was_useful: Mapped[bool | None] = mapped_column()
    feedback_note: Mapped[str | None] = mapped_column(Text)


class InsightOutcome(TenantMixin, Base):
    """What measurably happened after the owner acted.

    The renewal argument, and the reason to be conservative here: an outcome we
    cannot attribute honestly is better left unrecorded than inflated.
    `attributed_revenue` is null unless the money can be tied to the action,
    and `notes` says how it was tied.
    """

    __tablename__ = "insight_outcomes"
    __table_args__ = (
        Index("ix_insight_outcomes_tenant_measured", "tenant_id", "measured_at"),
        Index("ix_insight_outcomes_insight", "insight_id"),
    )

    id: Mapped[uuid.UUID] = pk()
    insight_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("insights.id", ondelete="CASCADE"), nullable=False
    )
    measured_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    # "units_per_week", "stockout_days", "cash_at_cost" — named so a chart can
    # group like with like.
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    before_value: Mapped[Decimal | None] = mapped_column(MONEY)
    after_value: Mapped[Decimal | None] = mapped_column(MONEY)
    attributed_revenue: Mapped[Decimal | None] = mapped_column(MONEY)
    notes: Mapped[str | None] = mapped_column(Text)
