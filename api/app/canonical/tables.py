"""The canonical schema: the only tables the AI layer is allowed to read.

Shape rules (see CLAUDE.md):

* Shop data carries `tenant_id`, `source` and `external_id`, unique together,
  so a re-sync updates rows instead of duplicating them.
* Soft deletes only (`deleted_at`): a product deleted in the POS today still has
  to resolve for an order line from last March.
* Money is Decimal dollars, quantities are Decimal, timestamps are tz-aware UTC.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.canonical.enums import (
    Channel,
    ChunkSource,
    MessageRole,
    MovementKind,
    OrderStatus,
    SyncMode,
    SyncStatus,
    Tender,
)
from app.db import Base
from app.schema import (
    MONEY,
    QUANTITY,
    SourcedMixin,
    TenantMixin,
    TimestampMixin,
    enum_column,
    pk,
    sourced_args,
)

# Money, quantities and the tenant/sourced mixins live in `app.schema` now that
# the feature tables built on top of this contract share them.
EMBEDDING_DIM = 1024


# --------------------------------------------------------------------------
# Tenancy and integrations
# --------------------------------------------------------------------------


class Tenant(TimestampMixin, Base):
    """One shop. Everything else hangs off this."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = pk()
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # IANA name, e.g. America/New_York. Every date bucket in analytics is cut in
    # this timezone, not UTC, or "yesterday's sales" is wrong by a few hours.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class Integration(TenantMixin, Base):
    """A tenant's connection to one source system."""

    __tablename__ = "integrations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source", name="uq_integrations_tenant_source"),
        Index("ix_integrations_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = pk()
    adapter: Mapped[str] = mapped_column(String(64), nullable=False)
    # `source` is the value stamped onto every row this integration syncs.
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    # What to call this register on a screen: "Square (front counter)", "Etsy",
    # "Market booth". Defaulted from the adapter's own `display_name` when the
    # integration is created, and meant to be editable by the shop afterwards —
    # a shop with two Square accounts needs to tell them apart in their own
    # words.
    #
    # It lives here, on a canonical table, for a specific reason: a consolidated
    # P&L has to label its rows, and `app.analytics` is not allowed to ask
    # `app.connectors` what a platform is called (CLAUDE.md rule 1). The
    # platform-aware layer writes the name down once; the platform-blind layer
    # reads a string.
    display_name: Mapped[str | None] = mapped_column(String(120))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Credentials live in a secret store; we only keep the pointer.
    secret_ref: Mapped[str | None] = mapped_column(String(255))
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------


class Location(SourcedMixin, Base):
    __tablename__ = "locations"
    __table_args__ = sourced_args("locations")

    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    address: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Category(SourcedMixin, Base):
    __tablename__ = "categories"
    __table_args__ = sourced_args("categories")

    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL")
    )


class Product(SourcedMixin, Base):
    __tablename__ = "products"
    __table_args__ = sourced_args(
        "products",
        Index("ix_products_tenant_category", "tenant_id", "category_id"),
    )

    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL")
    )
    product_type: Mapped[str | None] = mapped_column(String(128))
    vendor_name: Mapped[str | None] = mapped_column(String(255))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)


class Variant(SourcedMixin, Base):
    """The sellable unit. Single-variant products get exactly one row here."""

    __tablename__ = "variants"
    __table_args__ = sourced_args(
        "variants",
        Index("ix_variants_tenant_product", "tenant_id", "product_id"),
        Index("ix_variants_tenant_sku", "tenant_id", "sku"),
    )

    id: Mapped[uuid.UUID] = pk()
    product_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str | None] = mapped_column(String(255))
    sku: Mapped[str | None] = mapped_column(String(128))
    barcode: Mapped[str | None] = mapped_column(String(128))
    price: Mapped[Decimal | None] = mapped_column(MONEY)
    # Null cost is normal, not an error. Margin metrics report their coverage.
    cost: Mapped[Decimal | None] = mapped_column(MONEY)
    tracks_inventory: Mapped[bool] = mapped_column(nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)


class Vendor(SourcedMixin, Base):
    """Who the shop buys from.

    Sourced like everything else, because most POS systems hold a supplier
    list. A vendor the owner types in themselves is stamped with the
    `manual` source, so the two never collide on a re-sync and a backfill's
    sweep cannot soft-delete something we were told by hand.
    """

    __tablename__ = "vendors"
    __table_args__ = sourced_args("vendors")

    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(64))
    account_number: Mapped[str | None] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(Text)


