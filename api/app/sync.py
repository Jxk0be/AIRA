"""Sync a tenant from its source system.

    python -m app.sync --tenant animanga_knox --mode backfill
    python -m app.sync --tenant animanga_knox --mode incremental
    python -m app.sync --tenant animanga_knox --source registerone
    python -m app.sync --tenant animanga_knox --health

Backfill pulls everything and soft-deletes whatever the source no longer
returns. Incremental pulls only what changed since the last run's watermark.

A tenant can run more than one register, and every active one is synced, in the
order they were connected. Each gets its own run, its own watermark and its own
data-quality report; a failure on one does not roll back another. `--source`
narrows to one.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.canonical.enums import SyncMode
from app.connectors import registry
from app.connectors.base import ENTITIES
from app.connectors.data_quality import QualityReport, build_report
from app.connectors.dev_tenants import DEV_TENANTS, ensure_dev_tenant
from app.connectors.secrets import resolve
from app.connectors.sync import SyncEngine, SyncReport
from app.db import dispose_engine, get_sessionmaker
from app.rag.embeddings import EmbeddingError
from app.rag.ingest import IngestReport, ingest_tenant


async def load_tenant(
    session: AsyncSession, slug: str, only: str | None = None
) -> tuple[t.Tenant, list[t.Integration]]:
    """The tenant and every register it runs, oldest connection first.

    Returns a list because a shop can have more than one, and this used to return
    `.first()` — which meant a shop with a till on the floor and a marketplace
    online had one of them synced and the other silently ignored forever.

    `only` narrows to a single source slug, for re-running one register without
    touching the others.
    """
    tenant = (
        await session.execute(select(t.Tenant).where(t.Tenant.slug == slug))
    ).scalar_one_or_none()

    if slug in DEV_TENANTS:
        # A dev shop's spec is the authority, every time, not just on first
        # creation. Otherwise adding a second register to `DEV_TENANTS` does
        # nothing until somebody drops the database — which is exactly the
        # footgun that hid the single-register bugs for so long. Idempotent, and
        # a real tenant's slug is never in here.
        tenant, integrations = await ensure_dev_tenant(session, slug)
    elif tenant is None:
        raise SystemExit(
            f"no tenant {slug!r}, and it is not a dev shop. Known dev shops: "
            f"{', '.join(sorted(DEV_TENANTS))}"
        )
    else:
        integrations = list(
            (
                await session.execute(
                    select(t.Integration)
                    .where(t.Integration.tenant_id == tenant.id, t.Integration.is_active.is_(True))
                    .order_by(t.Integration.created_at, t.Integration.source)
                )
            )
            .scalars()
            .all()
        )
        if not integrations:
            raise SystemExit(f"{slug} has no active integration to sync")

    if only is not None:
        chosen = [one for one in integrations if one.source == only]
        if not chosen:
            known = ", ".join(one.source for one in integrations) or "none"
            raise SystemExit(f"{slug} has no active source {only!r}. It has: {known}")
        return tenant, chosen
    return tenant, integrations


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


def build_adapter(integration: t.Integration) -> object:
    registry.load_builtin_adapters()
    return registry.build(integration.adapter, integration.config, resolve(integration.secret_ref))


@dataclass(frozen=True, slots=True)
class RegisterSync:
    """What happened to one of a shop's registers."""

    integration: t.Integration
    report: SyncReport
    quality: QualityReport | None = None

    @property
    def source(self) -> str:
        return self.integration.source

    @property
    def label(self) -> str:
        return self.integration.display_name or self.integration.source


