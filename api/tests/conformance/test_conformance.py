"""The conformance suite.

Every adapter must pass this, and passing it is what "the adapter is done"
means. The checks are written once, against the canonical model — they never
mention a platform, a vendor, or a field name from anyone's API.

Checks a source's declared capabilities rule out are skipped, not failed: a
spreadsheet with no customer column cannot be asked about repeat customers, and
that is a correct answer rather than a missing one.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Numeric, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.canonical.enums import Channel, OrderStatus, SyncMode
from app.connectors.base import SourceAdapter
from app.connectors.sync import SyncEngine
from tests.conformance.conftest import SyncedTenant

ONE_CENT = Decimal("0.01")


async def scalar(session: AsyncSession, sql: str, **params: object) -> object:
    return (await session.execute(text(sql), params)).scalar_one()


# ---------------------------------------------------------------------------
# Types and units
# ---------------------------------------------------------------------------


async def test_money_is_decimal_dollars(synced: SyncedTenant) -> None:
    """Dollars, as exact Decimals.

    The failure this catches is cents landing unconverted: a $12.99 manga
    volume stored as 1299. Whole-number-only money across a whole catalog is
    the signature.
    """
    rows = (
        await synced.session.execute(
            select(t.OrderLine.unit_price, t.OrderLine.quantity)
            .where(t.OrderLine.tenant_id == synced.tenant.id, t.OrderLine.unit_price > 0)
            .limit(500)
        )
    ).all()
    assert rows, "no order lines to check"

    for unit_price, quantity in rows:
        assert isinstance(unit_price, Decimal), f"{unit_price!r} is {type(unit_price).__name__}"
        assert isinstance(quantity, Decimal)

    fractional = sum(1 for price, _ in rows if price % 1 != 0)
    assert fractional > 0, (
        "every unit price is a whole number, which is what cents-stored-as-dollars looks like"
    )

    biggest = (
        await synced.session.execute(
            select(func.max(t.OrderLine.unit_price)).where(
                t.OrderLine.tenant_id == synced.tenant.id
            )
        )
    ).scalar_one()
    assert biggest < Decimal("100000"), f"a unit price of {biggest} suggests unconverted cents"


async def test_timestamps_are_timezone_aware_utc(synced: SyncedTenant) -> None:
    columns = [
        (t.Order, t.Order.placed_at),
        (t.Refund, t.Refund.occurred_at),
        (t.InventoryLevel, t.InventoryLevel.as_of),
    ]
    for model, column in columns:
        value = (
            await synced.session.execute(
                select(column).where(model.tenant_id == synced.tenant.id).limit(1)
            )
        ).scalar_one_or_none()
        if value is None:
            continue
        assert isinstance(value, datetime)
        assert value.tzinfo is not None, f"{column.key} came back naive"
        assert value.utcoffset() == timedelta(0), f"{column.key} is not UTC"


async def test_every_row_carries_this_tenant_and_source(synced: SyncedTenant) -> None:
    """Tenant isolation is a schema guarantee; this checks the sync honoured it."""
    for model in (t.Product, t.Variant, t.Order, t.OrderLine, t.InventoryLevel):
        wrong = (
            await synced.session.execute(
                select(func.count())
                .select_from(model)
                .where(model.tenant_id == synced.tenant.id, model.source != synced.source)
            )
        ).scalar_one()
        assert wrong == 0, f"{model.__tablename__} has {wrong} rows from another source"


# ---------------------------------------------------------------------------
# Arithmetic the source has to survive
# ---------------------------------------------------------------------------


async def test_order_totals_reconcile_with_their_lines(synced: SyncedTenant) -> None:
    """total == subtotal - discounts + tax + tips, to the cent.

    Partial refunds, split payments and custom-amount lines are all places this
    quietly stops being true.
    """
    drift = await scalar(
        synced.session,
        """
        select count(*) from (
          select o.id,
                 o.total - (
                   coalesce((select sum(l.quantity * l.unit_price)
                             from order_lines l
                             where l.order_id = o.id and l.deleted_at is null), 0)
                   - o.discount_total + o.tax_total + o.tip_total) as drift
          from orders o
          where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
        ) d where abs(drift) > 0.01
        """,
        tenant=str(synced.tenant.id),
    )
    assert drift == 0, f"{drift} orders do not reconcile with their line items"


async def test_refunds_never_exceed_what_was_charged(synced: SyncedTenant) -> None:
    over = await scalar(
        synced.session,
        """
        select count(*) from (
          select o.id, o.total, coalesce(sum(r.amount), 0) as refunded
          from orders o
          left join refunds r on r.order_id = o.id and r.deleted_at is null
          where o.tenant_id = :tenant and o.deleted_at is null
          group by o.id, o.total
        ) x where refunded > total + 0.01
        """,
        tenant=str(synced.tenant.id),
    )
    assert over == 0, f"{over} orders were refunded for more than they were charged"


async def test_quantities_and_stock_are_never_silently_negative(synced: SyncedTenant) -> None:
    negative_lines = await scalar(
        synced.session,
        "select count(*) from order_lines where tenant_id = :tenant and quantity < 0",
        tenant=str(synced.tenant.id),
    )
    assert negative_lines == 0, f"{negative_lines} order lines have a negative quantity"


# ---------------------------------------------------------------------------
# History survives the present
# ---------------------------------------------------------------------------


async def test_soft_deleted_products_still_resolve_for_old_order_lines(
    synced: SyncedTenant,
) -> None:
    """A product deleted in the POS today must not erase last spring's sales."""
    deleted_variants = (
        await synced.session.execute(
            select(func.count())
            .select_from(t.Variant)
            .where(t.Variant.tenant_id == synced.tenant.id, t.Variant.deleted_at.is_not(None))
        )
    ).scalar_one()
    if not deleted_variants:
        pytest.skip("this source has no deleted products to check")

    resolvable = await scalar(
        synced.session,
        """
        select count(*)
        from order_lines l
        join variants v on v.id = l.variant_id
        join products p on p.id = v.product_id
        where l.tenant_id = :tenant and v.deleted_at is not null
        """,
        tenant=str(synced.tenant.id),
    )
    assert resolvable > 0, (
        "products were deleted upstream but no historical line still joins to one, "
        "so history lost its products"
    )


