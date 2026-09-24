"""HTTP for the insights inbox and the value card.

Canonical-only, like `app.dashboard.routes`: nothing here knows which platform
a tenant runs. The inbox is the one screen that the whole proactive half of the
product funnels into, so the shape of these responses is deliberately flat —
one list of things, each with a title, a figure, evidence and one button.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from app.canonical.enums import InsightStatus
from app.http import ShopDep, not_found
from app.insights import (
    counts_by_status,
    list_insights,
    record_feedback,
    run_detectors,
    set_status,
    value_ledger,
)
from app.insights.models import StoredInsight

router = APIRouter(tags=["insights"])


class InsightOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    kind: str
    severity: str
    status: str
    title: str
    summary: str
    dollar_impact: Decimal | None
    # The exact numbers behind the claim. Shown when the owner expands a row,
    # because "where did that come from" has to have an answer on screen.
    evidence: dict[str, Any]
    suggested_action: dict[str, Any]
    as_of: date
    created_at: datetime
    expires_at: datetime | None
    snoozed_until: datetime | None
    was_useful: bool | None

    @classmethod
    def of(cls, insight: StoredInsight) -> InsightOut:
        return cls(
            id=insight.id,
            kind=insight.kind,
            severity=str(insight.severity),
            status=insight.status,
            title=insight.title,
            summary=insight.summary,
            dollar_impact=insight.dollar_impact,
            evidence=insight.evidence,
            suggested_action=insight.suggested_action,
            as_of=insight.as_of,
            created_at=insight.created_at,
            expires_at=insight.expires_at,
            snoozed_until=insight.snoozed_until,
            was_useful=insight.was_useful,
        )


class Inbox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant: str
    insights: list[InsightOut]
    counts: dict[str, int]
    kinds: list[str]


class ValueOut(BaseModel):
    """What we can honestly say we were worth this month."""

    model_config = ConfigDict(extra="forbid")

    tenant: str
    start: date
    end: date
    insights_created: int
    insights_acted: int
    attributed_revenue: Decimal
    cash_recovered: Decimal
    # What the open findings say is still available. Never added to the two
    # figures above, because it is a claim about the future, not the past.
    flagged_impact: Decimal
    outcomes: int
    has_anything_to_show: bool


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["seen", "acted", "dismissed", "snoozed"]
    snooze_days: int | None = Field(default=None, ge=1, le=90)


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    useful: bool
    note: str | None = Field(default=None, max_length=500)


@router.get("/tenants/{tenant}/insights", response_model=Inbox)
async def inbox(
    shop: ShopDep,
    kind: str | None = None,
    status: str | None = Query(default="open"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Inbox:
    rows = await list_insights(
        shop.session, shop.ctx, kind=kind, status=status, limit=limit, offset=offset
    )
    counts = await counts_by_status(shop.session, shop.ctx)
    return Inbox(
        tenant=shop.slug,
        insights=[InsightOut.of(row) for row in rows],
        counts=counts,
        kinds=sorted({row.kind for row in rows}),
    )


@router.post("/tenants/{tenant}/insights/{insight_id}/status", response_model=InsightOut)
async def change_status(shop: ShopDep, insight_id: uuid.UUID, body: StatusIn) -> InsightOut:
    updated = await set_status(
        shop.session,
        shop.ctx.tenant_id,
        insight_id,
        InsightStatus(body.status),
        snooze_days=body.snooze_days,
    )
    if updated is None:
        raise not_found("insight")
    return InsightOut.of(updated)


@router.post("/tenants/{tenant}/insights/{insight_id}/feedback", status_code=204)
async def feedback(shop: ShopDep, insight_id: uuid.UUID, body: FeedbackIn) -> None:
    await record_feedback(shop.session, shop.ctx.tenant_id, insight_id, body.useful, body.note)


@router.get("/tenants/{tenant}/value", response_model=ValueOut)
async def value(
    shop: ShopDep,
    start: date | None = None,
    end: date | None = None,
) -> ValueOut:
    """The "value this month" card. Defaults to the current month to date."""
    today = shop.ctx.today()
    ledger = await value_ledger(shop.session, shop.ctx, start or today.replace(day=1), end or today)
    return ValueOut(
        tenant=shop.slug,
        start=ledger.start,
        end=ledger.end,
        insights_created=ledger.insights_created,
        insights_acted=ledger.insights_acted,
        attributed_revenue=ledger.attributed_revenue,
        cash_recovered=ledger.cash_recovered,
        flagged_impact=ledger.flagged_impact,
        outcomes=ledger.outcomes,
        has_anything_to_show=ledger.has_anything_to_show,
    )


class DetectorRunOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    drafts: int
    created: int
    updated: int
    skipped_reason: str | None
    error: str | None


@router.post("/tenants/{tenant}/insights/run", response_model=list[DetectorRunOut])
async def run_now(shop: ShopDep, as_of: date | None = None) -> list[DetectorRunOut]:
    """Run every detector now. The "check again" button on the inbox."""
    runs = await run_detectors(shop.session, shop.ctx, as_of)
    return [
        DetectorRunOut(
            kind=run.kind,
            drafts=run.drafts,
            created=run.created,
            updated=run.updated,
            skipped_reason=run.skipped_reason,
            error=run.error,
        )
        for run in runs
    ]
