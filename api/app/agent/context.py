"""What the assistant is told about the shop it is working for.

Built per request, never hardcoded and never cached across tenants. Everything
in here comes out of the canonical tables: the shop's name and timezone, the
locations and channels it actually sells through, what its source system can
provide, how far its data goes back, and the caveats the last sync recorded.

That last part matters more than it looks. An assistant that does not know
twelve percent of items have no cost will quote a margin as if it were the whole
picture, and be wrong in the most convincing way possible.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext, DateRange, load_context
from app.canonical import tables as t
from app.canonical.enums import Channel

# Findings worth putting in front of the model. Info-level ones are true but not
# load-bearing, and every line in a system prompt costs tokens on every turn.
LOUD_ENOUGH = ("error", "warning")
MAX_CAVEATS = 4


@dataclass(frozen=True, slots=True)
class LocationInfo:
    id: uuid.UUID
    name: str


@dataclass(frozen=True, slots=True)
class ShopContext:
    """One shop, as the assistant sees it."""

    analytics: AnalyticsContext
    locations: tuple[LocationInfo, ...]
    channels: tuple[Channel, ...]
    categories: tuple[str, ...]
    first_sale: date | None
    last_sale: date | None
    # Plain-language warnings from the most recent data quality report.
    caveats: tuple[str, ...]

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.analytics.tenant_id

    @property
    def slug(self) -> str:
        return self.analytics.slug

    @property
    def name(self) -> str:
        return self.analytics.name

    @property
    def today(self) -> date:
        return self.analytics.today()

    def default_period(self, days: int = 30) -> DateRange:
        return self.analytics.last_days(days)

    def location_named(self, name: str) -> LocationInfo | None:
        wanted = name.strip().lower()
        for location in self.locations:
            if location.name.lower() == wanted:
                return location
        # A shop with one location called "Animanga Knox - Market Square" gets
        # asked about "Market Square", and refusing that would be pedantry.
        matches = [loc for loc in self.locations if wanted in loc.name.lower()]
        return matches[0] if len(matches) == 1 else None


async def build_context(session: AsyncSession, tenant: str | uuid.UUID) -> ShopContext:
    """Assemble the briefing for one shop."""
    analytics = await load_context(session, tenant)

    locations = tuple(
        LocationInfo(id=row.id, name=row.name)
        for row in (
            await session.execute(
                select(t.Location.id, t.Location.name)
                .where(t.Location.tenant_id == analytics.tenant_id, t.Location.deleted_at.is_(None))
                .order_by(t.Location.name)
            )
        ).all()
    )

    channels = tuple(
        Channel(value)
        for value in (
            await session.execute(
                select(t.Order.channel)
                .where(t.Order.tenant_id == analytics.tenant_id, t.Order.deleted_at.is_(None))
                .distinct()
                .order_by(t.Order.channel)
            )
        )
        .scalars()
        .all()
    )

    categories = tuple(
        (
            await session.execute(
                select(t.Category.name)
                .where(t.Category.tenant_id == analytics.tenant_id, t.Category.deleted_at.is_(None))
                .order_by(t.Category.name)
            )
        )
        .scalars()
        .all()
    )

    window = (
        await session.execute(
            text(
                """
                select min(placed_at) as first_at, max(placed_at) as last_at
                from orders
                where tenant_id = :tenant and deleted_at is null and status <> 'canceled'
                """
            ),
            {"tenant": analytics.tenant_id},
        )
    ).one()

    report = (
        await session.execute(
            select(t.DataQualityReport.findings)
            .where(t.DataQualityReport.tenant_id == analytics.tenant_id)
            .order_by(t.DataQualityReport.generated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    caveats = tuple(
        str(finding["message"])
        for finding in (report or [])
        if finding.get("severity") in LOUD_ENOUGH
    )[:MAX_CAVEATS]

    zone = analytics.tz
    return ShopContext(
        analytics=analytics,
        locations=locations,
        channels=channels,
        categories=categories,
        first_sale=window.first_at.astimezone(zone).date() if window.first_at else None,
        last_sale=window.last_at.astimezone(zone).date() if window.last_at else None,
        caveats=caveats,
    )