class VariantVendor(SourcedMixin, Base):
    """What it costs to buy one variant from one vendor, and how.

    Reordering is arithmetic on these four numbers, and a shop that has never
    filled them in still gets suggestions — just with a caveat instead of a
    dollar figure. `lead_time_days` is the one that changes the answer most,
    which is why it is editable in the UI whatever the source said.
    """

    __tablename__ = "variant_vendors"
    __table_args__ = sourced_args(
        "variant_vendors",
        UniqueConstraint(
            "tenant_id", "variant_id", "vendor_id", name="uq_variant_vendors_variant_vendor"
        ),
        CheckConstraint("pack_size > 0", name="pack_size_positive"),
        CheckConstraint("min_order_qty >= 0", name="min_order_qty_non_negative"),
        CheckConstraint("lead_time_days >= 0", name="lead_time_non_negative"),
    )

    id: Mapped[uuid.UUID] = pk()
    variant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("variants.id", ondelete="CASCADE"), nullable=False
    )
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False
    )
    unit_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    # Cases of 6, packs of 12. Suggestions round up to this, because ordering
    # seven of something that ships in sixes is not an order anyone can place.
    pack_size: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False, default=Decimal("1"))
    min_order_qty: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False, default=Decimal("0"))
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    # The vendor a reorder defaults to when a variant can be bought from more
    # than one. Exactly one per variant should carry it; the UI enforces that.
    is_primary: Mapped[bool] = mapped_column(nullable=False, default=True)


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------


class InventoryLevel(SourcedMixin, Base):
    """Stock on hand right now, per variant per location."""

    __tablename__ = "inventory_levels"
    __table_args__ = sourced_args(
        "inventory_levels",
        UniqueConstraint(
            "tenant_id", "variant_id", "location_id", name="uq_inventory_levels_variant_location"
        ),
    )

    id: Mapped[uuid.UUID] = pk()
    variant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("variants.id", ondelete="CASCADE"), nullable=False
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("locations.id", ondelete="CASCADE"), nullable=False
    )
    on_hand: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    as_of: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class InventoryMovement(SourcedMixin, Base):
    """Optional history. Only sources with `has_inventory_history` fill this."""

    __tablename__ = "inventory_movements"
    __table_args__ = sourced_args(
        "inventory_movements",
        Index("ix_inventory_movements_tenant_occurred", "tenant_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = pk()
    variant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("variants.id", ondelete="CASCADE"), nullable=False
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("locations.id", ondelete="SET NULL")
    )
    kind: Mapped[MovementKind] = mapped_column(
        enum_column(MovementKind, "movement_kind"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))


# --------------------------------------------------------------------------
# Sales
# --------------------------------------------------------------------------


class Customer(SourcedMixin, Base):
    __tablename__ = "customers"
    __table_args__ = sourced_args(
        "customers",
        Index("ix_customers_tenant_email", "tenant_id", "email_normalized"),
        Index("ix_customers_tenant_phone", "tenant_id", "phone_normalized"),
    )

    id: Mapped[uuid.UUID] = pk()
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(64))
    # Written by the adapter layer's dedupe step: lowercased email, digits-only
    # phone. Matching happens on these, never on the raw values.
    email_normalized: Mapped[str | None] = mapped_column(String(320))
    phone_normalized: Mapped[str | None] = mapped_column(String(64))
    # Duplicates are merged by pointing at the survivor, never by deleting:
    # old orders must keep resolving to the row the POS actually referenced.
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    source_created_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class Order(SourcedMixin, Base):
    __tablename__ = "orders"
    __table_args__ = sourced_args(
        "orders",
        Index("ix_orders_tenant_placed", "tenant_id", "placed_at"),
        Index("ix_orders_tenant_customer", "tenant_id", "customer_id"),
    )

    id: Mapped[uuid.UUID] = pk()
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("locations.id", ondelete="SET NULL")
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    status: Mapped[OrderStatus] = mapped_column(
        enum_column(OrderStatus, "order_status"), nullable=False
    )
    channel: Mapped[Channel] = mapped_column(enum_column(Channel, "channel"), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    tip_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    placed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class OrderLine(SourcedMixin, Base):
    __tablename__ = "order_lines"
    __table_args__ = sourced_args(
        "order_lines",
        Index("ix_order_lines_tenant_order", "tenant_id", "order_id"),
        Index("ix_order_lines_tenant_variant", "tenant_id", "variant_id"),
    )

    id: Mapped[uuid.UUID] = pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    # Null for custom-amount lines rung up at the register.
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("variants.id", ondelete="SET NULL")
    )
    # What the receipt said at the time. Renames and deletions upstream must not
    # rewrite history.
    name_snapshot: Mapped[str] = mapped_column(String(512), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    discount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    # Cost at the moment of sale. Null means no cost was known then; margin
    # metrics count that line as uncovered rather than guessing today's cost.
    unit_cost_snapshot: Mapped[Decimal | None] = mapped_column(MONEY)
    note: Mapped[str | None] = mapped_column(Text)


class Refund(SourcedMixin, Base):
    __tablename__ = "refunds"
    __table_args__ = sourced_args(
        "refunds",
        Index("ix_refunds_tenant_occurred", "tenant_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class RefundLine(SourcedMixin, Base):
    """Line-level refund detail, when the source provides it."""

    __tablename__ = "refund_lines"
    __table_args__ = sourced_args(
        "refund_lines",
        Index("ix_refund_lines_tenant_refund", "tenant_id", "refund_id"),
    )

    id: Mapped[uuid.UUID] = pk()
    refund_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("refunds.id", ondelete="CASCADE"), nullable=False
    )
    order_line_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("order_lines.id", ondelete="SET NULL")
    )
    quantity: Mapped[Decimal | None] = mapped_column(QUANTITY)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)


