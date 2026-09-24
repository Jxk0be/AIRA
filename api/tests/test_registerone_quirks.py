"""One test per entry in `sources/registerone/QUIRKS.md`.

The conformance suite checks that an adapter is *correct*. This file checks
that the RegisterOne adapter survives the specific mess the fixture contains —
and, where it matters, compares the canonical numbers against the source
system's own, to the cent.

These tests may open `registerone_db` for ground truth. The adapter may not:
that is the whole point of the mock API standing in front of it.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.canonical import tables as t
from app.canonical.enums import Channel, OrderStatus

TENANT_SLUG = "tsundoku"
SOURCE_DSN = os.environ.get(
    "REGISTERONE_DB_DSN",
    "postgresql+asyncpg://registerone:registerone@127.0.0.1:5433/registerone",
)
CENT = Decimal("0.01")


@pytest.fixture
async def tenant(db: AsyncSession) -> t.Tenant:
    found = (
        await db.execute(select(t.Tenant).where(t.Tenant.slug == TENANT_SLUG))
    ).scalar_one_or_none()
    if found is None:
        pytest.skip("tsundoku is not synced; run `python tasks.py backfill`")
    orders = (
        await db.execute(
            select(func.count()).select_from(t.Order).where(t.Order.tenant_id == found.id)
        )
    ).scalar_one()
    if not orders:
        pytest.skip("tsundoku has no orders; run `python tasks.py backfill`")
    return found


@pytest.fixture
async def source() -> AsyncIterator[AsyncSession]:
    """Ground truth, straight out of the fake POS's database."""
    engine = create_async_engine(SOURCE_DSN, poolclass=NullPool)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        try:
            await session.execute(text("select 1"))
        except (OSError, OperationalError, InterfaceError, DBAPIError) as exc:
            pytest.skip(f"registerone_db not reachable ({type(exc).__name__}); `tasks.py sources`")
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def canonical(db: AsyncSession, tenant: t.Tenant, sql: str, **params: object) -> object:
    return (await db.execute(text(sql), {"tenant": str(tenant.id), **params})).scalar_one()


async def in_source(source: AsyncSession, sql: str, **params: object) -> object:
    return (await source.execute(text(sql), params)).scalar_one()


# ---------------------------------------------------------------------------
# The numbers themselves
# ---------------------------------------------------------------------------


async def test_net_sales_match_the_source_to_the_cent(
    db: AsyncSession, tenant: t.Tenant, source: AsyncSession
) -> None:
    """The single most important check in this file.

    Gross, discounts and refunds, computed independently on each side. If these
    drift, every number AIRA ever shows this shop is wrong by the same amount.
    """
    ours = (
        await db.execute(
            text(
                """
                select
                  coalesce(sum(l.quantity * l.unit_price), 0) as gross,
                  coalesce(sum(l.discount), 0) as discounts
                from orders o
                join order_lines l on l.order_id = o.id and l.deleted_at is null
                where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
                """
            ),
            {"tenant": str(tenant.id)},
        )
    ).one()
    theirs = (
        await source.execute(
            text(
                """
                select
                  coalesce(sum(l.gross_sales_money), 0) / 100.0 as gross,
                  coalesce(sum(l.total_discount_money), 0) / 100.0 as discounts
                from orders o
                join order_line_items l on l.order_id = o.id
                where o.state <> 'CANCELED'
                """
            )
        )
    ).one()

    assert abs(Decimal(ours.gross) - Decimal(theirs.gross)) <= CENT, (
        f"gross sales: canonical {ours.gross} vs source {theirs.gross}"
    )
    assert abs(Decimal(ours.discounts) - Decimal(theirs.discounts)) <= CENT

    our_refunds = await canonical(
        db,
        tenant,
        "select coalesce(sum(amount), 0) from refunds "
        "where tenant_id = :tenant and deleted_at is null",
    )
    their_refunds = await in_source(
        source, "select coalesce(sum(amount_money), 0) / 100.0 from refunds"
    )
    assert abs(Decimal(our_refunds) - Decimal(their_refunds)) <= CENT


