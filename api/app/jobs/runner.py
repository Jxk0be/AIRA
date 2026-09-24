"""Claiming a scheduled run, doing it, and writing down what happened.

Idempotency is an insert. A run claims `(tenant_id, job, due_at)`; a unique
violation means another worker — or this one before it restarted — already has
it, and the job simply does not happen again. Nothing here holds a lock for
the duration of the work, so a job that dies mid-flight leaves a `running` row
rather than blocking the next tick forever; the stale-run sweep below closes
those out.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext
from app.canonical.enums import JobStatus
from app.db import table_of
from app.jobs.tables import JobRun

log = logging.getLogger(__name__)

# A run still marked `running` after this long is assumed dead: a worker was
# killed, a container was rescheduled. Long enough that a genuinely slow
# backfill is never declared dead while it is still working.
STALE_AFTER = timedelta(hours=6)


@dataclass
class JobOutcome:
    job: str
    tenant: str
    status: JobStatus
    due_at: datetime
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_ms: int = 0
    run_id: uuid.UUID | None = None

    @property
    def ran(self) -> bool:
        return self.status in (JobStatus.SUCCEEDED, JobStatus.FAILED)

    def line(self) -> str:
        bits = f"{self.tenant:16} {self.job:12} {self.status.value:10}"
        if self.status is JobStatus.SKIPPED:
            return f"{bits} {self.detail.get('reason', '')}"
        if self.error:
            return f"{bits} {self.error}"
        counts = " ".join(f"{k}={v}" for k, v in self.detail.items() if isinstance(v, int | str))
        return f"{bits} {self.duration_ms / 1000:6.1f}s  {counts}"


async def _claim(
    session: AsyncSession, tenant_id: uuid.UUID, job: str, due_at: datetime, started: datetime
) -> uuid.UUID | None:
    """Take ownership of one scheduled instant, or return None if taken."""
    run_id = uuid.uuid4()
    statement = (
        insert(table_of(JobRun))
        .values(
            id=run_id,
            tenant_id=tenant_id,
            job=job,
            due_at=due_at,
            status=JobStatus.RUNNING,
            started_at=started,
            detail={},
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "job", "due_at"])
        .returning(table_of(JobRun).c.id)
    )
    try:
        result = await session.execute(statement)
    except IntegrityError:  # pragma: no cover — on_conflict_do_nothing covers it
        await session.rollback()
        return None
    claimed = result.scalar_one_or_none()
    await session.commit()
    return uuid.UUID(str(claimed)) if claimed else None


async def _close(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    status: JobStatus,
    detail: dict[str, Any],
    error: str | None,
    started: datetime,
) -> int:
    finished = datetime.now(tz=UTC)
    duration_ms = int((finished - started).total_seconds() * 1000)
    table = table_of(JobRun)
    await session.execute(
        update(table)
        .where(table.c.id == run_id)
        .values(
            status=status,
            finished_at=finished,
            duration_ms=duration_ms,
            detail=detail,
            error=error,
            updated_at=finished,
        )
    )
    await session.commit()
    return duration_ms


async def run_once(
    session: AsyncSession,
    ctx: AnalyticsContext,
    job: str,
    due_at: datetime,
    work: Callable[[], Awaitable[dict[str, Any]]],
) -> JobOutcome:
    """Run `work` for one scheduled instant, at most once, and log it.

    `work` returns whatever it wants remembered — counts, ids, the detectors it
    ran. A `JobSkipped` raised inside it is a first-class outcome: "the last
    sync is two days old, so no digest went out" is information, not a failure.
    """
    started = datetime.now(tz=UTC)
    run_id = await _claim(session, ctx.tenant_id, job, due_at, started)
    if run_id is None:
        return JobOutcome(
            job=job,
            tenant=ctx.slug,
            status=JobStatus.SKIPPED,
            due_at=due_at,
            detail={"reason": "already run for this scheduled time"},
        )

    try:
        detail = await work()
    except JobSkipped as skip:
        duration_ms = await _close(
            session,
            run_id,
            status=JobStatus.SKIPPED,
            detail={"reason": skip.reason, **skip.detail},
            error=None,
            started=started,
        )
        return JobOutcome(
            job=job,
            tenant=ctx.slug,
            status=JobStatus.SKIPPED,
            due_at=due_at,
            detail={"reason": skip.reason, **skip.detail},
            duration_ms=duration_ms,
            run_id=run_id,
        )
    except Exception as exc:
        await session.rollback()
        log.exception("job %s failed for %s", job, ctx.slug)
        error = f"{type(exc).__name__}: {exc}"
        duration_ms = await _close(
            session, run_id, status=JobStatus.FAILED, detail={}, error=error, started=started
        )
        return JobOutcome(
            job=job,
            tenant=ctx.slug,
            status=JobStatus.FAILED,
            due_at=due_at,
            error=error,
            duration_ms=duration_ms,
            run_id=run_id,
        )

    duration_ms = await _close(
        session, run_id, status=JobStatus.SUCCEEDED, detail=detail, error=None, started=started
    )
    return JobOutcome(
        job=job,
        tenant=ctx.slug,
        status=JobStatus.SUCCEEDED,
        due_at=due_at,
        detail=detail,
        duration_ms=duration_ms,
        run_id=run_id,
    )


class JobSkipped(Exception):
    """This run should not happen, and that is a normal outcome.

    Raised by a job's own body when the preconditions are not met — the sync is
    stale, the shop has no recipients, there is nothing to report. Recorded as
    `skipped` with the reason rather than as a failure, because a red job log
    that is mostly "nothing to do" is a job log nobody reads.
    """

    def __init__(self, reason: str, **detail: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


async def sweep_stale(session: AsyncSession) -> int:
    """Close out runs a dead worker left marked `running`."""
    cutoff = datetime.now(tz=UTC) - STALE_AFTER
    table = table_of(JobRun)
    result = await session.execute(
        update(table)
        .where(table.c.status == JobStatus.RUNNING, table.c.started_at < cutoff)
        .values(
            status=JobStatus.FAILED,
            error="the worker stopped before this run finished",
            finished_at=datetime.now(tz=UTC),
        )
    )
    await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


async def recent_runs(
    session: AsyncSession, tenant_id: uuid.UUID, *, limit: int = 20
) -> list[JobRun]:
    rows = (
        (
            await session.execute(
                select(JobRun)
                .where(JobRun.tenant_id == tenant_id)
                .order_by(JobRun.started_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def last_successful(session: AsyncSession, tenant_id: uuid.UUID, job: str) -> datetime | None:
    return (
        await session.execute(
            select(JobRun.finished_at)
            .where(
                JobRun.tenant_id == tenant_id,
                JobRun.job == job,
                JobRun.status == JobStatus.SUCCEEDED,
            )
            .order_by(JobRun.finished_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