class Payment(SourcedMixin, Base):
    """How an order was actually paid for.

    Kept apart from the order because one sale can be split across a card and
    a twenty-dollar note, and because a month-end packet has to report takings
    by tender. The amount here **excludes** the tip, matching how every POS
    we have met reports it; adding the tip twice is the easiest way to
    overstate a month.
    """

    __tablename__ = "payments"
    __table_args__ = sourced_args(
        "payments",
        Index("ix_payments_tenant_occurred", "tenant_id", "occurred_at"),
        Index("ix_payments_tenant_order", "tenant_id", "order_id"),
    )

    id: Mapped[uuid.UUID] = pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tip: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    tender: Mapped[Tender] = mapped_column(enum_column(Tender, "tender"), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


# --------------------------------------------------------------------------
# What the owner tells us themselves
# --------------------------------------------------------------------------


class StaffHours(TenantMixin, Base):
    """When someone is on the floor, and how many of them.

    Typed in by the owner rather than synced: no POS we have met knows the
    rota. Without it the staffing heatmap is still useful; with it we can say
    "Tuesday 11-1 has two people and 1.2 orders an hour".
    """

    __tablename__ = "staff_hours"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "location_id", "weekday", "start_time", name="uq_staff_hours_slot"
        ),
        Index("ix_staff_hours_tenant", "tenant_id"),
        CheckConstraint("weekday between 0 and 6", name="weekday_in_week"),
        CheckConstraint("staff_count > 0", name="staff_count_positive"),
        CheckConstraint("end_time > start_time", name="shift_ends_after_it_starts"),
    )

    id: Mapped[uuid.UUID] = pk()
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("locations.id", ondelete="CASCADE")
    )
    # 0 = Monday, matching Python's `date.weekday()` and the heatmap's columns.
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # Shop-local wall-clock times. A shift is a slot in the week, not an
    # instant, so there is no timezone on them beyond the tenant's own.
    start_time: Mapped[Any] = mapped_column(Time, nullable=False)
    end_time: Mapped[Any] = mapped_column(Time, nullable=False)
    staff_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    note: Mapped[str | None] = mapped_column(String(255))


# --------------------------------------------------------------------------
# Sync plumbing
# --------------------------------------------------------------------------


