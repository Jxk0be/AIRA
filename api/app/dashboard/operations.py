"""HTTP for the Data & sync screen.

The one part of the product that is allowed to know a platform exists. It
reports on the connection itself — which system, when it last ran, what it
brought in, what it could not make sense of — so it sits above
`app.connectors` exactly as the sync CLI does. Nothing in `app.agent`,
`app.rag` or `app.analytics` imports this module (CLAUDE.md rule 1).

The data quality report is the screen that earns its keep. "112 items have no
cost, so margin covers 88% of sales" is a sentence an owner can act on in their
own POS, and every fix makes every other screen better.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext, TenantNotFound, load_context
from app.canonical import tables as t
from app.canonical.enums import Severity, SyncMode, SyncStatus
from app.db import get_session, get_sessionmaker
from app.http import MANAGER_ONLY

log = logging.getLogger(__name__)

router = APIRouter(tags=["data"])

# One sync at a time per tenant, in this process. A second "Sync now" while the
# first is still running would fight it for the same cursors.
_running: set[str] = set()

# Strong references to the tasks in flight: asyncio only holds a weak one, and
# a sync that gets garbage-collected halfway through is a bad afternoon.
_background: set[asyncio.Task[None]] = set()


class IntegrationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    adapter: str
    source: str
    is_active: bool
    capabilities: dict[str, Any]
    # The config a shop owner should see: an endpoint or a filename, never a
    # secret. `secret_ref` is a pointer, so its name is safe to show.
    config: dict[str, Any]
    secret_ref: str | None


class SyncRunOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    mode: SyncMode
    status: SyncStatus
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    counts: dict[str, Any]
    errors: list[Any]


class Finding(BaseModel):
    """One thing the last sync noticed about the data it was given."""

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Severity
    message: str
    count: int = 0
    # A 0-1 proportion where the finding is about a share of something.
    share: float | None = None


class QualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    metrics: dict[str, Any]
    findings: list[Finding]


class DocumentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    title: str
    filename: str | None
    characters: int
    chunks: int
    created_at: datetime


class DataScreen(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant: str
    name: str
    timezone: str
    integrations: list[IntegrationOut]
    last_sync: SyncRunOut | None
    history: list[SyncRunOut]
    quality: QualityReport | None
    documents: list[DocumentOut]
    # True while a sync started from this screen is still going.
    syncing: bool


class SyncStarted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant: str
    mode: SyncMode
    started: bool
    detail: str


async def _context(session: AsyncSession, slug: str) -> AnalyticsContext:
    try:
        return await load_context(session, slug)
    except TenantNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _run_out(row: t.SyncRun) -> SyncRunOut:
    return SyncRunOut(
        id=row.id,
        mode=row.mode,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        duration_ms=row.duration_ms,
        counts=row.counts,
        errors=row.errors,
    )


@router.get("/tenants/{slug}/data", response_model=DataScreen)
async def data_screen(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    history: Annotated[int, Query(ge=1, le=50)] = 10,
) -> DataScreen:
    """Where this shop's data comes from, and how well it came through."""
    ctx = await _context(session, slug)

    integrations = [
        IntegrationOut(
            id=row.id,
            adapter=row.adapter,
            source=row.source,
            is_active=row.is_active,
            capabilities=row.capabilities,
            config=row.config,
            secret_ref=row.secret_ref,
        )
        for row in (
            await session.execute(
                select(t.Integration)
                .where(t.Integration.tenant_id == ctx.tenant_id)
                .order_by(t.Integration.created_at)
            )
        )
        .scalars()
        .all()
    ]

    runs = [
        _run_out(row)
        for row in (
            await session.execute(
                select(t.SyncRun)
                .where(t.SyncRun.tenant_id == ctx.tenant_id)
                .order_by(t.SyncRun.started_at.desc())
                .limit(history)
            )
        )
        .scalars()
        .all()
    ]

    report = (
        await session.execute(
            select(t.DataQualityReport)
            .where(t.DataQualityReport.tenant_id == ctx.tenant_id)
            .order_by(t.DataQualityReport.generated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    chunk_counts: dict[uuid.UUID, int] = {
        row.source_id: row.chunks
        for row in (
            await session.execute(
                select(t.Chunk.source_id, func.count().label("chunks"))
                .where(t.Chunk.tenant_id == ctx.tenant_id, t.Chunk.source == "document")
                .group_by(t.Chunk.source_id)
            )
        ).all()
    }
    documents = [
        DocumentOut(
            id=row.id,
            title=row.title,
            filename=row.filename,
            characters=len(row.content),
            chunks=int(chunk_counts.get(row.id, 0)),
            created_at=row.created_at,
        )
        for row in (
            await session.execute(
                select(t.Document)
                .where(t.Document.tenant_id == ctx.tenant_id)
                .order_by(t.Document.created_at.desc())
            )
        )
        .scalars()
        .all()
    ]

    return DataScreen(
        tenant=ctx.slug,
        name=ctx.name,
        timezone=ctx.timezone,
        integrations=integrations,
        last_sync=runs[0] if runs else None,
        history=runs,
        quality=(
            QualityReport(
                generated_at=report.generated_at,
                metrics=report.metrics,
                findings=[Finding(**finding) for finding in report.findings],
            )
            if report is not None
            else None
        ),
        documents=documents,
        syncing=ctx.slug in _running,
    )


async def _sync(slug: str, mode: SyncMode) -> None:
    """Run one sync in the background, on its own session.

    Imported here rather than at module scope so that the rest of this file —
    and the app that mounts it — does not pull the connector layer in just to
    render a page.
    """
    from app.sync import sync_tenant

    try:
        async with get_sessionmaker()() as session:
            # Every register this shop runs, not the first one found. `sync_tenant`
            # owns that loop so this screen and the worker cannot drift apart.
            result = await sync_tenant(session, slug, mode)
            log.info(
                "sync for %s finished: %d register(s), ok=%s%s",
                slug,
                len(result.registers),
                result.ok,
                "" if result.ok else f" — {'; '.join(result.errors)}",
            )
    except Exception:
        log.exception("background sync failed for %s", slug)
    finally:
        _running.discard(slug)


@router.post(
    "/tenants/{slug}/sync", response_model=SyncStarted, status_code=202, dependencies=MANAGER_ONLY
)
async def start_sync(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    mode: Literal["incremental", "backfill"] = "incremental",
) -> SyncStarted:
    """Sync now.

    Returns as soon as the work is handed off, because a backfill takes half a
    minute and a request that waits for it is a request that times out. The
    screen polls `GET /tenants/{slug}/data` to see it land.
    """
    ctx = await _context(session, slug)
    if ctx.slug in _running:
        return SyncStarted(
            tenant=ctx.slug,
            mode=SyncMode(mode),
            started=False,
            detail="A sync is already running for this shop.",
        )

    integration = (
        (
            await session.execute(
                select(t.Integration).where(
                    t.Integration.tenant_id == ctx.tenant_id, t.Integration.is_active.is_(True)
                )
            )
        )
        .scalars()
        .first()
    )
    if integration is None:
        raise HTTPException(status_code=409, detail=f"{ctx.name} has no connected system")

    _running.add(ctx.slug)
    # Fire and forget: the task owns its own session, and the reference is kept
    # only so the loop does not garbage-collect it mid-run.
    task = asyncio.create_task(_sync(ctx.slug, SyncMode(mode)))
    _background.add(task)
    task.add_done_callback(_background.discard)

    return SyncStarted(
        tenant=ctx.slug,
        mode=SyncMode(mode),
        started=True,
        detail=f"Reading {integration.adapter} for changes.",
    )
