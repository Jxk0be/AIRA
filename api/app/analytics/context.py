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
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.canonical.models import Capabilities


@dataclass(frozen=True, slots=True)
class SourceRef:
    """One register a shop runs, by the slug on its rows and the shop's name for it.

    Carries its *own* capabilities, not the shop's. The union on the context is
    what decides whether a metric can be attempted at all; this is what decides
    which half of the shop it actually covers — and the difference between those
    two is where a consolidated figure quietly goes wrong.
    """

    source: str
    display_name: str | None = None
    capabilities: Capabilities = field(default_factory=Capabilities)

    @property
    def label(self) -> str:
        return self.display_name or self.source

    def has(self, capability: str) -> bool:
        return bool(getattr(self.capabilities, capability))


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
    "has_vendors": (
        "{shop}'s system doesn't record who they buy from or on what terms, so "
        "there is nothing to group a purchase order by. Adding suppliers and "
        "lead times in the app turns this on."
    ),
    "has_payments": (
        "{shop}'s system doesn't report payments separately from sales, so "
        "there is no way to split takings into card and cash."
    ),
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
    # Every register this shop runs, in the order a screen should list them.
    # Carried here because almost every caller that wants a consolidated number
    # also wants to label its parts, and a second query per request to answer
    # "which tills does this shop have" would be silly.
    #
    # Still platform-blind: these are the slugs and names the sync engine wrote
    # onto `integrations`, not anything imported from `app.connectors`.
    sources: tuple[SourceRef, ...] = ()

    @property
    def has_more_than_one_source(self) -> bool:
        """Whether consolidation is this shop's problem at all.

        The gate a *screen* asks before giving "all your registers" a headline. A
        metric never asks: `source_breakdown` on a one-register shop returns one
        row, which is correct and reconciles.
        """
        return len(self.sources) > 1

    def label_for_source(self, source: str) -> str:
        """What to call one register. Falls back to the slug we stamped."""
        for ref in self.sources:
            if ref.source == source:
                return ref.label
        return source

    def sources_with(self, capability: str) -> tuple[str, ...]:
        """The registers that can answer for this capability, by slug.

        `has()` says whether *anybody* can, which is the right gate for
        attempting a metric. This says who — which is the right scope for
        reconciling one, because comparing a figure that covers one register
        against a figure that covers two is how a packet tells a bookkeeper their
        books do not balance when they do.
        """
        return tuple(ref.source for ref in self.sources if ref.has(capability))

    def covers_every_source(self, capability: str) -> bool:
        """True when every register this shop runs can answer for this."""
        return bool(self.sources) and len(self.sources_with(capability)) == len(self.sources)

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

    The same read also collects the registers themselves, in the order they were
    connected, so that a consolidated number can name its parts without a second
    query.
    """
    query = select(t.Tenant).where(t.Tenant.deleted_at.is_(None))
    if isinstance(tenant, uuid.UUID):
        query = query.where(t.Tenant.id == tenant)
    else:
        query = query.where(t.Tenant.slug == tenant)

    row = (await session.execute(query)).scalar_one_or_none()
    if row is None:
        raise TenantNotFound(f"no tenant {tenant!r}")

    active = (
        await session.execute(
            select(
                t.Integration.source,
                t.Integration.display_name,
                t.Integration.capabilities,
            )
            .where(t.Integration.tenant_id == row.id, t.Integration.is_active.is_(True))
            .order_by(t.Integration.created_at, t.Integration.source)
        )
    ).all()

    declared = [one.capabilities for one in active]
    merged = {
        field: any(bool(one.get(field)) for one in declared) for field in Capabilities.model_fields
    }
    sources = tuple(
        SourceRef(
            source=one.source,
            display_name=one.display_name,
            capabilities=Capabilities(**(one.capabilities or {})),
        )
        for one in active
    )

    return AnalyticsContext(
        tenant_id=row.id,
        slug=row.slug,
        name=row.name,
        timezone=row.timezone,
        currency=row.currency,
        capabilities=Capabilities(**merged),
        settings=dict(row.settings or {}),
        sources=sources,
    )