async def test_monthly_net_sales_match_the_source_in_shop_local_time(
    db: AsyncSession, tenant: t.Tenant, source: AsyncSession
) -> None:
    """Bucketed in America/New_York on both sides.

    A UTC bucket would move the evening's sales onto the next day and show up
    here as a few dollars adrift at every month boundary.
    """
    ours = {
        row.month: Decimal(row.net)
        for row in (
            await db.execute(
                text(
                    """
                    select to_char(date_trunc('month', o.placed_at at time zone :tz), 'YYYY-MM')
                             as month,
                           sum(l.quantity * l.unit_price - l.discount) as net
                    from orders o
                    join order_lines l on l.order_id = o.id and l.deleted_at is null
                    where o.tenant_id = :tenant and o.deleted_at is null
                      and o.status <> 'canceled'
                    group by 1
                    """
                ),
                {"tenant": str(tenant.id), "tz": tenant.timezone},
            )
        ).all()
    }
    theirs = {
        row.month: Decimal(row.net)
        for row in (
            await source.execute(
                text(
                    """
                    select to_char(date_trunc('month', o.created_at at time zone :tz), 'YYYY-MM')
                             as month,
                           sum(l.gross_sales_money - l.total_discount_money) / 100.0 as net
                    from orders o
                    join order_line_items l on l.order_id = o.id
                    where o.state <> 'CANCELED'
                    group by 1
                    """
                ),
                {"tz": tenant.timezone},
            )
        ).all()
    }

    assert set(ours) == set(theirs), "the two sides disagree about which months have sales"
    adrift = {m: (ours[m], theirs[m]) for m in ours if abs(ours[m] - theirs[m]) > CENT}
    assert not adrift, f"months that do not match: {adrift}"


# ---------------------------------------------------------------------------
# Quirk 1 — orders with no customer
# ---------------------------------------------------------------------------


async def test_cash_walk_ins_arrive_with_no_customer(
    db: AsyncSession, tenant: t.Tenant, source: AsyncSession
) -> None:
    anonymous = await canonical(
        db,
        tenant,
        "select count(*) from orders "
        "where tenant_id = :tenant and deleted_at is null and customer_id is null",
    )
    total = await canonical(
        db, tenant, "select count(*) from orders where tenant_id = :tenant and deleted_at is null"
    )
    theirs = await in_source(source, "select count(*) from orders where customer_id is null")

    assert anonymous == theirs, "the adapter invented or dropped a customer somewhere"
    assert 0.4 < anonymous / total < 0.8, f"{anonymous / total:.0%} anonymous looks wrong"


# ---------------------------------------------------------------------------
# Quirk 2 — custom-amount lines with no catalog object
# ---------------------------------------------------------------------------


async def test_custom_amount_lines_keep_their_money_and_their_name(
    db: AsyncSession, tenant: t.Tenant, source: AsyncSession
) -> None:
    """`variant_id` null is correct here, and the revenue still counts."""
    ours = (
        await db.execute(
            text(
                """
                select count(*) as lines, coalesce(sum(quantity * unit_price - discount), 0) as net
                from order_lines
                where tenant_id = :tenant and variant_id is null and deleted_at is null
                """
            ),
            {"tenant": str(tenant.id)},
        )
    ).one()
    theirs = (
        await source.execute(
            text(
                """
                select count(*) as lines,
                       coalesce(sum(gross_sales_money - total_discount_money), 0) / 100.0 as net
                from order_line_items where catalog_object_id is null
                """
            )
        )
    ).one()

    assert ours.lines == theirs.lines
    assert abs(Decimal(ours.net) - Decimal(theirs.net)) <= CENT
    assert ours.lines > 0, "the fixture lost its custom-amount lines"

    nameless = await canonical(
        db,
        tenant,
        "select count(*) from order_lines where tenant_id = :tenant and variant_id is null "
        "and coalesce(name_snapshot, '') = ''",
    )
    assert nameless == 0, "custom-amount lines came through with nothing to call them"


# ---------------------------------------------------------------------------
# Quirk 3 — items soft-deleted upstream, still on old orders
# ---------------------------------------------------------------------------


