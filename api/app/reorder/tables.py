"""Draft purchase orders, and the lines the owner is going to edit.

A draft is ours, not the POS's: customer source systems are read-only (CLAUDE.md
rule 2), so nothing here is ever written back. What we produce is a document the
owner sends themselves, which is also why there is no "send" that talks to a
vendor — `sent` means the owner told us they sent it.

The lines keep the suggestion's reasoning alongside the quantity. An owner who
halves a line six weeks later should still be able to see what we thought at
the time, and the outcome job needs the original number to measure against.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.canonical.enums import PurchaseOrderStatus
from app.db import Base
from app.schema import MONEY, QUANTITY, TenantMixin, enum_column, pk


class PurchaseOrder(TenantMixin, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        Index("ix_purchase_orders_tenant_status", "tenant_id", "status"),
        Index("ix_purchase_orders_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = pk()
    # Null for the "Unassigned" group: variants we want to reorder but cannot
    # attribute to a supplier yet. Shown as its own group so the gap is
    # visible and fixable rather than silently dropped.
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("vendors.id", ondelete="SET NULL")
    )
    vendor_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("locations.id", ondelete="SET NULL")
    )
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        enum_column(PurchaseOrderStatus, "purchase_order_status"),
        nullable=False,
        default=PurchaseOrderStatus.DRAFT,
    )
    # Human-facing, e.g. "PO-2026-09-0003". Unique per tenant by construction.
    reference: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    # The insight this draft came out of, so acting on the PO can close the
    # loop back to the finding that suggested it.
    insight_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("insights.id", ondelete="SET NULL")
    )
    expected_at: Mapped[date | None] = mapped_column()
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class PurchaseOrderLine(TenantMixin, Base):
    __tablename__ = "purchase_order_lines"
    __table_args__ = (
        Index("ix_purchase_order_lines_order", "purchase_order_id"),
        Index("ix_purchase_order_lines_tenant_variant", "tenant_id", "variant_id"),
        CheckConstraint("quantity >= 0", name="quantity_non_negative"),
    )

    id: Mapped[uuid.UUID] = pk()
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("variants.id", ondelete="CASCADE"), nullable=False
    )
    # What the catalogue called it when the draft was made: a PDF sent to a
    # distributor in March must not change when the shop renames the product.
    name_snapshot: Mapped[str] = mapped_column(String(512), nullable=False)
    sku_snapshot: Mapped[str | None] = mapped_column(String(128))
    # What we suggested, before the owner touched it. Kept separate from
    # `quantity` so "they always halve our numbers" is measurable.
    suggested_qty: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    on_hand_at_draft: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False, default=Decimal(0))
    # The sentence, plus the numbers behind it, exactly as the forecast
    # produced them.
    reasoning: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    received_qty: Mapped[Decimal | None] = mapped_column(QUANTITY)
