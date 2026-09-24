"""A month's packet, stored as its figures rather than as a file.

The PDF and the workbook are regenerated from this row on download. Keeping
the numbers instead of the bytes means a packet stays reproducible, a template
fix improves every past month at once, and a year of packets costs kilobytes
rather than megabytes.

The figures themselves are frozen at generation. A packet is a statement about
a month that has closed, and a late refund arriving in November must not
silently rewrite October's.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.schema import TenantMixin, pk


class MonthEndPacket(TenantMixin, Base):
    __tablename__ = "month_end_packets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "period_start", name="uq_month_end_packets_period"),
        Index("ix_month_end_packets_tenant_period", "tenant_id", "period_start"),
    )

    id: Mapped[uuid.UUID] = pk()
    period_start: Mapped[date] = mapped_column(nullable=False)
    period_end: Mapped[date] = mapped_column(nullable=False)
    generated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    # Everything the documents are built from, as plain JSON.
    figures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # The caveats, in the words the packet prints them in.
    notes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    # Where it was emailed, if it was. Blank means download-only.
    emailed_to: Mapped[str | None] = mapped_column(String(320))
    emailed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
