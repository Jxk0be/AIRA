"""The adapter contract.

Every adapter — a code adapter for a cloud POS, or the config-driven mapping
adapter for spreadsheets — yields these Pydantic models and nothing else. The
sync engine stamps `tenant_id` and `source` on the way in, so adapters only ever
have to speak in their own source's external ids.

Rules that apply to every model here:

* Money is `Decimal` in **dollars**. A source reporting integer cents is the
  adapter's problem, not ours.
* Timestamps are timezone-aware UTC.
* `external_id` is the source's own stable id for the thing. It must be stable
  across syncs — that is what makes re-syncing idempotent.
* References between entities use the *other* entity's `external_id`. The sync
  engine resolves those into our uuid foreign keys.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.canonical.enums import Channel, MovementKind, OrderStatus, Tender


class Capabilities(BaseModel):
    """What a tenant's source system can actually tell us.

    Lives in the canonical layer on purpose: analytics, the agent and the UI all
    read capabilities, and none of them may import from `app.connectors`.
    A capability that is False means tools degrade honestly ("your system
    doesn't track cost, so I can't compute margin") instead of returning zeros.
    """

    model_config = ConfigDict(extra="forbid")

    has_costs: bool = False
    has_customers: bool = False
    has_inventory_history: bool = False
    multi_location: bool = False
    has_online_channel: bool = False
    supports_incremental: bool = False
    # Does the source know who the shop buys from, and on what terms? Without
    # it, reorder suggestions still work but cannot be grouped into a purchase
    # order or priced at cost.
    has_vendors: bool = False
    # Does it report payments separately from orders? Needed to split a month's
    # takings by card and cash, and to reconcile a month-end packet.
    has_payments: bool = False


class CanonicalBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UtcMixin(BaseModel):
    """Rejects naive datetimes at the adapter boundary rather than in analytics."""

    @field_validator("*", mode="after")
    @classmethod
    def _require_tz(cls, value: Any) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware UTC")
        return value


class SourcedRecord(CanonicalBase, UtcMixin):
    """Anything that came from a customer system and can be deleted there.

    `deleted_at` carries the source's own deletion time when it has one. The
    row is never removed: an order line from last March still has to resolve.
    """

    external_id: str
    # When the source last changed this record. The sync engine uses the
    # highest value it sees as the incremental watermark, so a source that
    # leaves this empty can only ever be fully re-pulled.
    source_updated_at: datetime | None = None
    deleted_at: datetime | None = None


class CanonicalLocation(SourcedRecord):
    name: str
    timezone: str | None = None
    is_active: bool = True
    address: dict[str, Any] | None = None


class CanonicalCategory(SourcedRecord):
    name: str
    parent_external_id: str | None = None


class CanonicalProduct(SourcedRecord):
    name: str
    description: str | None = None
    category_external_id: str | None = None
    product_type: str | None = None
    vendor_name: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class CanonicalVariant(SourcedRecord):
    """The thing that actually has a price, a barcode and a stock level.

    Single-variant products still get exactly one variant, so every downstream
    query has one shape to deal with.
    """

    product_external_id: str
    name: str | None = None
    sku: str | None = None
    barcode: str | None = None
    price: Decimal | None = None  # None for variable-priced items
    cost: Decimal | None = None  # None when the source has no cost for it
    tracks_inventory: bool = True
    is_active: bool = True


class CanonicalVendor(SourcedRecord):
    """A supplier the shop buys from."""

    name: str
    email: str | None = None
    phone: str | None = None
    account_number: str | None = None
    notes: str | None = None


class CanonicalVariantVendor(SourcedRecord):
    """Buying terms for one variant from one vendor.

    `external_id` is synthesised from the variant and vendor when the source
    has no id of its own for the pairing, which is usual.
    """

    external_id: str = ""
    variant_external_id: str
    vendor_external_id: str
    unit_cost: Decimal | None = None
    pack_size: Decimal = Decimal("1")
    min_order_qty: Decimal = Decimal("0")
    lead_time_days: int = 14
    is_primary: bool = True


class CanonicalInventoryLevel(SourcedRecord):
    """Stock on hand right now, per variant per location.

    `external_id` is synthesised from variant + location when the source has no
    id of its own for a stock level, which is usual.
    """

    external_id: str = ""
    variant_external_id: str
    location_external_id: str
    on_hand: Decimal
    as_of: datetime


class CanonicalInventoryMovement(SourcedRecord):
    """Optional: only sources with `has_inventory_history` emit these."""

    variant_external_id: str
    location_external_id: str | None = None
    kind: MovementKind
    quantity: Decimal
    occurred_at: datetime
    reason: str | None = None


class CanonicalCustomer(SourcedRecord):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    source_created_at: datetime | None = None


class CanonicalOrderLine(SourcedRecord):
    variant_external_id: str | None = None  # None for custom-amount lines
    name_snapshot: str
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal = Decimal("0")
    unit_cost_snapshot: Decimal | None = None
    note: str | None = None


class CanonicalOrder(SourcedRecord):
    location_external_id: str | None = None
    customer_external_id: str | None = None
    status: OrderStatus
    channel: Channel
    # Gross merchandise value: the sum of the lines *before* discounts. The
    # arithmetic every adapter has to satisfy is
    #     total = subtotal - discount_total + tax_total + tip_total
    # which is what the conformance suite checks, to the cent.
    subtotal: Decimal
    discount_total: Decimal = Decimal("0")
    tax_total: Decimal = Decimal("0")
    tip_total: Decimal = Decimal("0")
    total: Decimal
    placed_at: datetime
    closed_at: datetime | None = None
    lines: list[CanonicalOrderLine] = Field(default_factory=list)


class CanonicalRefundLine(SourcedRecord):
    external_id: str = ""
    order_line_external_id: str
    quantity: Decimal | None = None
    amount: Decimal


class CanonicalRefund(SourcedRecord):
    order_external_id: str
    amount: Decimal
    reason: str | None = None
    occurred_at: datetime
    lines: list[CanonicalRefundLine] = Field(default_factory=list)


class CanonicalPayment(SourcedRecord):
    """One tender against one order.

    `amount` is what was taken **without** the tip, because that is what every
    source we have met reports and what reconciles against net sales plus tax.
    """

    order_external_id: str
    amount: Decimal
    tip: Decimal = Decimal("0")
    tender: Tender
    occurred_at: datetime


__all__ = [
    "CanonicalCategory",
    "CanonicalCustomer",
    "CanonicalInventoryLevel",
    "CanonicalInventoryMovement",
    "CanonicalLocation",
    "CanonicalOrder",
    "CanonicalOrderLine",
    "CanonicalPayment",
    "CanonicalProduct",
    "CanonicalRefund",
    "CanonicalRefundLine",
    "CanonicalVariant",
    "CanonicalVariantVendor",
    "CanonicalVendor",
    "Capabilities",
]
