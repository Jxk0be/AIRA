"""Every scheduled run, and what it did.

`(tenant_id, job, due_at)` is unique, and that one constraint is what makes the
whole worker idempotent. A job does not run "now", it runs *for* a scheduled
instant; claiming that instant is an insert, and an insert that conflicts means
somebody already did this. Restart the worker mid-morning and Monday's digest
does not go out twice.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.canonical.enums import JobStatus
from app.db import Base
from app.schema import TenantMixin, enum_column, pk


class JobRun(TenantMixin, Base):
    __tablename__ = "job_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "job", "due_at", name="uq_job_runs_tenant_job_due"),
        Index("ix_job_runs_tenant_started", "tenant_id", "started_at"),
        Index("ix_job_runs_job_status", "job", "status"),
    )

    id: Mapped[uuid.UUID] = pk()
    # "sync", "detectors", "digest", "outcomes", "month_end".
    job: Mapped[str] = mapped_column(String(64), nullable=False)
    # The scheduled instant this run is *for*, not when it happened. A digest
    # that fires late is still Monday's digest.
    due_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        enum_column(JobStatus, "job_status"), nullable=False, default=JobStatus.RUNNING
    )
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    # Whatever the job wants remembered: counts, ids, the detectors it ran.
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