@dataclass(frozen=True, slots=True)
class TenantSync:
    """What happened to a shop: one entry per register, plus the search index.

    The aggregate properties exist because the callers want a shop-level answer —
    the worker logs one line per job, the screen shows one toast — while the
    per-register detail stays available underneath for the Data screen.
    """

    tenant: t.Tenant
    registers: list[RegisterSync] = field(default_factory=list)
    indexed: IngestReport | None = None

    @property
    def ok(self) -> bool:
        return all(one.report.ok for one in self.registers)

    def total(self, field_name: str) -> int:
        return sum(one.report.total(field_name) for one in self.registers)

    @property
    def customers_merged(self) -> int:
        return sum(one.report.customers_merged for one in self.registers)

    @property
    def duration_ms(self) -> int:
        return sum(one.report.duration_ms for one in self.registers)

    @property
    def errors(self) -> list[str]:
        """Every failure, named by the register it happened on.

        A shop with two tills needs to know *which* one stopped answering; a bare
        "orders: connection refused" is not actionable when there are two.
        """
        out: list[str] = []
        for one in self.registers:
            out.extend(
                f"{one.source}/{error['entity']}: {error['error']}" for error in one.report.errors
            )
        return out


async def sync_tenant(
    session: AsyncSession,
    slug: str,
    mode: SyncMode,
    *,
    entities: list[str] | None = None,
    only: str | None = None,
    embed: bool = True,
    on_register: Callable[[RegisterSync], None] | None = None,
) -> TenantSync:
    """Sync every register a shop runs, then re-index once.

    The only place this loop is written. It used to be written three times — in
    this CLI, in the Data screen's background task and in the worker's sync job —
    and all three stopped at the first integration they found, so a shop with a
    till on the floor and a marketplace online had one of them synced and the
    other silently ignored. Three copies of a loop is how that happens; one copy
    is the fix.

    Each register is committed as it finishes. A failure on the second does not
    roll back the first: those rows are synced and correct, and a shop's other
    numbers should not go dark because one spreadsheet moved. `TenantSync.ok` is
    false if any register failed, so a caller that needs all-or-nothing still has
    the answer.

    `on_register` is called as each one lands, so a half-minute backfill can
    print as it goes instead of going quiet and then printing everything.
    """
    tenant, integrations = await load_tenant(session, slug, only)

    registers: list[RegisterSync] = []
    for integration in integrations:
        adapter = build_adapter(integration)
        try:
            engine = SyncEngine(session, tenant, integration, adapter)  # type: ignore[arg-type]
            report = await engine.run(mode, entities)
        finally:
            # We built it, so we close it.
            await adapter.aclose()  # type: ignore[attr-defined]

        quality = await build_report(session, tenant, integration)
        await session.commit()

        landed = RegisterSync(integration=integration, report=report, quality=quality)
        registers.append(landed)
        if on_register is not None:
            on_register(landed)

    result = TenantSync(tenant=tenant, registers=registers)

    # Once for the shop, not once per register: the index covers the catalog and
    # the uploaded documents, neither of which knows what a source is.
    if result.ok and embed:
        indexed = await reindex(session, tenant)
        await session.commit()
        result = TenantSync(tenant=tenant, registers=registers, indexed=indexed)

    return result


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
    parser.add_argument(
        "--source",
        help="only this source slug, for re-running one register without the others",
    )
    parser.add_argument("--health", action="store_true", help="check every connection and stop")
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
            if args.health:
                _, integrations = await load_tenant(session, args.tenant, args.source)
                # Every register, and the worst answer wins: a shop whose
                # marketplace export has gone missing is not healthy just because
                # the till still answers.
                healthy = True
                for integration in integrations:
                    adapter = build_adapter(integration)
                    try:
                        status = await adapter.healthcheck()  # type: ignore[attr-defined]
                    finally:
                        await adapter.aclose()  # type: ignore[attr-defined]
                    mark = "ok" if status.ok else "FAILED"
                    print(
                        f"  {integration.source} ({integration.adapter}): {mark} — {status.detail}"
                    )
                    healthy = healthy and status.ok
                return 0 if healthy else 1

            result = await sync_tenant(
                session,
                args.tenant,
                SyncMode(args.mode),
                entities=args.entities.split(",") if args.entities else None,
                only=args.source,
                embed=not args.no_embed,
                # Printed as each register lands rather than all at the end: a
                # backfill of two registers takes a while and silence looks broken.
                on_register=lambda landed: print_report(landed.report, landed.quality),
            )

            if result.indexed is not None:
                print("\n  Retrieval index")
                for line in result.indexed.lines():
                    print(f"    {line}")
            return 0 if result.ok else 1
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
