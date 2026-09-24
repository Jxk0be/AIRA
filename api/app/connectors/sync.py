"""The sync engine: adapter output in, canonical rows out.

The shape of a run:

1. Open a `sync_runs` row.
2. For each entity in dependency order, pull from the adapter. Every record's
   untouched payload lands in `raw_records` *first*, then it is mapped and
   upserted on `(tenant_id, source, external_id)`.
3. On a backfill, anything the source no longer returns is soft-deleted.
4. Merge duplicate customers, write a data quality report, close the run.

Two design choices worth knowing:

* **The incremental watermark is the source's own clock**, not ours: the
  highest `source_updated_at` seen during a run. A fixture dated into next week,
  or a POS whose clock runs slow, both still sync correctly.
* **Upserts are idempotent by construction.** Running the same backfill twice
  produces the same rows with the same ids, which is what the conformance suite
  checks and what makes re-running a mapping safe.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import Table, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.canonical.enums import SyncMode, SyncStatus
from app.canonical.models import (
    CanonicalCategory,
    CanonicalCustomer,
    CanonicalInventoryLevel,
    CanonicalInventoryMovement,
    CanonicalLocation,
    CanonicalOrder,
    CanonicalPayment,
    CanonicalProduct,
    CanonicalRefund,
    CanonicalVariant,
    CanonicalVariantVendor,
    CanonicalVendor,
)
from app.connectors.base import ENTITIES, Fetched, SourceAdapter
from app.connectors.data_quality import build_report
from app.connectors.dedupe import merge_duplicate_customers
from app.db import table_of

log = logging.getLogger(__name__)


CHUNK_SIZE = 500
# Above this many external ids, the "delete what the source no longer returns"
# sweep is skipped rather than shipping a gigantic array to Postgres. Recorded
# in the run's notes so it is never a silent omission.
MAX_SWEEP_IDS = 100_000


@dataclass
class EntityCounts:
    fetched: int = 0
    upserted: int = 0
    children: int = 0
    soft_deleted: int = 0
    watermark: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "fetched": self.fetched,
            "upserted": self.upserted,
            "children": self.children,
            "soft_deleted": self.soft_deleted,
            "watermark": self.watermark.isoformat() if self.watermark else None,
        }


@dataclass
class SyncReport:
    run_id: uuid.UUID
    tenant_slug: str
    mode: SyncMode
    status: SyncStatus
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    counts: dict[str, EntityCounts] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    customers_merged: int = 0
    quality_report_id: uuid.UUID | None = None

    @property
    def ok(self) -> bool:
        return self.status is SyncStatus.SUCCEEDED

    def total(self, key: str) -> int:
        return sum(getattr(c, key) for c in self.counts.values())


class Refs:
    """external_id -> our uuid, per entity, for one tenant and source."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID, source: str) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._source = source
        self._maps: dict[str, dict[str, uuid.UUID]] = {}

    async def load(self, name: str, table: Table) -> dict[str, uuid.UUID]:
        rows = await self._session.execute(
            select(table.c.external_id, table.c.id).where(
                table.c.tenant_id == self._tenant_id, table.c.source == self._source
            )
        )
        self._maps[name] = {external_id: row_id for external_id, row_id in rows}
        return self._maps[name]

    def of(self, name: str, external_id: str | None) -> uuid.UUID | None:
        if external_id is None:
            return None
        return self._maps.get(name, {}).get(external_id)

    def require(self, name: str, external_id: str) -> uuid.UUID:
        found = self.of(name, external_id)
        if found is None:
            raise KeyError(f"{name} {external_id!r} was referenced before it was synced")
        return found

    def known(self, name: str) -> set[str]:
        return set(self._maps.get(name, {}))


