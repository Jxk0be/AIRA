"""What the worker actually does, one function per job.

Each one takes a session and a shop and returns whatever it wants remembered in
`job_runs.detail`. Each one is safe to run twice: the claim in `runner` stops a
second run for the same scheduled instant, and inside these bodies the
detectors deduplicate, the packets upsert and the outcome measurements check
before they write.

This module knows about `app.connectors`, because syncing is one of the jobs
and the worker sits above the adapter layer the way the sync CLI does. Nothing
in the AI layer imports it (CLAUDE.md rule 1).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext
from app.canonical import tables as t
from app.canonical.enums import SyncMode
from app.deadstock import measure as measure_rescues
from app.digest import DigestSkipped
from app.digest import send as send_digest
from app.insights import expire_stale, run_detectors
from app.jobs.runner import JobSkipped
from app.monthend import email_packet
from app.monthend import generate as generate_packet
from app.notify import Notifier
from app.reorder import measure as measure_reorders

log = logging.getLogger(__name__)


async def sync(session: AsyncSession, ctx: AnalyticsContext) -> dict[str, Any]:
    """Pull whatever changed since last time, then re-index for search."""
    from app.connectors.sync import SyncEngine
    from app.sync import build_adapter, load_tenant, reindex

    tenant, integration = await load_tenant(session, ctx.slug)
    adapter = build_adapter(integration)
    try:
        engine = SyncEngine(session, tenant, integration, adapter)  # type: ignore[arg-type]
        report = await engine.run(SyncMode.INCREMENTAL)
    finally:
        await adapter.aclose()  # type: ignore[attr-defined]

    indexed = None
    if report.ok:
        indexed = await reindex(session, tenant)
    await session.commit()

    if not report.ok:
        raise RuntimeError(
            "; ".join(f"{error['entity']}: {error['error']}" for error in report.errors)
        )

    return {
        "fetched": report.total("fetched"),
        "upserted": report.total("upserted"),
        "customers_merged": report.customers_merged,
        "chunks_embedded": getattr(indexed, "embedded", 0) if indexed else 0,
        "duration_ms": report.duration_ms,
    }


async def detectors(
    session: AsyncSession, ctx: AnalyticsContext, *, as_of: date | None = None
) -> dict[str, Any]:
    """Run every detector this shop can support, then retire stale findings."""
    expired = await expire_stale(session, ctx)
    await session.commit()

    runs = await run_detectors(session, ctx, as_of)
    created = sum(run.created for run in runs)
    failures = [run for run in runs if run.error]

    detail: dict[str, Any] = {
        "created": created,
        "updated": sum(run.updated for run in runs),
        "expired": expired,
        "detectors": {
            run.kind: (
                run.error
                if run.error
                else run.skipped_reason
                if run.skipped_reason
                else f"{run.created} new, {run.updated} updated"
            )
            for run in runs
        },
    }
    if failures and len(failures) == len(runs):
        raise RuntimeError("; ".join(f"{run.kind}: {run.error}" for run in failures))
    return detail


async def digest(
    session: AsyncSession,
    ctx: AnalyticsContext,
    *,
    as_of: date | None = None,
    notifier: Notifier | None = None,
) -> dict[str, Any]:
    """Monday's email, unless the data is too old to build one from."""
    try:
        results = await send_digest(session, ctx, as_of=as_of, notifier=notifier)
    except DigestSkipped as skipped:
        raise JobSkipped(skipped.reason) from skipped

    if not results:
        raise JobSkipped("nobody at this shop has asked for the digest")
    return {
        "sent": sum(1 for result in results if result.sent),
        "suppressed": sum(1 for result in results if not result.sent),
        "recipients": len(results),
    }


async def outcomes(
    session: AsyncSession, ctx: AnalyticsContext, *, as_of: date | None = None
) -> dict[str, Any]:
    """Measure what the shop did about last month's advice."""
    reorders = await measure_reorders(session, ctx, as_of=as_of)
    rescues = await measure_rescues(session, ctx, as_of=as_of)
    return {
        "purchase_orders_measured": len(reorders),
        "rescues_measured": len(rescues),
        "attributed_revenue": str(
            sum((order.attributed_revenue for order in reorders), start=_zero())
        ),
        "cash_recovered": str(sum((r.cash_recovered for r in rescues), start=_zero())),
    }


def _zero() -> Any:
    from decimal import Decimal

    return Decimal("0")


async def month_end(
    session: AsyncSession,
    ctx: AnalyticsContext,
    *,
    as_of: date | None = None,
    notifier: Notifier | None = None,
) -> dict[str, Any]:
    """Build last month's packet and tell the owner it is ready."""
    packet = await generate_packet(session, ctx, as_of=as_of)
    bookkeeper = ctx.setting("bookkeeper_email", None)
    sent = await email_packet(
        session,
        ctx,
        packet,
        bookkeeper=str(bookkeeper) if bookkeeper else None,
        notifier=notifier,
    )
    failures = [note for note in packet.notes if "gap of" in note]
    return {
        "packet_id": str(packet.id),
        "period": packet.label,
        "emails_sent": sent,
        "reconciliation_gaps": len(failures),
    }


async def active_tenants(session: AsyncSession) -> list[str]:
    """Every shop the worker should be doing rounds for."""
    from sqlalchemy import select

    rows = (
        (
            await session.execute(
                select(t.Tenant.slug).where(t.Tenant.deleted_at.is_(None)).order_by(t.Tenant.slug)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)