class RawRecord(TenantMixin, Base):
    """Landing table: exactly what the source sent, before any mapping.

    Two jobs. Re-run a mapping without re-fetching (and without hammering a
    rate-limited API), and settle "where did this number come from?" against the
    payload the POS actually returned.
    """

    __tablename__ = "raw_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source", "entity", "external_id", name="uq_raw_records_identity"
        ),
        Index("ix_raw_records_tenant_entity", "tenant_id", "entity"),
    )

    id: Mapped[uuid.UUID] = pk()
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class SyncState(TenantMixin, Base):
    """Per-entity cursor, so incremental sync knows where it left off."""

    __tablename__ = "sync_state"
    __table_args__ = (
        UniqueConstraint("tenant_id", "integration_id", "entity", name="uq_sync_state_identity"),
    )

    id: Mapped[uuid.UUID] = pk()
    integration_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False
    )
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    cursor: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class SyncRun(TenantMixin, Base):
    __tablename__ = "sync_runs"
    __table_args__ = (Index("ix_sync_runs_tenant_started", "tenant_id", "started_at"),)

    id: Mapped[uuid.UUID] = pk()
    integration_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[SyncMode] = mapped_column(enum_column(SyncMode, "sync_mode"), nullable=False)
    status: Mapped[SyncStatus] = mapped_column(
        enum_column(SyncStatus, "sync_status"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    errors: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)


class DataQualityReport(TenantMixin, Base):
    """What we had to work with.

    The dashboard shows this to the shop owner in plain language; analytics
    reads it for the caveats it attaches to numbers.
    """

    __tablename__ = "data_quality_reports"
    __table_args__ = (
        Index("ix_data_quality_reports_tenant_generated", "tenant_id", "generated_at"),
    )

    id: Mapped[uuid.UUID] = pk()
    sync_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sync_runs.id", ondelete="SET NULL")
    )
    generated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    findings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------


class Document(TenantMixin, Base):
    """Uploaded prose: policies, FAQs, event schedules."""

    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_tenant", "tenant_id"),)

    id: Mapped[uuid.UUID] = pk()
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    doc_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    uploaded_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class Chunk(TenantMixin, Base):
    """One embedded passage.

    `chunk_index` is part of the identity because a document splits into many
    passages; a product chunk is always index 0.
    """

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source", "source_id", "chunk_index", name="uq_chunks_identity"
        ),
        Index("ix_chunks_tenant_source", "tenant_id", "source", "source_id"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_fts", "fts", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = pk()
    source: Mapped[ChunkSource] = mapped_column(
        enum_column(ChunkSource, "chunk_source"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # sha256 of content + embedding_model: lets ingest skip unchanged chunks so
    # a no-op re-ingest costs zero embedding calls.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    # Vectors from different models are not comparable. Every search filters on
    # this column as well as on tenant_id.
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    fts: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )


# --------------------------------------------------------------------------
# Conversations
# --------------------------------------------------------------------------


class Conversation(TenantMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_tenant_updated", "tenant_id", "updated_at"),)

    id: Mapped[uuid.UUID] = pk()
    title: Mapped[str | None] = mapped_column(String(512))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class Message(TenantMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)

    id: Mapped[uuid.UUID] = pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(
        enum_column(MessageRole, "message_role"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_calls: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    charts: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    # Buttons the answer offered, as `app.agent.actions.ActionSpec` dumps them.
    # Stored rather than recomputed: they are part of what was said, and an
    # offer that vanishes when the conversation is reopened is worse than none.
    actions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)


class SavedChart(TenantMixin, Base):
    """A chart the owner pinned to their dashboard."""

    __tablename__ = "saved_charts"
    __table_args__ = (
        Index("ix_saved_charts_tenant_position", "tenant_id", "position"),
        CheckConstraint("position >= 0", name="position_non_negative"),
    )

    id: Mapped[uuid.UUID] = pk()
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class AgentRun(TenantMixin, Base):
    """One question, and everything it cost to answer.

    Written for every run, successful or not. Two audiences again: us, for "why
    did that take nine seconds" and "what does a question cost"; and the shop,
    because a per-question cost is what makes a per-shop monthly price
    defensible rather than a guess.
    """

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_tenant_started", "tenant_id", "started_at"),
        Index("ix_agent_runs_conversation", "conversation_id"),
    )

    id: Mapped[uuid.UUID] = pk()
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    # One entry per call: name, arguments, duration, and the error if it failed.
    # Arguments are kept because a wrong answer is usually a wrong argument.
    tool_calls: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Cached tokens are billed differently, so they are counted separately
    # rather than folded into the input total.
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # "ok" or "error". A refusal or a failed tool is still a run that happened.
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
