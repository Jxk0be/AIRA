"""Putting a rescue plan together, and logging what the owner actually did.

The one-click "I did this" is the point of the whole feature. Without it the
product has opinions; with it, it has a record of what its opinions were worth,
which is the difference between a dashboard and a subscription.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext, Filters
from app.analytics.stale import (
    StaleItem,
    StaleReport,
    category_by_location,
    co_purchases,
    stale_inventory,
)
from app.canonical import tables as t
from app.db import table_of
from app.deadstock.rescue import (
    PHRASING_SYSTEM,
    Rescue,
    apply_phrasing,
    choose,
    phrasing_payload,
)
from app.deadstock.tables import RescueAction
from app.llm import Phraser, check_numbers, facts_from

NO_FILTERS = Filters()

# How many items get a worked-out plan. Beyond this the list is a spreadsheet,
# not a plan, and the owner will not work through it.
PLANNED_ITEMS = 25


@dataclass(slots=True)
class RescuePlan:
    report: StaleReport
    rescues: list[Rescue]

    @property
    def total_cash(self) -> Decimal:
        return self.report.total_cash

    @property
    def planned_cash(self) -> Decimal:
        return sum((r.cash_tied_up for r in self.rescues), Decimal("0"))


async def plan(
    session: AsyncSession,
    ctx: AnalyticsContext,
    filters: Filters = NO_FILTERS,
    *,
    as_of: date | None = None,
    phraser: Phraser | None = None,
    phrase: bool = False,
    limit: int = PLANNED_ITEMS,
) -> RescuePlan:
    """Grade the shelf, then work out what to do about the worst of it.

    `phrase` sends the rule-written reasons through the cheap model to be
    reworded, and is off by default. It costs a round trip per batch, which is
    nothing in the weekly detector run and is twenty seconds of a blank screen
    if it happens while somebody is waiting for a page. The plain wording is
    always correct; it is only plainer.
    """
    report = await stale_inventory(session, ctx, filters, as_of=as_of)
    worst = sorted(report.items, key=lambda item: item.cash_tied_up, reverse=True)[:limit]
    if not worst:
        return RescuePlan(report=report, rescues=[])

    variant_ids = [item.variant_id for item in worst]
    partners = await co_purchases(session, ctx, variant_ids, as_of=as_of)
    locations = (
        await category_by_location(session, ctx, as_of=as_of) if ctx.has("multi_location") else {}
    )
    vendors = await vendor_terms(session, ctx, variant_ids)
    shelves = await _where_it_sits(session, ctx, variant_ids)

    rescues = [
        choose(
            item,
            partners=partners.get(item.variant_id, []),
            locations=locations.get(item.category_id, []),
            current_location_id=shelves.get(item.variant_id),
            vendor=vendors.get(item.variant_id),
            multi_location=ctx.has("multi_location"),
        )
        for item in worst
    ]

    if phrase:
        await _phrase(rescues, phraser)
    return RescuePlan(report=report, rescues=rescues)


async def _phrase(rescues: list[Rescue], phraser: Phraser | None) -> None:
    """Let the cheap model rewrite the reasons, and check what it wrote.

    The play, the price and the discount are already decided and are not sent
    back through the model's answer — only the sentence is. Anything it writes
    that contains a figure the plan did not is thrown away.
    """
    phraser = phraser or Phraser()
    if not phraser.available or not rescues:
        return

    payload = phrasing_payload(rescues)
    facts = facts_from(payload)

    def parse(raw: Any) -> dict[str, str]:
        if not isinstance(raw, dict):
            raise TypeError("expected an object keyed by item id")
        out: dict[str, str] = {}
        for key, value in raw.items():
            if not isinstance(value, str):
                continue
            check_numbers(value, facts)
            out[str(key)] = value.strip()
        return out

    unphrased: dict[str, str] = {}
    phrased = await phraser.json(
        system=PHRASING_SYSTEM,
        payload=payload,
        instruction=(
            "Rewrite each item's `reason` as one plain sentence a shop owner would read. "
            'Reply as {"<id>": "<sentence>"} using the ids given.'
        ),
        parse=parse,
        fallback=unphrased,
        max_tokens=1500,
    )
    apply_phrasing(rescues, phrased)


async def vendor_terms(
    session: AsyncSession, ctx: AnalyticsContext, variant_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any]]:
    """The supplier behind each variant, where one is on file.

    `takes_returns` comes from the vendor's notes rather than a column: no POS
    we have met records a returns policy, so it is something the owner writes
    down and we read back, and a vendor nobody has annotated is assumed not to
    take returns.
    """
    if not variant_ids:
        return {}
    rows = (
        await session.execute(
            select(
                t.VariantVendor.variant_id,
                t.Vendor.id,
                t.Vendor.name,
                t.Vendor.email,
                t.Vendor.notes,
            )
            .join(t.Vendor, t.Vendor.id == t.VariantVendor.vendor_id)
            .where(
                t.VariantVendor.tenant_id == ctx.tenant_id,
                t.VariantVendor.deleted_at.is_(None),
                t.Vendor.deleted_at.is_(None),
                t.VariantVendor.variant_id.in_(variant_ids),
            )
            .order_by(t.VariantVendor.is_primary.desc())
        )
    ).all()

    out: dict[uuid.UUID, dict[str, Any]] = {}
    for variant_id, vendor_id, name, email, notes in rows:
        if variant_id in out:
            continue
        haystack = (notes or "").lower()
        out[variant_id] = {
            "id": vendor_id,
            "name": name,
            "email": email,
            "takes_returns": "return" in haystack and "no return" not in haystack,
        }
    return out


async def _where_it_sits(
    session: AsyncSession, ctx: AnalyticsContext, variant_ids: list[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID | None]:
    """The location holding most of each variant, so "move it" has a from."""
    if not variant_ids:
        return {}
    rows = (
        await session.execute(
            select(
                t.InventoryLevel.variant_id,
                t.InventoryLevel.location_id,
                t.InventoryLevel.on_hand,
            )
            .where(
                t.InventoryLevel.tenant_id == ctx.tenant_id,
                t.InventoryLevel.deleted_at.is_(None),
                t.InventoryLevel.variant_id.in_(variant_ids),
            )
            .order_by(t.InventoryLevel.on_hand.desc())
        )
    ).all()
    out: dict[uuid.UUID, uuid.UUID | None] = {}
    for variant_id, location_id, _ in rows:
        out.setdefault(variant_id, location_id)
    return out


# --------------------------------------------------------------------------
# Logging what was done
# --------------------------------------------------------------------------


async def log_action(
    session: AsyncSession,
    ctx: AnalyticsContext,
    *,
    variant_id: uuid.UUID,
    kind: str,
    detail: dict[str, Any] | None = None,
    price_after: Decimal | None = None,
    insight_id: uuid.UUID | None = None,
    note: str | None = None,
) -> uuid.UUID:
    """Record "I did this", capturing the before state as it stands right now."""
    before = (
        await session.execute(
            select(t.Variant.price, t.Variant.cost).where(t.Variant.id == variant_id)
        )
    ).first()
    on_hand = (
        (
            await session.execute(
                select(t.InventoryLevel.on_hand).where(
                    t.InventoryLevel.tenant_id == ctx.tenant_id,
                    t.InventoryLevel.variant_id == variant_id,
                    t.InventoryLevel.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    action_id = uuid.uuid4()
    await session.execute(
        insert(table_of(RescueAction)).values(
            id=action_id,
            tenant_id=ctx.tenant_id,
            variant_id=variant_id,
            insight_id=insight_id,
            kind=kind,
            detail=detail or {},
            price_before=before[0] if before else None,
            price_after=price_after,
            on_hand_before=sum((Decimal(value) for value in on_hand), Decimal("0")),
            unit_cost=before[1] if before else None,
            taken_at=datetime.now(tz=UTC),
            note=note,
        )
    )
    await session.commit()
    return action_id


async def recent_actions(
    session: AsyncSession, ctx: AnalyticsContext, *, limit: int = 50
) -> list[RescueAction]:
    rows = (
        (
            await session.execute(
                select(RescueAction)
                .where(RescueAction.tenant_id == ctx.tenant_id)
                .order_by(RescueAction.taken_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def mark_measured(session: AsyncSession, action_id: uuid.UUID) -> None:
    table = table_of(RescueAction)
    await session.execute(
        update(table).where(table.c.id == action_id).values(measured_at=datetime.now(tz=UTC))
    )


def item_by_id(report: StaleReport, variant_id: uuid.UUID) -> StaleItem | None:
    return next((item for item in report.items if item.variant_id == variant_id), None)
