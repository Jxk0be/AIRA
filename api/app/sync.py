"""Sync a tenant from its source system.

    python -m app.sync --tenant animanga_knox --mode backfill
    python -m app.sync --tenant animanga_knox --mode incremental
    python -m app.sync --tenant animanga_knox --health

Backfill pulls everything and soft-deletes whatever the source no longer
returns. Incremental pulls only what changed since the last run's watermark.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.canonical.enums import SyncMode
from app.connectors import registry
from app.connectors.base import ENTITIES
from app.connectors.data_quality import QualityReport, build_report
from app.connectors.dev_tenants import ensure_dev_tenant
from app.connectors.secrets import resolve
from app.connectors.sync import SyncEngine, SyncReport
from app.db import dispose_engine, get_sessionmaker
from app.rag.embeddings import EmbeddingError
from app.rag.ingest import IngestReport, ingest_tenant


async def load_tenant(session: AsyncSession, slug: str) -> tuple[t.Tenant, t.Integration]:
    tenant = (
        await session.execute(select(t.Tenant).where(t.Tenant.slug == slug))
    ).scalar_one_or_none()
    if tenant is None:
        return await ensure_dev_tenant(session, slug)

    integration = (
        (
            await session.execute(
                select(t.Integration).where(
                    t.Integration.tenant_id == tenant.id, t.Integration.is_active.is_(True)
                )
            )
        )
        .scalars()
        .first()
    )
    if integration is None:
        return await ensure_dev_tenant(session, slug)
    return tenant, integration


def build_adapter(integration: t.Integration) -> object:
    registry.load_builtin_adapters()
    return registry.build(integration.adapter, integration.config, resolve(integration.secret_ref))


async def reindex(session: AsyncSession, tenant: t.Tenant) -> IngestReport | None:
    """Re-embed whatever the sync changed.

    Runs here rather than inside the sync engine: `app.connectors` must not
    import the AI layer, and this CLI is above both. A failure is reported and
    swallowed — a shop whose embedding provider is down has still had its sales
    synced, and every number on the dashboard still works.
    """
    try:
        return await ingest_tenant(session, tenant)
    except EmbeddingError as exc:
        print(f"\n  Retrieval index not updated: {exc}")
        return None


def print_report(report: SyncReport, quality: QualityReport | None) -> None:
    print(f"\n  {report.tenant_slug} — {report.mode.value} sync")
    print(f"  run {report.run_id}   {report.status.value}   {report.duration_ms / 1000:.1f}s\n")
    print(f"    {'entity':22} {'fetched':>9} {'upserted':>9} {'children':>9} {'deleted':>8}")
    for entity in ENTITIES:
        counts = report.counts.get(entity)
        if counts is None:
            continue
        print(
            f"    {entity:22} {counts.fetched:>9,} {counts.upserted:>9,} "
            f"{counts.children:>9,} {counts.soft_deleted:>8,}"
        )
    if report.customers_merged:
        print(f"\n    {report.customers_merged:,} duplicate customers merged")
    for note in report.notes:
        print(f"    note: {note}")
    for error in report.errors:
        print(f"    FAILED {error['entity']}: {error['error']}")

    if quality is not None:
        print("\n  Data quality")
        for finding in quality.findings:
            print(f"    [{finding.severity.value:7}] {finding.message}")
        if not quality.findings:
            print("    nothing to flag")


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True, help="tenant slug, e.g. animanga_knox")
    parser.add_argument("--mode", choices=[m.value for m in SyncMode], default="backfill")
    parser.add_argument(
        "--entities",
        help=f"comma-separated subset of: {', '.join(ENTITIES)}",
    )
    parser.add_argument("--health", action="store_true", help="check the connection and stop")
    parser.add_argument(
        "--no-embed",
        action="store_true",
        help="skip re-indexing for search (no embedding calls)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            tenant, integration = await load_tenant(session, args.tenant)
            adapter = build_adapter(integration)
            try:
                if args.health:
                    status = await adapter.healthcheck()  # type: ignore[attr-defined]
                    mark = "ok" if status.ok else "FAILED"
                    print(f"  {integration.adapter}: {mark} — {status.detail}")
                    return 0 if status.ok else 1

                entities = args.entities.split(",") if args.entities else None
                engine = SyncEngine(session, tenant, integration, adapter)  # type: ignore[arg-type]
                report = await engine.run(SyncMode(args.mode), entities)
            finally:
                # We built it, so we close it.
                await adapter.aclose()  # type: ignore[attr-defined]

            quality = await build_report(session, tenant, integration)
            indexed = None
            if report.ok and not args.no_embed:
                indexed = await reindex(session, tenant)
            await session.commit()

            print_report(report, quality)
            if indexed is not None:
                print("\n  Retrieval index")
                for line in indexed.lines():
                    print(f"    {line}")
            return 0 if report.ok else 1
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