async def test_deleted_products_are_soft_deleted_and_history_still_joins(
    db: AsyncSession, tenant: t.Tenant, source: AsyncSession
) -> None:
    deleted_upstream = await in_source(
        source, "select count(*) from catalog_items where is_deleted"
    )
    ours = await canonical(
        db,
        tenant,
        "select count(*) from products where tenant_id = :tenant and deleted_at is not null",
    )
    assert ours == deleted_upstream, (
        f"{deleted_upstream} items are deleted upstream but {ours} are marked deleted here"
    )

    # Hard deletes would have taken the old sales with them.
    historical = await canonical(
        db,
        tenant,
        """
        select count(*) from order_lines l
        join variants v on v.id = l.variant_id
        join products p on p.id = v.product_id
        where l.tenant_id = :tenant and p.deleted_at is not null
        """,
    )
    assert historical > 0, "no historical line still resolves to a deleted product"


# ---------------------------------------------------------------------------
# Quirk 4 — a category renamed mid-history, same id
# ---------------------------------------------------------------------------


async def test_the_renamed_category_stayed_one_category(
    db: AsyncSession, tenant: t.Tenant, source: AsyncSession
) -> None:
    """Keying on the id rather than the name is what keeps this from splitting
    the shop's card sales into a "TCG" era and a "Trading Cards" era."""
    rows = (
        await db.execute(
            text(
                "select id, name from categories "
                "where tenant_id = :tenant and external_id = 'CAT_TCG'"
            ),
            {"tenant": str(tenant.id)},
        )
    ).all()
    assert len(rows) == 1, f"CAT_TCG became {len(rows)} canonical categories"

    current_name = await in_source(source, "select name from categories where id = 'CAT_TCG'")
    assert rows[0].name == current_name, "the category did not follow its rename"

    # And everything that ever hung off it still does.
    ours = await canonical(
        db,
        tenant,
        """
        select count(*) from products p
        join categories c on c.id = p.category_id
        where p.tenant_id = :tenant and c.external_id = 'CAT_TCG'
        """,
    )
    theirs = await in_source(
        source, "select count(*) from catalog_items where category_id = 'CAT_TCG'"
    )
    assert ours == theirs


# ---------------------------------------------------------------------------
# Quirk 5 — duplicate customers sharing an email
# ---------------------------------------------------------------------------


async def test_duplicate_customers_are_merged_never_deleted(
    db: AsyncSession, tenant: t.Tenant, source: AsyncSession
) -> None:
    theirs = await in_source(source, "select count(*) from customers")
    ours = await canonical(db, tenant, "select count(*) from customers where tenant_id = :tenant")
    assert ours == theirs, "merging deleted rows instead of pointing them at a survivor"

    merged = await canonical(
        db,
        tenant,
        "select count(*) from customers where tenant_id = :tenant and merged_into_id is not null",
    )
    duplicate_emails = await in_source(
        source,
        "select coalesce(sum(n - 1), 0) from ("
        "  select count(*) as n from customers where email is not null group by email"
        ") g where n > 1",
    )
    assert merged == duplicate_emails, (
        f"{duplicate_emails} duplicate email records upstream, {merged} merged here"
    )

    # Normalisation is what found them: raw string equality would miss case.
    unnormalised = await canonical(
        db,
        tenant,
        "select count(*) from customers where tenant_id = :tenant "
        "and email is not null and email_normalized is null",
    )
    assert unnormalised == 0


async def test_repeat_customers_count_each_person_once(db: AsyncSession, tenant: t.Tenant) -> None:
    """Following `merged_into_id` is what stops one person counting twice."""
    naive = await canonical(
        db,
        tenant,
        "select count(distinct customer_id) from orders "
        "where tenant_id = :tenant and customer_id is not null",
    )
    deduped = await canonical(
        db,
        tenant,
        """
        select count(distinct coalesce(c.merged_into_id, c.id))
        from orders o join customers c on c.id = o.customer_id
        where o.tenant_id = :tenant
        """,
    )
    assert deduped <= naive
    assert deduped < naive, "merging changed nothing, so the duplicates never placed an order"


# ---------------------------------------------------------------------------
# Quirk 6 and 7 — partial refunds, canceled orders
# ---------------------------------------------------------------------------