async def test_line_snapshots_survive_a_rename(synced: SyncedTenant) -> None:
    """Every line carries what the receipt said, not what the catalog says now."""
    blank = await scalar(
        synced.session,
        "select count(*) from order_lines "
        "where tenant_id = :tenant and coalesce(name_snapshot, '') = ''",
        tenant=str(synced.tenant.id),
    )
    assert blank == 0, f"{blank} order lines have no name snapshot"


async def test_custom_amount_lines_are_allowed_and_still_count(synced: SyncedTenant) -> None:
    """A line with no product behind it is normal, and its money is real."""
    orphan_lines = await scalar(
        synced.session,
        "select count(*) from order_lines where tenant_id = :tenant and variant_id is null "
        "and deleted_at is null",
        tenant=str(synced.tenant.id),
    )
    if not orphan_lines:
        pytest.skip("this source has no custom-amount lines")
    zero_money = await scalar(
        synced.session,
        "select count(*) from order_lines where tenant_id = :tenant and variant_id is null "
        "and deleted_at is null and quantity * unit_price <= 0",
        tenant=str(synced.tenant.id),
    )
    assert zero_money == 0, f"{zero_money} custom-amount lines came through with no money on them"


# ---------------------------------------------------------------------------
# Capabilities have to be true
# ---------------------------------------------------------------------------


