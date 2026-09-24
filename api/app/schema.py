"""Column shapes every table of ours shares.

Pulled out of `app.canonical.tables` once a second family of tables appeared.
The canonical schema is the adapter contract and deserves its own file; the
feature tables that sit on top of it — insights, purchase orders, series,
job runs — are not part of that contract but do have to agree with it about
what money is, how a tenant is referenced and what an enum column looks like.

One rule holds across all of them and is the reason this file exists at all:
every table that holds shop data carries a non-null `tenant_id` and every
query filters on it (CLAUDE.md rule 3).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Enum, ForeignKey, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

# Money is Decimal dollars. Four decimal places because unit prices on
# fractional-quantity items genuinely need them; totals still reconcile
# to the cent.
MONEY = Numeric(14, 4)
QUANTITY = Numeric(14, 4)


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def enum_column(python_enum: type, name: str) -> Enum:
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


def sourced_args(table: str, *extra: Any) -> tuple[Any, ...]:
    return (
        UniqueConstraint("tenant_id", "source", "external_id", name=f"uq_{table}_source_external"),
        Index(f"ix_{table}_tenant", "tenant_id"),
        *extra,
    )