async def test_partial_refunds_land_on_their_own_date(
    db: AsyncSession, tenant: t.Tenant, source: AsyncSession
) -> None:
    ours = await canonical(
        db, tenant, "select count(*) from refunds where tenant_id = :tenant and deleted_at is null"
    )
    theirs = await in_source(source, "select count(*) from refunds")
    assert ours == theirs

    partial = await canonical(
        db,
        tenant,
        "select count(*) from refunds r join orders o on o.id = r.order_id "
        "where r.tenant_id = :tenant and r.amount < o.total",
    )
    assert partial > 0, "every refund is for the full order, so partials were flattened"

    later = await canonical(
        db,
        tenant,
        "select count(*) from refunds r join orders o on o.id = r.order_id "
        "where r.tenant_id = :tenant and r.occurred_at > o.placed_at",
    )
    assert later > 0, "refunds all share their order's date, so refund months are wrong"


async def test_canceled_orders_are_kept_but_are_not_revenue(
    db: AsyncSession, tenant: t.Tenant, source: AsyncSession
) -> None:
    ours = await canonical(
        db,
        tenant,
        "select count(*) from orders where tenant_id = :tenant and status = :status",
        status=OrderStatus.CANCELED.value,
    )
    theirs = await in_source(source, "select count(*) from orders where state = 'CANCELED'")
    assert ours == theirs > 0

    # They keep their line items, which is exactly why excluding them matters.
    with_lines = await canonical(
        db,
        tenant,
        "select count(*) from orders o join order_lines l on l.order_id = o.id "
        "where o.tenant_id = :tenant and o.status = :status",
        status=OrderStatus.CANCELED.value,
    )
    assert with_lines > 0


# ---------------------------------------------------------------------------
# Quirk 8 — split payments
# ---------------------------------------------------------------------------


async def test_split_payments_do_not_double_count_an_order(
    db: AsyncSession, tenant: t.Tenant, source: AsyncSession
) -> None:
    """Two tenders, one sale.

    Summing payments instead of orders is the classic way to book a split-paid
    order twice. The canonical model has one order, so the check is that its
    total matches the source's, for exactly those orders.
    """
    split_ids = [
        row.order_id
        for row in (
            await source.execute(
                text("select order_id from payments group by order_id having count(*) > 1")
            )
        ).all()
    ]
    assert split_ids, "the fixture lost its split payments"

    ours = await canonical(
        db,
        tenant,
        "select coalesce(sum(total), 0) from orders "
        "where tenant_id = :tenant and external_id = any(:ids)",
        ids=split_ids,
    )
    theirs = (
        await source.execute(
            text("select coalesce(sum(total_money), 0) / 100.0 from orders where id = any(:ids)"),
            {"ids": split_ids},
        )
    ).scalar_one()
    assert abs(Decimal(ours) - Decimal(theirs)) <= CENT


# ---------------------------------------------------------------------------
# Quirk 9 and 10 — missing costs, register-priced items
# ---------------------------------------------------------------------------


async def test_missing_costs_stay_missing_rather_than_becoming_zero(
    db: AsyncSession, tenant: t.Tenant, source: AsyncSession
) -> None:
    """A null cost is data. A zero cost is a 100% margin that is not real."""
    zero_costs = await canonical(
        db, tenant, "select count(*) from variants where tenant_id = :tenant and cost = 0"
    )
    assert zero_costs == 0, f"{zero_costs} variants have a cost of exactly zero"

    ours = await canonical(
        db,
        tenant,
        "select count(*) from variants where tenant_id = :tenant and cost is null "
        "and deleted_at is null",
    )
    theirs = await in_source(
        source,
        """
        select count(*) from item_variations v
        join catalog_items i on i.id = v.item_id
        left join variation_vendor_info vi on vi.variation_id = v.id
        where vi.unit_cost_amount is null and not i.is_deleted
        """,
    )
    assert ours == theirs, f"canonical says {ours} variants lack a cost, source says {theirs}"


async def test_the_quality_report_states_cost_coverage_instead_of_guessing(
    db: AsyncSession, tenant: t.Tenant
) -> None:
    report = (
        await db.execute(
            select(t.DataQualityReport)
            .where(t.DataQualityReport.tenant_id == tenant.id)
            .order_by(t.DataQualityReport.generated_at.desc())
            .limit(1)
        )
    ).scalar_one()
    coverage = report.metrics["cost_coverage_share"]
    assert coverage is not None
    assert 0.5 < coverage < 1.0, f"cost coverage of {coverage} is not a partial-coverage story"
    assert any(f["code"] == "missing_costs" for f in report.findings), (
        "missing costs were not reported to the shop owner"
    )


