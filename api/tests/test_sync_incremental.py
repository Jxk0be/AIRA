"""Incremental sync: only what changed, and nothing deleted by accident.

Drives the fake POS forward a day through its admin endpoint, then syncs. Two
failures this is here to catch:

* an incremental pull that re-reads the whole catalog, which turns a
  thirty-second nightly job into a rate-limited hour; and
* an incremental pull that runs the backfill's "delete what the source no
  longer returned" sweep, which would soft-delete the entire shop on the first
  night.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.canonical import tables as t
from app.canonical.enums import SyncMode
from app.connectors import registry
from app.connectors.secrets import resolve
from app.connectors.sync import SyncEngine

TENANT_SLUG = "tsundoku"
API_BASE = os.environ.get("REGISTERONE_BASE_URL", "http://localhost:8100")
ADMIN_TOKEN = os.environ.get("REGISTERONE_ADMIN_TOKEN", "ro_admin_9c3e77")
SOURCE_DSN = os.environ.get(
    "REGISTERONE_DB_DSN",
    "postgresql+asyncpg://registerone:registerone@127.0.0.1:5433/registerone",
)

pytestmark = pytest.mark.slow


@pytest.fixture
async def source() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SOURCE_DSN, poolclass=NullPool)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        try:
            await session.execute(text("select 1"))
        except (OSError, OperationalError, InterfaceError, DBAPIError) as exc:
            pytest.skip(f"registerone_db not reachable ({type(exc).__name__})")
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def _context(db: AsyncSession) -> tuple[t.Tenant, t.Integration]:
    tenant = (
        await db.execute(select(t.Tenant).where(t.Tenant.slug == TENANT_SLUG))
    ).scalar_one_or_none()
    if tenant is None:
        pytest.skip("tsundoku is not set up; run `python tasks.py backfill`")
    integration = (
        (await db.execute(select(t.Integration).where(t.Integration.tenant_id == tenant.id)))
        .scalars()
        .first()
    )
    assert integration is not None
    return tenant, integration


async def _counts(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, int]:
    out: dict[str, int] = {}
    for model in (t.Product, t.Variant, t.Customer, t.Order, t.OrderLine, t.Location):
        out[model.__tablename__] = (
            await db.execute(
                select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
            )
        ).scalar_one()
    return out


async def test_incremental_picks_up_a_simulated_day_and_nothing_else(
    db: AsyncSession, source: AsyncSession
) -> None:
    tenant, integration = await _context(db)
    # Hold the ids as plain values: the sync engine commits repeatedly, and a
    # lazy attribute refresh in the middle of an assertion is a confusing way
    # to discover that.
    tenant_id = tenant.id
    timezone = tenant.timezone
    assert timezone

    before = await _counts(db, tenant_id)
    if before["orders"] == 0:
        pytest.skip("tsundoku has not been backfilled yet")
    latest_before = (
        await db.execute(select(func.max(t.Order.placed_at)).where(t.Order.tenant_id == tenant_id))
    ).scalar_one()

    async with httpx.AsyncClient(base_url=API_BASE, timeout=60) as client:
        try:
            response = await client.post(
                "/_simulate/day",
                json={"orders": 7},
                headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            )
        except httpx.HTTPError as exc:
            pytest.skip(f"RegisterOne API not reachable ({exc})")
        assert response.status_code == 200, response.text
        simulated = response.json()

    assert simulated["orders_created"] == 7
    new_ids = set(simulated["order_ids"])

    registry.load_builtin_adapters()
    adapter = registry.build(
        integration.adapter, integration.config, resolve(integration.secret_ref)
    )
    engine = SyncEngine(db, tenant, integration, adapter)
    try:
        report = await engine.run(SyncMode.INCREMENTAL)
    finally:
        await adapter.aclose()
    assert report.ok, report.errors

    after = await _counts(db, tenant_id)

    # The new orders arrived.
    assert after["orders"] == before["orders"] + 7
    landed = (
        (
            await db.execute(
                select(t.Order.external_id).where(
                    t.Order.tenant_id == tenant_id, t.Order.external_id.in_(sorted(new_ids))
                )
            )
        )
        .scalars()
        .all()
    )
    assert set(landed) == new_ids

    # The catalog was not re-walked: a day of sales touches a handful of
    # products, not all of them.
    assert report.counts["orders"].fetched < 60, (
        f"an incremental pull fetched {report.counts['orders'].fetched} orders; "
        "that is a backfill wearing a disguise"
    )
    assert report.counts["products"].fetched < before["products"] / 2

    # Nothing was swept. This is the one that would quietly destroy a customer.
    assert report.total("soft_deleted") == 0, "an incremental sync soft-deleted rows"
    deleted_now = (
        await db.execute(
            select(func.count())
            .select_from(t.Product)
            .where(t.Product.tenant_id == tenant_id, t.Product.deleted_at.is_not(None))
        )
    ).scalar_one()
    assert deleted_now == 8, f"{deleted_now} products are deleted; the fixture only deletes 8"

    # Existing rows were left alone.
    assert after["products"] == before["products"]
    assert after["variants"] == before["variants"]
    assert after["locations"] == before["locations"]

    latest_after = (
        await db.execute(select(func.max(t.Order.placed_at)).where(t.Order.tenant_id == tenant_id))
    ).scalar_one()
    assert latest_after > latest_before

    # And the books still balance against the source.
    ours = (
        await db.execute(
            text(
                """
                select coalesce(sum(l.quantity * l.unit_price - l.discount), 0)
                from orders o
                join order_lines l on l.order_id = o.id and l.deleted_at is null
                where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
                """
            ),
            {"tenant": str(tenant_id)},
        )
    ).scalar_one()
    theirs = (
        await source.execute(
            text(
                """
                select coalesce(sum(l.gross_sales_money - l.total_discount_money), 0) / 100.0
                from orders o join order_line_items l on l.order_id = o.id
                where o.state <> 'CANCELED'
                """
            )
        )
    ).scalar_one()
    assert abs(Decimal(ours) - Decimal(theirs)) <= Decimal("0.01")


async def test_the_watermark_follows_the_source_clock_not_ours(db: AsyncSession) -> None:
    """The fixture's newest order can be dated later today, or even tomorrow.

    Watermarking on our own wall clock would step past those records and they
    would never sync. The state row has to hold the highest timestamp the
    *source* reported.
    """
    tenant, integration = await _context(db)
    tenant_id = tenant.id
    state = (
        await db.execute(
            select(t.SyncState.entity, t.SyncState.last_synced_at).where(
                t.SyncState.tenant_id == tenant_id,
                t.SyncState.integration_id == integration.id,
                t.SyncState.entity == "orders",
            )
        )
    ).first()
    if state is None:
        pytest.skip("orders have not been synced yet")

    newest_source_stamp = (
        await db.execute(
            select(func.max(t.Order.source_updated_at)).where(t.Order.tenant_id == tenant_id)
        )
    ).scalar_one()
    assert newest_source_stamp is not None
    assert state.last_synced_at == newest_source_stamp, (
        f"watermark is {state.last_synced_at}, but the newest record the source gave us "
        f"is {newest_source_stamp}"
    )
    assert isinstance(state.last_synced_at, datetime)