class SyncEngine:
    def __init__(
        self,
        session: AsyncSession,
        tenant: t.Tenant,
        integration: t.Integration,
        adapter: SourceAdapter,
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.integration = integration
        self.adapter = adapter
        self.source = integration.source
        # Read once, as plain values. A failed entity rolls the session back,
        # which expires every loaded ORM attribute; touching `tenant.id` after
        # that triggers a lazy reload from inside async code that has no
        # greenlet to do it on, and the *next* entity dies with an error that
        # names neither the rollback nor the entity that actually failed.
        self.tenant_id = tenant.id
        self.tenant_slug = tenant.slug
        self.integration_id = integration.id
        self.refs = Refs(session, self.tenant_id, integration.source)

    # -- public ------------------------------------------------------------

    async def run(
        self, mode: SyncMode = SyncMode.BACKFILL, entities: Sequence[str] | None = None
    ) -> SyncReport:
        wanted = tuple(entities) if entities else ENTITIES
        unknown = set(wanted) - set(ENTITIES)
        if unknown:
            raise ValueError(f"unknown entities: {sorted(unknown)}")

        started = datetime.now(tz=UTC)
        await self._refresh_capabilities()
        run_id = await self._open_run(mode, started)
        report = SyncReport(
            run_id=run_id,
            tenant_slug=self.tenant_slug,
            mode=mode,
            status=SyncStatus.SUCCEEDED,
            started_at=started,
            finished_at=started,
            duration_ms=0,
        )

        try:
            for entity in wanted:
                try:
                    report.counts[entity] = await self._sync_entity(entity, mode, report)
                except Exception as exc:  # one bad entity must not lose the rest
                    log.exception("sync of %s failed", entity)
                    await self.session.rollback()
                    report.status = SyncStatus.FAILED
                    report.errors.append(
                        {"entity": entity, "error": f"{type(exc).__name__}: {exc}"}
                    )

            if "customers" in wanted and not report.errors:
                report.customers_merged = await merge_duplicate_customers(
                    self.session, self.tenant_id, self.source
                )
                await self.session.commit()

            quality = await build_report(
                self.session, self.tenant, self.integration, sync_run_id=run_id
            )
            report.quality_report_id = quality.id
            await self.session.commit()
        finally:
            # The adapter is not ours to close: whoever built it owns its
            # connection, and an engine that closed it could not be run twice.
            report.finished_at = datetime.now(tz=UTC)
            report.duration_ms = int(
                (report.finished_at - report.started_at).total_seconds() * 1000
            )
            await self._close_run(report)

        return report

    # -- the per-entity loop ------------------------------------------------

    async def _sync_entity(self, entity: str, mode: SyncMode, report: SyncReport) -> EntityCounts:
        counts = EntityCounts()
        await self._load_refs_for(entity)

        since = None
        resume_cursor = None
        if mode is SyncMode.INCREMENTAL:
            state = await self._state(entity)
            since = state["last_synced_at"]
        else:
            state = await self._state(entity)
            resume_cursor = state["cursor"]

        seen: list[str] = []
        buffer: list[Fetched[Any]] = []
        last_cursor: str | None = None

        iterator = self.adapter.iterator_for(entity)
        async for fetched in iterator(since=since, resume_cursor=resume_cursor):
            counts.fetched += 1
            buffer.append(fetched)
            last_cursor = fetched.cursor or last_cursor
            stamped = getattr(fetched.record, "source_updated_at", None)
            if stamped and (counts.watermark is None or stamped > counts.watermark):
                counts.watermark = stamped

            if len(buffer) >= CHUNK_SIZE:
                await self._flush(entity, buffer, counts, seen)
                await self._remember_cursor(entity, last_cursor)
                await self.session.commit()
                buffer.clear()

        if buffer:
            await self._flush(entity, buffer, counts, seen)
            buffer.clear()

        if entity == "categories":
            await self._link_category_parents()

        if mode is SyncMode.BACKFILL:
            counts.soft_deleted = await self._sweep(entity, seen, report)

        await self._save_state(entity, counts.watermark or datetime.now(tz=UTC))
        await self.session.commit()
        return counts

    async def _flush(
        self, entity: str, buffer: list[Fetched[Any]], counts: EntityCounts, seen: list[str]
    ) -> None:
        await self._land_raw(entity, buffer)
        handler = getattr(self, f"_upsert_{entity}")
        upserted, children = await handler(buffer, seen)
        counts.upserted += upserted
        counts.children += children

    # -- raw landing --------------------------------------------------------

    async def _land_raw(self, entity: str, buffer: list[Fetched[Any]]) -> None:
        """Untouched payloads, before any mapping touches them."""
        now = datetime.now(tz=UTC)
        rows = [
            {
                "id": uuid.uuid4(),
                "tenant_id": self.tenant_id,
                "source": self.source,
                "entity": entity,
                "external_id": self._external_id(f.record),
                "payload": f.raw,
                "fetched_at": now,
            }
            for f in buffer
        ]
        statement = insert(table_of(t.RawRecord)).values(rows)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["tenant_id", "source", "entity", "external_id"],
                set_={
                    "payload": statement.excluded.payload,
                    "fetched_at": statement.excluded.fetched_at,
                    "updated_at": now,
                },
            )
        )

    # -- upserts ------------------------------------------------------------

    async def _upsert(
        self, table: Table, rows: list[dict[str, Any]], *, conflict: Sequence[str] | None = None
    ) -> int:
        if not rows:
            return 0
        keys = conflict or ("tenant_id", "source", "external_id")

        # Postgres refuses an ON CONFLICT DO UPDATE that would touch the same
        # row twice in one statement, so collapse duplicates here. A source
        # with real ids never produces them; a spreadsheet with no ids at all
        # produces them the moment two rows describe the same thing. Last one
        # wins, and the count is logged rather than swallowed.
        seen: dict[tuple[Any, ...], dict[str, Any]] = {}
        for row in rows:
            seen[tuple(row[key] for key in keys)] = row
        if len(seen) != len(rows):
            log.warning(
                "%s: %s rows collapsed onto %s distinct keys in one batch",
                table.name,
                len(rows),
                len(seen),
            )
        rows = list(seen.values())

        now = datetime.now(tz=UTC)
        statement = insert(table).values(rows)
        updatable = {
            column: getattr(statement.excluded, column)
            for column in rows[0]
            if column not in {"id", "created_at", *keys}
        }
        updatable["updated_at"] = now
        await self.session.execute(
            statement.on_conflict_do_update(index_elements=list(keys), set_=updatable)
        )
        return len(rows)

    def _base(self, record: Any) -> dict[str, Any]:
        return {
            "id": uuid.uuid4(),
            "tenant_id": self.tenant_id,
            "source": self.source,
            "external_id": self._external_id(record),
            "source_updated_at": getattr(record, "source_updated_at", None),
            "deleted_at": getattr(record, "deleted_at", None),
        }

    @staticmethod
    def _external_id(record: Any) -> str:
        external_id = getattr(record, "external_id", None)
        if not external_id:
            raise ValueError(f"{type(record).__name__} arrived without an external_id")
        return str(external_id)

    async def _upsert_locations(
        self, buffer: list[Fetched[CanonicalLocation]], seen: list[str]
    ) -> tuple[int, int]:
        rows = []
        for f in buffer:
            r = f.record
            seen.append(r.external_id)
            rows.append(
                self._base(r)
                | {
                    "name": r.name,
                    "timezone": r.timezone,
                    "is_active": r.is_active,
                    "address": r.address,
                }
            )
        return await self._upsert(table_of(t.Location), rows), 0

    async def _upsert_categories(
        self, buffer: list[Fetched[CanonicalCategory]], seen: list[str]
    ) -> tuple[int, int]:
        rows = []
        for f in buffer:
            r = f.record
            seen.append(r.external_id)
            # parent_id is resolved in a second pass: a child can arrive before
            # its parent, and often does.
            self._pending_parents[r.external_id] = r.parent_external_id
            rows.append(self._base(r) | {"name": r.name})
        return await self._upsert(table_of(t.Category), rows), 0

    _pending_parents: dict[str, str | None]

    async def _link_category_parents(self) -> None:
        pending = {k: v for k, v in getattr(self, "_pending_parents", {}).items() if v}
        if not pending:
            return
        await self.refs.load("categories", table_of(t.Category))
        table = table_of(t.Category)
        for child_external, parent_external in pending.items():
            child = self.refs.of("categories", child_external)
            parent = self.refs.of("categories", parent_external)
            if child and parent:
                await self.session.execute(
                    update(table).where(table.c.id == child).values(parent_id=parent)
                )

    async def _upsert_products(
        self, buffer: list[Fetched[CanonicalProduct]], seen: list[str]
    ) -> tuple[int, int]:
        rows = []
        for f in buffer:
            r = f.record
            seen.append(r.external_id)
            rows.append(
                self._base(r)
                | {
                    "name": r.name,
                    "description": r.description,
                    "category_id": self.refs.of("categories", r.category_external_id),
                    "product_type": r.product_type,
                    "vendor_name": r.vendor_name,
                    "attributes": r.attributes,
                    "is_active": r.is_active,
                }
            )
        return await self._upsert(table_of(t.Product), rows), 0

    async def _upsert_variants(
        self, buffer: list[Fetched[CanonicalVariant]], seen: list[str]
    ) -> tuple[int, int]:
        rows = []
        orphans = 0
        for f in buffer:
            r = f.record
            product_id = self.refs.of("products", r.product_external_id)
            if product_id is None:
                # A variant whose product we have never seen is not something to
                # invent a product for; it is a gap in the source pull.
                orphans += 1
                continue
            seen.append(r.external_id)
            rows.append(
                self._base(r)
                | {
                    "product_id": product_id,
                    "name": r.name,
                    "sku": r.sku,
                    "barcode": r.barcode,
                    "price": r.price,
                    "cost": r.cost,
                    "tracks_inventory": r.tracks_inventory,
                    "is_active": r.is_active,
                }
            )
        if orphans:
            log.warning("%s variants skipped: their product was not in this pull", orphans)
        return await self._upsert(table_of(t.Variant), rows), 0

    async def _upsert_vendors(
        self, buffer: list[Fetched[CanonicalVendor]], seen: list[str]
    ) -> tuple[int, int]:
        rows = []
        for f in buffer:
            r = f.record
            seen.append(r.external_id)
            rows.append(
                self._base(r)
                | {
                    "name": r.name,
                    "email": r.email,
                    "phone": r.phone,
                    "account_number": r.account_number,
                    "notes": r.notes,
                }
            )
        return await self._upsert(table_of(t.Vendor), rows), 0

    async def _upsert_variant_vendors(
        self, buffer: list[Fetched[CanonicalVariantVendor]], seen: list[str]
    ) -> tuple[int, int]:
        """Buying terms, only where both ends of the pairing exist.

        A terms row whose variant or vendor was not in this pull is dropped
        rather than half-written: a purchase order priced against a vendor we
        cannot name is worse than no suggestion.
        """
        rows = []
        dangling = 0
        for f in buffer:
            r = f.record
            variant_id = self.refs.of("variants", r.variant_external_id)
            vendor_id = self.refs.of("vendors", r.vendor_external_id)
            if variant_id is None or vendor_id is None:
                dangling += 1
                continue
            external_id = r.external_id or f"{r.variant_external_id}:{r.vendor_external_id}"
            seen.append(external_id)
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "tenant_id": self.tenant_id,
                    "source": self.source,
                    "external_id": external_id,
                    "source_updated_at": r.source_updated_at,
                    "deleted_at": r.deleted_at,
                    "variant_id": variant_id,
                    "vendor_id": vendor_id,
                    "unit_cost": r.unit_cost,
                    "pack_size": r.pack_size,
                    "min_order_qty": r.min_order_qty,
                    "lead_time_days": r.lead_time_days,
                    "is_primary": r.is_primary,
                }
            )
        if dangling:
            log.warning("%s vendor terms skipped: variant or vendor missing", dangling)
        return await self._upsert(table_of(t.VariantVendor), rows), 0

    async def _upsert_payments(
        self, buffer: list[Fetched[CanonicalPayment]], seen: list[str]
    ) -> tuple[int, int]:
        rows = []
        for f in buffer:
            r = f.record
            order_id = self.refs.of("orders", r.order_external_id)
            if order_id is None:
                log.warning(
                    "payment %s refers to order %s, which we do not have",
                    r.external_id,
                    r.order_external_id,
                )
                continue
            seen.append(r.external_id)
            rows.append(
                self._base(r)
                | {
                    "order_id": order_id,
                    "amount": r.amount,
                    "tip": r.tip,
                    "tender": r.tender,
                    "occurred_at": r.occurred_at,
                }
            )
        return await self._upsert(table_of(t.Payment), rows), 0

    async def _upsert_customers(
        self, buffer: list[Fetched[CanonicalCustomer]], seen: list[str]
    ) -> tuple[int, int]:
        from app.connectors.dedupe import normalise_email, normalise_phone

        rows = []
        for f in buffer:
            r = f.record
            seen.append(r.external_id)
            rows.append(
                self._base(r)
                | {
                    "first_name": r.first_name,
                    "last_name": r.last_name,
                    "email": r.email,
                    "phone": r.phone,
                    "email_normalized": normalise_email(r.email),
                    "phone_normalized": normalise_phone(r.phone),
                    "source_created_at": r.source_created_at,
                }
            )
        return await self._upsert(table_of(t.Customer), rows), 0

    async def _upsert_orders(
        self, buffer: list[Fetched[CanonicalOrder]], seen: list[str]
    ) -> tuple[int, int]:
        rows = []
        for f in buffer:
            r = f.record
            seen.append(r.external_id)
            rows.append(
                self._base(r)
                | {
                    "location_id": self.refs.of("locations", r.location_external_id),
                    "customer_id": self.refs.of("customers", r.customer_external_id),
                    "status": r.status,
                    "channel": r.channel,
                    "subtotal": r.subtotal,
                    "discount_total": r.discount_total,
                    "tax_total": r.tax_total,
                    "tip_total": r.tip_total,
                    "total": r.total,
                    "placed_at": r.placed_at,
                    "closed_at": r.closed_at,
                }
            )
        upserted = await self._upsert(table_of(t.Order), rows)

        order_ids = await self._ids_for(table_of(t.Order), [f.record.external_id for f in buffer])
        line_rows = []
        for f in buffer:
            order_id = order_ids.get(f.record.external_id)
            if order_id is None:  # pragma: no cover — we just wrote it
                continue
            for line in f.record.lines:
                self._seen_order_lines.append(line.external_id)
                line_rows.append(
                    self._base(line)
                    | {
                        "order_id": order_id,
                        "variant_id": self.refs.of("variants", line.variant_external_id),
                        "name_snapshot": line.name_snapshot,
                        "quantity": line.quantity,
                        "unit_price": line.unit_price,
                        "discount": line.discount,
                        "unit_cost_snapshot": line.unit_cost_snapshot,
                        "note": line.note,
                    }
                )
        children = await self._upsert(table_of(t.OrderLine), line_rows)
        return upserted, children

    _seen_order_lines: list[str]
    _seen_refund_lines: list[str]

    async def _upsert_refunds(
        self, buffer: list[Fetched[CanonicalRefund]], seen: list[str]
    ) -> tuple[int, int]:
        rows = []
        kept: list[CanonicalRefund] = []
        for f in buffer:
            r = f.record
            order_id = self.refs.of("orders", r.order_external_id)
            if order_id is None:
                log.warning(
                    "refund %s refers to order %s, which we do not have",
                    r.external_id,
                    r.order_external_id,
                )
                continue
            seen.append(r.external_id)
            kept.append(r)
            rows.append(
                self._base(r)
                | {
                    "order_id": order_id,
                    "amount": r.amount,
                    "reason": r.reason,
                    "occurred_at": r.occurred_at,
                }
            )
        upserted = await self._upsert(table_of(t.Refund), rows)

        if not any(r.lines for r in kept):
            return upserted, 0

        refund_ids = await self._ids_for(table_of(t.Refund), [r.external_id for r in kept])
        await self.refs.load("order_lines", table_of(t.OrderLine))
        line_rows = []
        for r in kept:
            refund_id = refund_ids.get(r.external_id)
            for index, line in enumerate(r.lines, start=1):
                external_id = line.external_id or f"{r.external_id}:{index}"
                self._seen_refund_lines.append(external_id)
                line_rows.append(
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": self.tenant_id,
                        "source": self.source,
                        "external_id": external_id,
                        "source_updated_at": line.source_updated_at,
                        "deleted_at": line.deleted_at,
                        "refund_id": refund_id,
                        "order_line_id": self.refs.of("order_lines", line.order_line_external_id),
                        "quantity": line.quantity,
                        "amount": line.amount,
                    }
                )
        children = await self._upsert(table_of(t.RefundLine), line_rows)
        return upserted, children

    async def _upsert_inventory_levels(
        self, buffer: list[Fetched[CanonicalInventoryLevel]], seen: list[str]
    ) -> tuple[int, int]:
        rows = []
        for f in buffer:
            r = f.record
            variant_id = self.refs.of("variants", r.variant_external_id)
            location_id = self.refs.of("locations", r.location_external_id)
            if variant_id is None or location_id is None:
                continue
            external_id = r.external_id or f"{r.variant_external_id}:{r.location_external_id}"
            seen.append(external_id)
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "tenant_id": self.tenant_id,
                    "source": self.source,
                    "external_id": external_id,
                    "source_updated_at": r.source_updated_at,
                    "deleted_at": r.deleted_at,
                    "variant_id": variant_id,
                    "location_id": location_id,
                    "on_hand": r.on_hand,
                    "as_of": r.as_of,
                }
            )
        return await self._upsert(table_of(t.InventoryLevel), rows), 0

    async def _upsert_inventory_movements(
        self, buffer: list[Fetched[CanonicalInventoryMovement]], seen: list[str]
    ) -> tuple[int, int]:
        rows = []
        for f in buffer:
            r = f.record
            variant_id = self.refs.of("variants", r.variant_external_id)
            if variant_id is None:
                continue
            seen.append(r.external_id)
            rows.append(
                self._base(r)
                | {
                    "variant_id": variant_id,
                    "location_id": self.refs.of("locations", r.location_external_id),
                    "kind": r.kind,
                    "quantity": r.quantity,
                    "occurred_at": r.occurred_at,
                    "reason": r.reason,
                }
            )
        return await self._upsert(table_of(t.InventoryMovement), rows), 0

    # -- helpers ------------------------------------------------------------

    async def _ids_for(self, table: Table, external_ids: list[str]) -> dict[str, uuid.UUID]:
        if not external_ids:
            return {}
        rows = await self.session.execute(
            select(table.c.external_id, table.c.id).where(
                table.c.tenant_id == self.tenant_id,
                table.c.source == self.source,
                table.c.external_id.in_(external_ids),
            )
        )
        return {external_id: row_id for external_id, row_id in rows}

    async def _load_refs_for(self, entity: str) -> None:
        needed = {
            "products": [("categories", table_of(t.Category))],
            "variants": [("products", table_of(t.Product))],
            "variant_vendors": [
                ("variants", table_of(t.Variant)),
                ("vendors", table_of(t.Vendor)),
            ],
            "orders": [
                ("locations", table_of(t.Location)),
                ("customers", table_of(t.Customer)),
                ("variants", table_of(t.Variant)),
            ],
            "payments": [("orders", table_of(t.Order))],
            "refunds": [("orders", table_of(t.Order))],
            "inventory_levels": [
                ("variants", table_of(t.Variant)),
                ("locations", table_of(t.Location)),
            ],
            "inventory_movements": [
                ("variants", table_of(t.Variant)),
                ("locations", table_of(t.Location)),
            ],
        }.get(entity, [])
        for name, table in needed:
            await self.refs.load(name, table)

    SWEEPABLE: ClassVar[dict[str, Any]] = {
        "locations": t.Location,
        "categories": t.Category,
        "products": t.Product,
        "variants": t.Variant,
        "vendors": t.Vendor,
        "variant_vendors": t.VariantVendor,
        "customers": t.Customer,
        "orders": t.Order,
        "payments": t.Payment,
        "refunds": t.Refund,
        "inventory_levels": t.InventoryLevel,
        "inventory_movements": t.InventoryMovement,
    }

    async def _sweep(self, entity: str, seen: list[str], report: SyncReport) -> int:
        """Soft-delete rows the source stopped returning.

        Only meaningful on a backfill, where "not returned" really does mean
        "gone". On an incremental pull it would delete the entire catalog.
        """
        model = self.SWEEPABLE.get(entity)
        if model is None or not seen:
            return 0
        if len(seen) > MAX_SWEEP_IDS:
            report.notes.append(
                f"{entity}: {len(seen):,} ids is above the sweep limit, so rows removed "
                "upstream were not soft-deleted this run"
            )
            return 0

        table = model.__table__
        now = datetime.now(tz=UTC)
        result = await self.session.execute(
            update(table)
            .where(
                table.c.tenant_id == self.tenant_id,
                table.c.source == self.source,
                table.c.deleted_at.is_(None),
                table.c.external_id.notin_(seen),
            )
            .values(deleted_at=now, updated_at=now)
        )
        swept = int(getattr(result, "rowcount", 0) or 0)

        if entity == "orders" and self._seen_order_lines:
            lines = table_of(t.OrderLine)
            if len(self._seen_order_lines) <= MAX_SWEEP_IDS:
                await self.session.execute(
                    update(lines)
                    .where(
                        lines.c.tenant_id == self.tenant_id,
                        lines.c.source == self.source,
                        lines.c.deleted_at.is_(None),
                        lines.c.external_id.notin_(self._seen_order_lines),
                    )
                    .values(deleted_at=now, updated_at=now)
                )
        return swept

    # -- run and state bookkeeping -----------------------------------------

    async def _refresh_capabilities(self) -> None:
        """Take the adapter's word for what it can do, every run.

        `integrations.capabilities` is written when the tenant is set up, and
        then the adapter grows: costs arrive, payments arrive. Nothing
        downstream reads the adapter - the analytics layer reads this row - so
        a row left at its first value is how a shop keeps getting "this system
        has no cost data" about a system that now does.
        """
        declared = self.adapter.describe().capabilities.model_dump()
        table = table_of(t.Integration)
        stored = (
            await self.session.execute(
                select(table.c.capabilities).where(table.c.id == self.integration_id)
            )
        ).scalar_one()
        if stored == declared:
            return

        gained = sorted(k for k, v in declared.items() if v and not (stored or {}).get(k))
        lost = sorted(k for k, v in declared.items() if not v and (stored or {}).get(k))
        log.info(
            "%s: capabilities changed (gained: %s, lost: %s)",
            self.tenant_slug,
            ", ".join(gained) or "none",
            ", ".join(lost) or "none",
        )
        await self.session.execute(
            update(table)
            .where(table.c.id == self.integration_id)
            .values(capabilities=declared, updated_at=datetime.now(tz=UTC))
        )
        await self.session.commit()
        # The in-memory row is what the data quality report reads at the end of
        # this same run.
        self.integration.capabilities = declared

    async def _open_run(self, mode: SyncMode, started: datetime) -> uuid.UUID:
        self._pending_parents = {}
        self._seen_order_lines = []
        self._seen_refund_lines = []
        run_id = uuid.uuid4()
        await self.session.execute(
            insert(table_of(t.SyncRun)).values(
                id=run_id,
                tenant_id=self.tenant_id,
                integration_id=self.integration_id,
                mode=mode,
                status=SyncStatus.RUNNING,
                started_at=started,
                counts={},
                errors=[],
            )
        )
        await self.session.commit()
        return run_id

    async def _close_run(self, report: SyncReport) -> None:
        table = table_of(t.SyncRun)
        await self.session.execute(
            update(table)
            .where(table.c.id == report.run_id)
            .values(
                status=report.status,
                finished_at=report.finished_at,
                duration_ms=report.duration_ms,
                counts={
                    **{entity: c.as_dict() for entity, c in report.counts.items()},
                    "_customers_merged": report.customers_merged,
                    "_notes": report.notes,
                },
                errors=report.errors,
                updated_at=datetime.now(tz=UTC),
            )
        )
        await self.session.commit()

    async def _state(self, entity: str) -> dict[str, Any]:
        table = table_of(t.SyncState)
        row = (
            await self.session.execute(
                select(table.c.cursor, table.c.last_synced_at).where(
                    table.c.tenant_id == self.tenant_id,
                    table.c.integration_id == self.integration_id,
                    table.c.entity == entity,
                )
            )
        ).first()
        if row is None:
            return {"cursor": None, "last_synced_at": None}
        return {"cursor": row[0], "last_synced_at": row[1]}

    async def _save_state(self, entity: str, watermark: datetime) -> None:
        statement = insert(table_of(t.SyncState)).values(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            integration_id=self.integration_id,
            entity=entity,
            cursor=None,  # a finished entity has nothing to resume
            last_synced_at=watermark,
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["tenant_id", "integration_id", "entity"],
                set_={
                    "cursor": None,
                    "last_synced_at": statement.excluded.last_synced_at,
                    "updated_at": datetime.now(tz=UTC),
                },
            )
        )

    async def _remember_cursor(self, entity: str, cursor: str | None) -> None:
        if cursor is None:
            return
        statement = insert(table_of(t.SyncState)).values(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            integration_id=self.integration_id,
            entity=entity,
            cursor=cursor,
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["tenant_id", "integration_id", "entity"],
                set_={"cursor": statement.excluded.cursor, "updated_at": datetime.now(tz=UTC)},
            )
        )
