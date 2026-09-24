"""Who a number is for, and what window it covers.

Every function in this package takes an `AnalyticsContext` and a `DateRange`.
The context carries the tenant id (so nothing has to be passed around loose),
the shop's timezone (so "last December" is cut in shop time, not UTC) and the
capabilities its source system declared (so a tool can refuse honestly instead
of returning a confident zero).

Nothing here imports from `app.connectors`: capabilities reach us through the
`integrations` row the sync engine wrote, which is canonical data like any
other (CLAUDE.md rule 1).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.canonical.models import Capabilities


class AnalyticsError(RuntimeError):
    """Something about this request cannot be answered. Message is owner-ready."""


class TenantNotFound(AnalyticsError):
    pass


class CapabilityUnavailable(AnalyticsError):
    """The shop's system does not hold what this metric needs.

    Raised rather than returned as zero, and deliberately not caught inside this
    package: the agent turns it into a sentence, the dashboard hides the widget.
    A zero here would read as "you made no money", which is a different claim
    from "your POS doesn't record that".
    """

    def __init__(self, capability: str, message: str) -> None:
        super().__init__(message)
        self.capability = capability
        self.message = message


# Why a metric is missing, in words a shop owner would use. `{shop}` is filled
# in with the tenant's name.
CAPABILITY_REASONS: dict[str, str] = {
    "has_costs": (
        "{shop}'s system doesn't record what items cost, so there is nothing to "
        "work margin out from. Adding cost to items in your POS is what turns "
        "this on."
    ),
    "has_customers": (
        "{shop}'s system doesn't attach a customer to sales, so there is no way "
        "to tell a regular from a first-timer."
    ),
    "has_inventory_history": (
        "{shop}'s system only reports stock as it is right now, with no history "
        "of receiving and adjustments behind it."
    ),
    "multi_location": "{shop} has a single location, so there is nothing to split by location.",
    "has_online_channel": "{shop} has no online channel, so every sale is an in-person one.",
    "supports_incremental": "{shop}'s system can only be re-read in full, never in changes only.",
}


@dataclass(frozen=True, slots=True)
class DateRange:
    """A span of shop-local calendar days, both ends inclusive.

    Calendar days rather than instants because that is how the question is
    always asked ("last December", "this week"). Turning that into UTC instants
    is `bounds()`'s job, and it is the only place the conversion happens.
    """

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"date range ends before it starts: {self.start} to {self.end}")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def bounds(self, tz: ZoneInfo) -> tuple[datetime, datetime]:
        """Half-open UTC instants: midnight local on `start`, midnight local on
        the day after `end`.

        Half-open rather than `<=` on the last instant because there is no
        sensible "last instant" of a day, and a sale at 23:59:59.7 has to land
        inside the day it happened on.
        """
        first = datetime.combine(self.start, time.min, tzinfo=tz)
        after = datetime.combine(self.end + timedelta(days=1), time.min, tzinfo=tz)
        return first.astimezone(UTC), after.astimezone(UTC)

    def previous(self) -> DateRange:
        """The same number of days, immediately before this range."""
        length = timedelta(days=self.days)
        return DateRange(self.start - length, self.start - length + timedelta(days=self.days - 1))

    @property
    def label(self) -> str:
        if self.start == self.end:
            return self.start.isoformat()
        return f"{self.start.isoformat()} to {self.end.isoformat()}"


@dataclass(frozen=True, slots=True)
class AnalyticsContext:
    """One shop, plus everything a metric needs to know about it."""

    tenant_id: uuid.UUID
    slug: str
    name: str
    timezone: str
    currency: str
    capabilities: Capabilities
    settings: dict[str, Any]

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def today(self) -> date:
        """Today in shop time. At 01:00 in Knoxville that is still not tomorrow."""
        return datetime.now(tz=UTC).astimezone(self.tz).date()

    def last_days(self, count: int) -> DateRange:
        """The `count` days ending today, inclusive. `last_days(30)` is the
        dashboard's "last 30 days"."""
        end = self.today()
        return DateRange(end - timedelta(days=count - 1), end)

    def month(self, year: int, month: int) -> DateRange:
        start = date(year, month, 1)
        end = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
        return DateRange(start, end)

    def has(self, capability: str) -> bool:
        return bool(getattr(self.capabilities, capability))

    def require(self, capability: str) -> None:
        """Gate a metric on a capability, with a message fit to show the owner."""
        if self.has(capability):
            return
        reason = CAPABILITY_REASONS.get(
            capability, "{shop}'s system does not provide what this needs."
        )
        raise CapabilityUnavailable(capability, reason.format(shop=self.name))

    def setting(self, key: str, default: Any) -> Any:
        value = self.settings.get(key)
        return default if value is None else value

    @property
    def low_stock_threshold(self) -> int:
        return int(self.setting("low_stock_threshold", 3))

    @property
    def dead_stock_days(self) -> int:
        return int(self.setting("dead_stock_days", 90))


async def load_context(session: AsyncSession, tenant: str | uuid.UUID) -> AnalyticsContext:
    """Build the context for a tenant, by slug or by id.

    Capabilities are the union across the tenant's active integrations: a shop
    running a POS plus a spreadsheet of costs can do margin even though only one
    of the two sources knows a cost. A capability nothing claims stays off.
    """
    query = select(t.Tenant).where(t.Tenant.deleted_at.is_(None))
    if isinstance(tenant, uuid.UUID):
        query = query.where(t.Tenant.id == tenant)
    else:
        query = query.where(t.Tenant.slug == tenant)

    row = (await session.execute(query)).scalar_one_or_none()
    if row is None:
        raise TenantNotFound(f"no tenant {tenant!r}")

    declared = (
        (
            await session.execute(
                select(t.Integration.capabilities).where(
                    t.Integration.tenant_id == row.id, t.Integration.is_active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    merged = {
        field: any(bool(one.get(field)) for one in declared) for field in Capabilities.model_fields
    }

    return AnalyticsContext(
        tenant_id=row.id,
        slug=row.slug,
        name=row.name,
        timezone=row.timezone,
        currency=row.currency,
        capabilities=Capabilities(**merged),
        settings=dict(row.settings or {}),
    )