async def test_declared_capabilities_are_backed_by_data(synced: SyncedTenant) -> None:
    """A capability is a promise the rest of AIRA acts on.

    `has_costs` turns on margin. `has_customers` turns on repeat rate. Claiming
    one and delivering nothing produces a confident wrong answer, which is the
    one outcome worse than no answer.
    """
    caps = synced.capabilities
    tenant_id = str(synced.tenant.id)

    if caps.has_costs:
        with_cost = await scalar(
            synced.session,
            "select count(*) from variants where tenant_id = :tenant and cost is not null",
            tenant=tenant_id,
        )
        assert with_cost > 0, "has_costs is declared but not one variant has a cost"

    if caps.has_customers:
        identified = await scalar(
            synced.session,
            "select count(*) from orders where tenant_id = :tenant and customer_id is not null",
            tenant=tenant_id,
        )
        assert identified > 0, "has_customers is declared but no order has a customer"

    if caps.multi_location:
        locations = await scalar(
            synced.session,
            "select count(*) from locations where tenant_id = :tenant and deleted_at is null",
            tenant=tenant_id,
        )
        assert locations > 1, f"multi_location is declared but there is only {locations} location"

    if caps.has_online_channel:
        channels = (
            (
                await synced.session.execute(
                    select(t.Order.channel).where(t.Order.tenant_id == synced.tenant.id).distinct()
                )
            )
            .scalars()
            .all()
        )
        assert set(channels) - {Channel.IN_STORE}, (
            "has_online_channel is declared but every order is in_store"
        )

    if caps.has_inventory_history:
        movements = await scalar(
            synced.session,
            "select count(*) from inventory_movements where tenant_id = :tenant",
            tenant=tenant_id,
        )
        assert movements > 0, "has_inventory_history is declared but no movements were synced"


async def test_capabilities_not_declared_are_not_quietly_invented(
    synced: SyncedTenant,
) -> None:
    """The inverse: a source that says it has no costs must not have costs
    appearing from somewhere, because that somewhere would be a guess."""
    caps = synced.capabilities
    tenant_id = str(synced.tenant.id)

    if not caps.has_costs:
        with_cost = await scalar(
            synced.session,
            "select count(*) from order_lines "
            "where tenant_id = :tenant and unit_cost_snapshot is not null",
            tenant=tenant_id,
        )
        assert with_cost == 0, (
            f"has_costs is false but {with_cost} order lines carry a cost snapshot"
        )

    if not caps.has_customers:
        identified = await scalar(
            synced.session,
            "select count(*) from orders where tenant_id = :tenant and customer_id is not null",
            tenant=tenant_id,
        )
        assert identified == 0, "has_customers is false but orders have customers attached"


# ---------------------------------------------------------------------------
# Talking to the source again
# ---------------------------------------------------------------------------


@pytest.mark.slow
async def test_since_returns_exactly_the_records_that_changed_after_it(
    synced: SyncedTenant, adapter: SourceAdapter
) -> None:
    """Incremental pulls must be a correct subset of everything — no more, and
    crucially no less.

    Ground truth comes from the adapter's own unfiltered pull rather than from
    what happens to be in our database. Comparing against canonical rows would
    make this fail whenever the source has simply moved on since the last sync,
    which says nothing about whether `since` works.
    """
    if not synced.capabilities.supports_incremental:
        pytest.skip("this source cannot filter by a changed-since watermark")

    everything: dict[str, datetime] = {}
    async for fetched in adapter.iter_orders():
        stamped = fetched.record.source_updated_at
        assert stamped is not None, (
            f"order {fetched.record.external_id} has no source_updated_at, so it can never "
            "be picked up by an incremental sync"
        )
        everything[fetched.record.external_id] = stamped
    assert everything, "the source returned no orders at all"

    newest = max(everything.values())
    cutoff = newest - timedelta(days=14)
    expected = {key for key, stamp in everything.items() if stamp >= cutoff}
    assert 0 < len(expected) < len(everything), (
        "the fixture is too small or too bunched up for this check to mean anything"
    )

    seen: set[str] = set()
    async for fetched in adapter.iter_orders(since=cutoff):
        seen.add(fetched.record.external_id)
        stamped = fetched.record.source_updated_at
        assert stamped is not None and stamped >= cutoff, (
            f"order {fetched.record.external_id} changed at {stamped}, before the {cutoff} cutoff"
        )

    missed = expected - seen
    assert not missed, (
        f"{len(missed)} orders changed after the cutoff but `since` did not return them "
        f"(for example {sorted(missed)[:3]}); an incremental sync would lose them for good"
    )
    assert seen == expected


