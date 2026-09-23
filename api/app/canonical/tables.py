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
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
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
)
from app.db import Base

# Money is Decimal dollars. Four decimal places because unit prices on
# fractional-quantity items genuinely need them; totals still reconcile
# to the cent.
MONEY = Numeric(14, 4)
QUANTITY = Numeric(14, 4)
EMBEDDING_DIM = 1024


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _enum(python_enum: type, name: str) -> Enum:
    """VARCHAR + CHECK rather than a native PG enum.

    Adding a value later is then a constraint change rather than a type
    migration. Two settings matter and neither is the default:

    * `values_callable` stores the enum's *value* (`"in_store"`), not its member
      name (`"IN_STORE"`). The canonical model documents the lowercase form, and
      every hand-written query and every eval compares against it.
    * `create_constraint` actually writes the CHECK. Without it the column is a
      bare VARCHAR and an unknown channel would simply be stored.
    """
    return Enum(
        python_enum,
        native_enum=False,
        create_constraint=True,
        length=32,
        name=name,
        validate_strings=True,
        values_callable=lambda enum: [member.value for member in enum],
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TenantMixin(TimestampMixin):
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )


class SourcedMixin(TenantMixin):
    """Shop data that came from a customer system."""

    source: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # When the source last changed this row. The incremental watermark is the
    # highest value seen, not our own clock, so a fixture dated into next week
    # still syncs correctly.
    source_updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


def _sourced_args(table: str, *extra: Any) -> tuple[Any, ...]:
    return (
        UniqueConstraint("tenant_id", "source", "external_id", name=f"uq_{table}_source_external"),
        Index(f"ix_{table}_tenant", "tenant_id"),
        *extra,
    )


# --------------------------------------------------------------------------
# Tenancy and integrations
# --------------------------------------------------------------------------


class Tenant(TimestampMixin, Base):
    """One shop. Everything else hangs off this."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = _pk()
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

    id: Mapped[uuid.UUID] = _pk()
    adapter: Mapped[str] = mapped_column(String(64), nullable=False)
    # `source` is the value stamped onto every row this integration syncs.
    source: Mapped[str] = mapped_column(String(64), nullable=False)
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
    __table_args__ = _sourced_args("locations")

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    address: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Category(SourcedMixin, Base):
    __tablename__ = "categories"
    __table_args__ = _sourced_args("categories")

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL")
    )


class Product(SourcedMixin, Base):
    __tablename__ = "products"
    __table_args__ = _sourced_args(
        "products",
        Index("ix_products_tenant_category", "tenant_id", "category_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
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
    __table_args__ = _sourced_args(
        "variants",
        Index("ix_variants_tenant_product", "tenant_id", "product_id"),
        Index("ix_variants_tenant_sku", "tenant_id", "sku"),
    )

    id: Mapped[uuid.UUID] = _pk()
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


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------


class InventoryLevel(SourcedMixin, Base):
    """Stock on hand right now, per variant per location."""

    __tablename__ = "inventory_levels"
    __table_args__ = _sourced_args(
        "inventory_levels",
        UniqueConstraint(
            "tenant_id", "variant_id", "location_id", name="uq_inventory_levels_variant_location"
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
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
    __table_args__ = _sourced_args(
        "inventory_movements",
        Index("ix_inventory_movements_tenant_occurred", "tenant_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    variant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("variants.id", ondelete="CASCADE"), nullable=False
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("locations.id", ondelete="SET NULL")
    )
    kind: Mapped[MovementKind] = mapped_column(_enum(MovementKind, "movement_kind"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))


# --------------------------------------------------------------------------
# Sales
# --------------------------------------------------------------------------


class Customer(SourcedMixin, Base):
    __tablename__ = "customers"
    __table_args__ = _sourced_args(
        "customers",
        Index("ix_customers_tenant_email", "tenant_id", "email_normalized"),
        Index("ix_customers_tenant_phone", "tenant_id", "phone_normalized"),
    )

    id: Mapped[uuid.UUID] = _pk()
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
    __table_args__ = _sourced_args(
        "orders",
        Index("ix_orders_tenant_placed", "tenant_id", "placed_at"),
        Index("ix_orders_tenant_customer", "tenant_id", "customer_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("locations.id", ondelete="SET NULL")
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    status: Mapped[OrderStatus] = mapped_column(_enum(OrderStatus, "order_status"), nullable=False)
    channel: Mapped[Channel] = mapped_column(_enum(Channel, "channel"), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    tip_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    placed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class OrderLine(SourcedMixin, Base):
    __tablename__ = "order_lines"
    __table_args__ = _sourced_args(
        "order_lines",
        Index("ix_order_lines_tenant_order", "tenant_id", "order_id"),
        Index("ix_order_lines_tenant_variant", "tenant_id", "variant_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
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
    __table_args__ = _sourced_args(
        "refunds",
        Index("ix_refunds_tenant_occurred", "tenant_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class RefundLine(SourcedMixin, Base):
    """Line-level refund detail, when the source provides it."""

    __tablename__ = "refund_lines"
    __table_args__ = _sourced_args(
        "refund_lines",
        Index("ix_refund_lines_tenant_refund", "tenant_id", "refund_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    refund_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("refunds.id", ondelete="CASCADE"), nullable=False
    )
    order_line_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("order_lines.id", ondelete="SET NULL")
    )
    quantity: Mapped[Decimal | None] = mapped_column(QUANTITY)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)


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

    id: Mapped[uuid.UUID] = _pk()
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

    id: Mapped[uuid.UUID] = _pk()
    integration_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False
    )
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    cursor: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class SyncRun(TenantMixin, Base):
    __tablename__ = "sync_runs"
    __table_args__ = (Index("ix_sync_runs_tenant_started", "tenant_id", "started_at"),)

    id: Mapped[uuid.UUID] = _pk()
    integration_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[SyncMode] = mapped_column(_enum(SyncMode, "sync_mode"), nullable=False)
    status: Mapped[SyncStatus] = mapped_column(_enum(SyncStatus, "sync_status"), nullable=False)
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

    id: Mapped[uuid.UUID] = _pk()
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

    id: Mapped[uuid.UUID] = _pk()
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

    id: Mapped[uuid.UUID] = _pk()
    source: Mapped[ChunkSource] = mapped_column(_enum(ChunkSource, "chunk_source"), nullable=False)
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

    id: Mapped[uuid.UUID] = _pk()
    title: Mapped[str | None] = mapped_column(String(512))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class Message(TenantMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)

    id: Mapped[uuid.UUID] = _pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(_enum(MessageRole, "message_role"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_calls: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    charts: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)


class SavedChart(TenantMixin, Base):
    """A chart the owner pinned to their dashboard."""

    __tablename__ = "saved_charts"
    __table_args__ = (
        Index("ix_saved_charts_tenant_position", "tenant_id", "position"),
        CheckConstraint("position >= 0", name="position_non_negative"),
    )

    id: Mapped[uuid.UUID] = _pk()
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