async def test_register_priced_items_have_no_catalog_price_but_sold_at_one(
    db: AsyncSession, tenant: t.Tenant, source: AsyncSession
) -> None:
    variable_ids = [
        row.id
        for row in (
            await source.execute(
                text("select id from item_variations where pricing_type = 'VARIABLE'")
            )
        ).all()
    ]
    assert variable_ids, "the fixture lost its register-priced items"

    priced = await canonical(
        db,
        tenant,
        "select count(*) from variants where tenant_id = :tenant and external_id = any(:ids) "
        "and price is not null",
        ids=variable_ids,
    )
    assert priced == 0, f"{priced} register-priced variants came through with a catalog price"

    # The line still knows what was charged, because the line is where it lives.
    sold_for = await canonical(
        db,
        tenant,
        """
        select count(*) from order_lines l
        join variants v on v.id = l.variant_id
        where l.tenant_id = :tenant and v.external_id = any(:ids) and l.unit_price > 0
        """,
        ids=variable_ids,
    )
    assert sold_for > 0, "register-priced items sold, but no line kept the price charged"


# ---------------------------------------------------------------------------
# Quirks 11 and 12 — low stock and dead stock
# ---------------------------------------------------------------------------


async def test_low_and_dead_stock_survive_the_trip(
    db: AsyncSession, tenant: t.Tenant, source: AsyncSession
) -> None:
    ours = await canonical(
        db,
        tenant,
        "select coalesce(sum(on_hand), 0) from inventory_levels "
        "where tenant_id = :tenant and deleted_at is null",
    )
    theirs = await in_source(
        source,
        "select coalesce(sum(quantity::numeric), 0) from inventory_counts where state = 'IN_STOCK'",
    )
    assert Decimal(ours) == Decimal(theirs), "stock on hand does not match the source"

    low = await canonical(
        db,
        tenant,
        "select count(*) from inventory_levels "
        "where tenant_id = :tenant and on_hand between 0 and 3",
    )
    assert low > 0, "nothing is low on stock, so reorder suggestions would have nothing to say"

    dead = await canonical(
        db,
        tenant,
        """
        select count(*) from variants v
        where v.tenant_id = :tenant and v.deleted_at is null
          and not exists (select 1 from order_lines l where l.variant_id = v.id)
        """,
    )
    assert dead > 0, "everything has sold at least once, so dead stock has nothing to find"


# ---------------------------------------------------------------------------
# The signals, not the mess
# ---------------------------------------------------------------------------


async def test_the_con_booth_became_the_event_channel(
    db: AsyncSession, tenant: t.Tenant, source: AsyncSession
) -> None:
    """Normalising a second location into a channel is the whole reason
    "how did the con booth do?" is answerable at all."""
    ours = await canonical(
        db,
        tenant,
        "select count(*) from orders where tenant_id = :tenant and channel = :channel",
        channel=Channel.EVENT.value,
    )
    theirs = await in_source(source, "select count(*) from orders where location_id = 'LOC_CON'")
    assert ours == theirs > 0

    online = await canonical(
        db,
        tenant,
        "select count(*) from orders where tenant_id = :tenant and channel = :channel",
        channel=Channel.ONLINE.value,
    )
    assert online > 0, "the online channel disappeared in translation"


async def test_inventory_history_came_across_with_its_reasons(
    db: AsyncSession, tenant: t.Tenant, source: AsyncSession
) -> None:
    ours = await canonical(
        db,
        tenant,
        "select count(*) from inventory_movements where tenant_id = :tenant and deleted_at is null",
    )
    theirs = await in_source(source, "select count(*) from inventory_adjustments")
    assert ours == theirs

    kinds = {
        row.kind
        for row in (
            await db.execute(
                text("select distinct kind from inventory_movements where tenant_id = :tenant"),
                {"tenant": str(tenant.id)},
            )
        ).all()
    }
    assert {"received", "sold", "damaged"} <= kinds, (
        f"movement kinds collapsed into {kinds}; sell-through needs them apart"
    )