@pytest.mark.slow
async def test_a_second_backfill_changes_nothing(
    synced: SyncedTenant, adapter: SourceAdapter
) -> None:
    """Idempotency and id stability, in one run.

    Re-syncing must update rows rather than duplicate them, and must keep every
    row's id: the ids are what conversations, pinned charts and embeddings
    point at.

    This establishes its own baseline with a first backfill rather than trusting
    whatever state the tenant is in. Otherwise anything that legitimately moves
    the source forward — `tasks.py simulate-day`, the incremental test — makes a
    perfectly idempotent adapter look like it is inventing rows, because the
    "second" backfill is really the first one to see those records.
    """
    tenant_id = synced.tenant.id
    models = (t.Location, t.Category, t.Product, t.Variant, t.Customer, t.Order, t.OrderLine)

    async def snapshot() -> tuple[dict[str, int], dict[str, str]]:
        counts: dict[str, int] = {}
        for model in models:
            counts[model.__tablename__] = (
                await synced.session.execute(
                    select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
                )
            ).scalar_one()
        ids = {
            f"{external_id}": str(row_id)
            for external_id, row_id in (
                await synced.session.execute(
                    select(t.Variant.external_id, t.Variant.id)
                    .where(t.Variant.tenant_id == tenant_id)
                    .order_by(t.Variant.external_id)
                    .limit(200)
                )
            ).all()
        }
        return counts, ids

    engine = SyncEngine(synced.session, synced.tenant, synced.integration, adapter)
    baseline = await engine.run(SyncMode.BACKFILL)
    assert baseline.ok, f"the baseline backfill failed: {baseline.errors}"

    before_counts, before_ids = await snapshot()

    report = await engine.run(SyncMode.BACKFILL)
    assert report.ok, f"the second backfill failed: {report.errors}"

    synced.session.expire_all()
    after_counts, after_ids = await snapshot()

    assert after_counts == before_counts, (
        f"row counts moved on a re-sync: {before_counts} -> {after_counts}"
    )
    assert after_ids == before_ids, "a re-sync changed the ids of existing rows"
    assert report.total("soft_deleted") == 0, (
        "the second backfill soft-deleted rows the first one had just created"
    )


async def test_the_adapter_describes_what_the_integration_stored(
    synced: SyncedTenant, adapter: SourceAdapter
) -> None:
    """The adapter is the authority on its own capabilities; a stale
    integration row is how a shop ends up with a tool it cannot support."""
    info = adapter.describe()
    assert info.capabilities.model_dump() == synced.capabilities.model_dump(), (
        "the integration's stored capabilities have drifted from the adapter's"
    )


async def test_orders_have_a_status_and_channel_from_our_vocabulary(
    synced: SyncedTenant,
) -> None:
    statuses = (
        (
            await synced.session.execute(
                select(t.Order.status).where(t.Order.tenant_id == synced.tenant.id).distinct()
            )
        )
        .scalars()
        .all()
    )
    channels = (
        (
            await synced.session.execute(
                select(t.Order.channel).where(t.Order.tenant_id == synced.tenant.id).distinct()
            )
        )
        .scalars()
        .all()
    )

    assert set(statuses) <= set(OrderStatus), f"unknown order statuses: {set(statuses)}"
    assert set(channels) <= set(Channel), f"unknown channels: {set(channels)}"


async def test_inventory_levels_are_one_row_per_variant_and_location(
    synced: SyncedTenant,
) -> None:
    duplicates = await scalar(
        synced.session,
        """
        select count(*) from (
          select variant_id, location_id from inventory_levels
          where tenant_id = :tenant and deleted_at is null
          group by 1, 2 having count(*) > 1
        ) d
        """,
        tenant=str(synced.tenant.id),
    )
    assert duplicates == 0, f"{duplicates} variant/location pairs have more than one stock level"


