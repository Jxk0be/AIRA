"""HTTP for "is the worker doing its rounds?".

Read by the Data & sync screen, which is the one place in the app that is
allowed to be about the plumbing. A shop owner should be able to see that the
digest went out on Monday and that last night's outcome measurement failed,
without anybody opening a log.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.http import ShopDep
from app.jobs import ROUNDS, recent_runs
from app.jobs.runner import last_successful
from app.jobs.schedule import Schedule

router = APIRouter(tags=["jobs"])


class RunOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    job: str
    status: str
    due_at: datetime
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    detail: dict[str, Any]
    error: str | None


class ScheduleOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: str
    # Plain English, because this is shown to a shop owner and "0 7 * * 1"
    # is not.
    when: str
    last_due: datetime
    last_succeeded_at: datetime | None
    overdue: bool


class JobsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant: str
    timezone: str
    schedules: list[ScheduleOut]
    runs: list[RunOut]


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def describe(plan: Schedule) -> str:
    if plan.kind == "hourly":
        return f"every hour, at {plan.minute:02d} past"
    if plan.kind == "daily":
        return f"every day at {plan.hour:02d}:{plan.minute:02d}"
    if plan.kind == "weekly":
        return f"{WEEKDAYS[plan.weekday]}s at {plan.hour:02d}:{plan.minute:02d}"
    return f"the {_ordinal(plan.day)} of each month at {plan.hour:02d}:{plan.minute:02d}"


def _ordinal(day: int) -> str:
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


@router.get("/tenants/{tenant}/jobs", response_model=JobsOut)
async def jobs(shop: ShopDep, limit: int = 20) -> JobsOut:
    now = datetime.now(tz=UTC)
    schedules: list[ScheduleOut] = []
    for name, plan, _ in ROUNDS:
        due = plan.last_due(shop.ctx.tz, now)
        succeeded = await last_successful(shop.session, shop.ctx.tenant_id, name)
        schedules.append(
            ScheduleOut(
                job=name,
                when=f"{describe(plan)}, {shop.ctx.timezone.replace('_', ' ')}",
                last_due=due,
                last_succeeded_at=succeeded,
                overdue=succeeded is None or succeeded < due,
            )
        )

    runs = await recent_runs(shop.session, shop.ctx.tenant_id, limit=limit)
    return JobsOut(
        tenant=shop.slug,
        timezone=shop.ctx.timezone,
        schedules=schedules,
        runs=[
            RunOut(
                id=run.id,
                job=run.job,
                status=str(run.status),
                due_at=run.due_at,
                started_at=run.started_at,
                finished_at=run.finished_at,
                duration_ms=run.duration_ms,
                detail=dict(run.detail or {}),
                error=run.error,
            )
            for run in runs
        ],
    )
