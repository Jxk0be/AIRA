"""What a detector hands back, and the contract it satisfies.

A detector is deliberately a small thing: given a shop and a date, return a
list of drafts. It does no I/O of its own beyond reading canonical data through
the semantic layer, it does not decide whether anyone is told, and it does not
write to the insights table. That is the job of `app.insights.service`, which
is why a detector can be tested by calling it and looking at what comes back.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext
from app.canonical.enums import InsightSeverity


@dataclass(frozen=True, slots=True)
class InsightDraft:
    """One finding, before it has met the table.

    `dedupe_key` is the detector's promise about identity: two drafts with the
    same key are the same problem, however many times the detector runs. Build
    it out of what the finding is *about* — the variant, the week, the vendor —
    and never out of a timestamp, or nothing will ever deduplicate.
    """

    kind: str
    severity: InsightSeverity
    title: str
    summary: str
    dedupe_key: str
    # The exact numbers and ids this was worked out from. Anything the owner
    # will eventually read has to be checkable against this.
    evidence: dict[str, Any] = field(default_factory=dict)
    suggested_action: dict[str, Any] = field(default_factory=dict)
    dollar_impact: Decimal | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.dollar_impact is not None and self.dollar_impact < 0:
            raise ValueError(f"{self.kind}: dollar_impact must not be negative")
        if not self.dedupe_key:
            raise ValueError(f"{self.kind}: a draft needs a dedupe_key")


@runtime_checkable
class Detector(Protocol):
    """Everything that produces insights.

    `requires` gates the detector on the tenant's declared capabilities, the
    same way a tool is gated: a shop whose POS keeps no costs never runs the
    margin-shaped detectors at all, rather than running them and apologising.
    """

    kind: str
    # How often it is worth running, in the shop's own timezone.
    schedule: str
    requires: tuple[str, ...]

    async def run(
        self, session: AsyncSession, ctx: AnalyticsContext, as_of: date
    ) -> list[InsightDraft]: ...


@dataclass(frozen=True, slots=True)
class DetectorRun:
    """What one detector did on one day, for the job log."""

    kind: str
    drafts: int
    created: int
    updated: int
    skipped_reason: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True, slots=True)
class ValueLedger:
    """What we can honestly claim to have been worth over a window.

    Split three ways on purpose. `attributed_revenue` is money we can point at
    a sale for; `cash_recovered` is stock turned back into money at cost;
    `flagged_impact` is what the open, un-acted findings say is available. The
    third is the weakest claim and is never added to the other two.
    """

    tenant: str
    start: date
    end: date
    insights_created: int
    insights_acted: int
    attributed_revenue: Decimal
    cash_recovered: Decimal
    flagged_impact: Decimal
    outcomes: int

    @property
    def has_anything_to_show(self) -> bool:
        """Whether there is a claim worth printing.

        A digest that says "we delivered $0 this month" is worse than a digest
        that says nothing, so the card and the email both check this first.
        """
        return bool(self.insights_acted or self.attributed_revenue or self.cash_recovered)


@dataclass(frozen=True, slots=True)
class StoredInsight:
    """An insight as the inbox, the digest and the agent all see it."""

    id: uuid.UUID
    kind: str
    severity: InsightSeverity
    status: str
    title: str
    summary: str
    dollar_impact: Decimal | None
    evidence: dict[str, Any]
    suggested_action: dict[str, Any]
    as_of: date
    created_at: datetime
    expires_at: datetime | None
    snoozed_until: datetime | None
    notified_at: datetime | None
    was_useful: bool | None