async def test_merged_customers_point_at_a_survivor_that_is_not_merged(
    synced: SyncedTenant,
) -> None:
    """Duplicates are merged, never deleted, and never into another duplicate."""
    if not synced.capabilities.has_customers:
        pytest.skip("this source has no customers")

    chained = await scalar(
        synced.session,
        """
        select count(*) from customers c
        join customers s on s.id = c.merged_into_id
        where c.tenant_id = :tenant and s.merged_into_id is not null
        """,
        tenant=str(synced.tenant.id),
    )
    assert chained == 0, f"{chained} customers were merged into another merged customer"

    vanished = await scalar(
        synced.session,
        "select count(*) from orders o left join customers c on c.id = o.customer_id "
        "where o.tenant_id = :tenant and o.customer_id is not null and c.id is null",
        tenant=str(synced.tenant.id),
    )
    assert vanished == 0, f"{vanished} orders point at a customer that no longer exists"


async def test_raw_payloads_were_landed_before_mapping(synced: SyncedTenant) -> None:
    """`raw_records` is what makes a number arguable against the source."""
    orders = (
        await synced.session.execute(
            select(func.count()).select_from(t.Order).where(t.Order.tenant_id == synced.tenant.id)
        )
    ).scalar_one()
    landed = await scalar(
        synced.session,
        "select count(*) from raw_records where tenant_id = :tenant and entity = 'orders'",
        tenant=str(synced.tenant.id),
    )
    assert landed >= orders, f"{orders} orders were synced but only {landed} raw payloads were kept"


async def test_money_columns_are_exact_not_floating_point(synced: SyncedTenant) -> None:
    for model, column in (
        (t.Order, "total"),
        (t.OrderLine, "unit_price"),
        (t.Variant, "cost"),
        (t.Refund, "amount"),
    ):
        kind = model.__table__.c[column].type
        assert isinstance(kind, Numeric), (
            f"{model.__tablename__}.{column} is {type(kind).__name__}, not NUMERIC"
        )


async def test_the_sync_recorded_what_it_did(synced: SyncedTenant) -> None:
    """A run nobody can inspect afterwards is not a run you can trust."""
    run = (
        await synced.session.execute(
            select(t.SyncRun)
            .where(t.SyncRun.tenant_id == synced.tenant.id)
            .order_by(t.SyncRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    assert run is not None, "no sync_runs row was written"
    assert run.finished_at is not None, "the last run never closed"
    assert run.counts, "the run recorded no counts"

    report = (
        await synced.session.execute(
            select(t.DataQualityReport)
            .where(t.DataQualityReport.tenant_id == synced.tenant.id)
            .order_by(t.DataQualityReport.generated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    assert report is not None, "no data quality report was written"
    assert "cost_coverage_share" in report.metrics


async def test_dates_bucket_in_the_shop_timezone_not_utc(synced: SyncedTenant) -> None:
    """Sanity check on the thing that is wrong-by-a-few-hours when it is wrong.

    A shop trading in the evening in a negative-offset timezone has sales that
    fall on the *next* UTC day. If bucketing by UTC and by the tenant's zone
    give the same answer for every day, either the timezone is UTC or the
    conversion is not happening.
    """
    if synced.tenant.timezone == "UTC":
        pytest.skip("this tenant trades in UTC, so there is nothing to disagree about")

    times_of_day = await scalar(
        synced.session,
        "select count(distinct (placed_at at time zone :tz)::time) from orders "
        "where tenant_id = :tenant",
        tenant=str(synced.tenant.id),
        tz=synced.tenant.timezone,
    )
    if int(times_of_day or 0) <= 1:
        # A file export records the day but not the clock, so every sale sits at
        # the same assumed hour and none of them can straddle a UTC boundary.
        # That is the adapter behaving correctly, not a timezone bug.
        pytest.skip("this source records dates without times, so there is no clock to convert")

    differing = await scalar(
        synced.session,
        """
        select count(*) from orders
        where tenant_id = :tenant
          and (placed_at at time zone :tz)::date <> (placed_at at time zone 'UTC')::date
        """,
        tenant=str(synced.tenant.id),
        tz=synced.tenant.timezone,
    )
    assert differing > 0, (
        "not one order falls on a different day in the shop's timezone than in UTC, "
        "which means the timestamps are probably not real local trading hours"
    )
