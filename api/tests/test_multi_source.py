"""A shop whose tills are not all the same brand.

This is the one thing neither Square nor Shopify can ever ship, so it is the one
thing that has to actually hold. Square AI needs Square; Sidekick needs Shopify;
neither will ever add a competitor's revenue to their own. A shop running a till
on the floor and a marketplace online has two dashboards that never meet.

Everything here is built on a tenant with **two integrations and rows stamped
with two different sources**, because until this file existed no such tenant had
ever run through the semantic layer. Both dev shops have exactly one register, so
every multi-source path in the codebase was untested — and two of them were
wrong: the sync CLI, the Data screen and the worker all stopped at the first
integration they found.

The data is written straight into the canonical tables rather than coming from a
fake customer system. That is deliberate and it is the right layer: `app.analytics`
cannot tell where a row came from and must not care, so a test that stood up two
fake POSes would be testing the adapters, which `tests/conformance` already does.

The claim under test, in one line: **consolidated net sales equals the sum of its
per-register parts, to the cent.**
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import (
    DateRange,
    Dimension,
    Filters,
    breakdown,
    load_context,
    sales_summary,
    source_breakdown,
)
from app.analytics.finance import financial_summary
from app.canonical import tables as t
from app.canonical.enums import Channel, OrderStatus, Tender
from app.monthend.packet import build as build_packet

# Two registers: the till on the shop floor, and a marketplace that only ever
# hands over a spreadsheet. A realistic pairing, and the pairing the product
# exists for.
FLOOR = "till_floor"
MARKET = "marketplace_csv"

FLOOR_NAME = "Front counter"
MARKET_NAME = "Marketplace (monthly export)"


@dataclass(frozen=True, slots=True)
class TwoRegisterShop:
    """A shop with two registers and known sales on each."""

    tenant_id: uuid.UUID
    slug: str
    # What each register sold, gross, before any refund.
    gross: dict[str, Decimal]
    refunds: dict[str, Decimal]

    @property
    def net(self) -> Decimal:
        return sum(self.gross.values(), Decimal("0")) - sum(self.refunds.values(), Decimal("0"))


async def _variant(session: AsyncSession, tenant_id: uuid.UUID, source: str) -> uuid.UUID:
    """One sellable thing, owned by one register.

    Each register carries its own catalog rows, which is what really happens: the
    marketplace does not know the till's SKUs. The unique constraint on every
    sourced table is (tenant_id, source, external_id), so both can use SKU-1
    without colliding — a property worth leaning on here.
    """
    product = t.Product(
        tenant_id=tenant_id, source=source, external_id="SKU-1", name=f"Widget ({source})"
    )
    session.add(product)
    await session.flush()
    variant = t.Variant(
        tenant_id=tenant_id,
        source=source,
        external_id="SKU-1",
        product_id=product.id,
        sku="SKU-1",
        price=Decimal("10.00"),
    )
    session.add(variant)
    await session.flush()
    return variant.id


async def _order(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    source: str,
    variant_id: uuid.UUID,
    *,
    external_id: str,
    placed_at: datetime,
    amount: Decimal,
    channel: Channel,
    refund: Decimal | None = None,
    payment: bool = False,
) -> None:
    order = t.Order(
        tenant_id=tenant_id,
        source=source,
        external_id=external_id,
        placed_at=placed_at,
        status=OrderStatus.COMPLETED,
        channel=channel,
        subtotal=amount,
        total=amount,
        discount_total=Decimal("0"),
        tax_total=Decimal("0"),
    )
    session.add(order)
    await session.flush()
    session.add(
        t.OrderLine(
            tenant_id=tenant_id,
            source=source,
            external_id=f"{external_id}-L1",
            order_id=order.id,
            variant_id=variant_id,
            # Not null in the canonical schema, and rightly so: what the line was
            # called when it sold is not recoverable from a variant that has since
            # been renamed.
            name_snapshot=f"Widget ({source})",
            quantity=Decimal("1"),
            unit_price=amount,
            discount=Decimal("0"),
        )
    )
    if payment:
        # Excludes the tip, matching how every POS reports it.
        session.add(
            t.Payment(
                tenant_id=tenant_id,
                source=source,
                external_id=f"{external_id}-P1",
                order_id=order.id,
                amount=amount,
                tip=Decimal("0"),
                tender=Tender.CARD,
                occurred_at=placed_at,
            )
        )
    if refund is not None:
        session.add(
            t.Refund(
                tenant_id=tenant_id,
                source=source,
                external_id=f"{external_id}-R1",
                order_id=order.id,
                occurred_at=placed_at + timedelta(hours=1),
                amount=refund,
            )
        )


@pytest.fixture
async def two_registers(db: AsyncSession) -> AsyncIterator[TwoRegisterShop]:
    """A shop with two live integrations and sales on both.

    Committed rather than left in the fixture's transaction, because
    `load_context` and the metrics read through their own session in some
    callers. Torn down by deleting the tenant, which cascades.
    """
    slug = f"pytest-two-{uuid.uuid4().hex[:8]}"
    tenant = t.Tenant(slug=slug, name="Two Register Shop", timezone="America/New_York")
    db.add(tenant)
    await db.flush()

    # Connected a month apart, and explicitly so. Both rows flushed in one
    # transaction share `now()` to the microsecond, which would leave the slug
    # deciding the order and make any assertion about connection order vacuous.
    # A real shop adds its second register long after its first.
    connected = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    for offset, (source, name, caps) in enumerate(
        (
            # The till knows costs and customers. The marketplace export knows
            # neither — so the union has to turn margin on for the shop while the
            # coverage caveats stay honest about which half of the sales it covers.
            (
                FLOOR,
                FLOOR_NAME,
                {"has_costs": True, "has_customers": True, "has_payments": True},
            ),
            (MARKET, MARKET_NAME, {"has_online_channel": True}),
        )
    ):
        db.add(
            t.Integration(
                tenant_id=tenant.id,
                adapter="pytest",
                source=source,
                display_name=name,
                config={},
                capabilities=caps,
                is_active=True,
                created_at=connected + timedelta(days=30 * offset),
            )
        )
    await db.flush()

    floor_variant = await _variant(db, tenant.id, FLOOR)
    market_variant = await _variant(db, tenant.id, MARKET)

    # Mid-month and mid-day in shop time, so no bucket-edge ambiguity.
    when = datetime(2026, 6, 15, 16, 0, tzinfo=UTC)

    gross = {FLOOR: Decimal("0"), MARKET: Decimal("0")}
    refunds = {FLOOR: Decimal("0"), MARKET: Decimal("0")}

    # Deliberately uneven, and deliberately not round: a split that reconciles
    # for 100.00 + 100.00 can still be hiding a rounding bug.
    floor_sales = [Decimal("12.34"), Decimal("45.67"), Decimal("8.90")]
    market_sales = [Decimal("99.01"), Decimal("23.45")]

    for index, amount in enumerate(floor_sales):
        # One of the till's sales comes back, so refund attribution is exercised
        # on one register and not the other.
        refund = Decimal("5.00") if index == 0 else None
        await _order(
            db,
            tenant.id,
            FLOOR,
            floor_variant,
            external_id=f"F-{index}",
            placed_at=when,
            amount=amount,
            channel=Channel.IN_STORE,
            refund=refund,
            payment=True,
        )
        gross[FLOOR] += amount
        if refund is not None:
            refunds[FLOOR] += refund

    for index, amount in enumerate(market_sales):
        await _order(
            db,
            tenant.id,
            MARKET,
            market_variant,
            external_id=f"M-{index}",
            placed_at=when,
            amount=amount,
            channel=Channel.ONLINE,
        )
        gross[MARKET] += amount

    await db.commit()
    try:
        yield TwoRegisterShop(tenant_id=tenant.id, slug=slug, gross=gross, refunds=refunds)
    finally:
        await db.execute(delete(t.Tenant).where(t.Tenant.id == tenant.id))
        await db.commit()


JUNE = DateRange(datetime(2026, 6, 1).date(), datetime(2026, 6, 30).date())


async def test_the_context_knows_both_registers(
    db: AsyncSession, two_registers: TwoRegisterShop
) -> None:
    ctx = await load_context(db, two_registers.slug)

    assert ctx.has_more_than_one_source
    # Connection order, oldest first — the till was there before the marketplace.
    # Note this is *not* alphabetical, which is what makes it a real assertion.
    assert [ref.source for ref in ctx.sources] == [FLOOR, MARKET]
    assert FLOOR > MARKET, "the fixture would pass on a slug sort if this flipped"
    assert ctx.label_for_source(FLOOR) == FLOOR_NAME
    assert ctx.label_for_source(MARKET) == MARKET_NAME
    # A source we do not have falls back to the slug rather than raising: a row
    # from a disconnected register must still be labellable.
    assert ctx.label_for_source("gone") == "gone"


async def test_capabilities_are_the_union_of_both(
    db: AsyncSession, two_registers: TwoRegisterShop
) -> None:
    """The reason the union exists: one register knowing costs is enough to make
    margin answerable for the shop, even though the other never will."""
    ctx = await load_context(db, two_registers.slug)

    assert ctx.has("has_costs")
    assert ctx.has("has_customers")
    assert ctx.has("has_online_channel")
    # Claimed by neither, so still off. A union that turned everything on would
    # be worse than no union.
    assert not ctx.has("multi_location")
    assert not ctx.has("supports_incremental")


async def test_consolidated_net_sales_equals_the_sum_of_its_registers(
    db: AsyncSession, two_registers: TwoRegisterShop
) -> None:
    """The whole promise, in one assertion.

    A consolidated figure that does not reconcile to its own parts is worse than
    no consolidated figure, because somebody will present it to a bookkeeper.
    """
    ctx = await load_context(db, two_registers.slug)

    summary = await sales_summary(db, ctx, JUNE)
    split = await source_breakdown(db, ctx, JUNE)

    assert len(split.rows) == 2
    assert sum((row.net_sales for row in split.rows), Decimal("0")) == summary.net_sales
    assert split.total_net_sales == summary.net_sales
    assert summary.net_sales == two_registers.net


async def test_each_register_is_attributed_its_own_sales_and_refunds(
    db: AsyncSession, two_registers: TwoRegisterShop
) -> None:
    """Exact, not approximate. A refund belongs to an order and an order came out
    of exactly one system, so unlike a product split this one can subtract them."""
    ctx = await load_context(db, two_registers.slug)
    split = await source_breakdown(db, ctx, JUNE)
    rows = {row.key: row for row in split.rows}

    assert rows[FLOOR].label == FLOOR_NAME
    assert rows[MARKET].label == MARKET_NAME

    for source in (FLOOR, MARKET):
        expected = two_registers.gross[source] - two_registers.refunds[source]
        assert rows[source].net_sales == expected, source
        assert rows[source].gross_sales == two_registers.gross[source], source

    # The marketplace outsold the counter here, so the rows must not be in
    # connection order.
    assert [row.key for row in split.rows] == [MARKET, FLOOR]

    # And it is not carrying the "we cannot attribute refunds" caveat that the
    # product-shaped splits have to.
    assert not any(c.code == "breakdown_before_refunds" for c in split.caveats)


async def test_the_shares_add_up_to_one(db: AsyncSession, two_registers: TwoRegisterShop) -> None:
    """ "38% of your revenue is going through the marketplace" is the sentence the
    screen exists to say, so the percentages have to be trustworthy."""
    ctx = await load_context(db, two_registers.slug)
    split = await source_breakdown(db, ctx, JUNE)

    shares = [row.share_of_net_sales for row in split.rows]
    assert all(share is not None for share in shares)
    total = sum((share for share in shares if share is not None), Decimal("0"))
    # Two rows, each rounded to four places; a cent of rounding is acceptable,
    # a percent is not.
    assert abs(total - Decimal("1")) < Decimal("0.0002"), shares


async def test_filtering_to_one_register_matches_that_register_s_row(
    db: AsyncSession, two_registers: TwoRegisterShop
) -> None:
    """ "How did the booth do?" on a shop whose booth is a different system."""
    ctx = await load_context(db, two_registers.slug)
    split = await source_breakdown(db, ctx, JUNE)
    rows = {row.key: row for row in split.rows}

    for source in (FLOOR, MARKET):
        only = await sales_summary(db, ctx, JUNE, Filters(sources=(source,)))
        assert only.net_sales == rows[source].net_sales, source

    # And both together are the whole shop again.
    both = await sales_summary(db, ctx, JUNE, Filters(sources=(FLOOR, MARKET)))
    assert both.net_sales == two_registers.net


async def test_a_source_filter_for_a_register_we_do_not_have_returns_nothing(
    db: AsyncSession, two_registers: TwoRegisterShop
) -> None:
    """Not an error, and emphatically not the whole shop. A filter that silently
    stopped applying would report every register's takings as one register's."""
    ctx = await load_context(db, two_registers.slug)
    summary = await sales_summary(db, ctx, JUNE, Filters(sources=("nope",)))
    assert summary.net_sales == Decimal("0")


async def test_the_other_dimensions_still_cut_across_both_registers(
    db: AsyncSession, two_registers: TwoRegisterShop
) -> None:
    """Consolidation is not a separate report: every existing breakdown should now
    be answering across both systems without knowing it."""
    ctx = await load_context(db, two_registers.slug)

    by_channel = await breakdown(db, ctx, JUNE, Dimension.CHANNEL)
    channels = {row.key: row.net_sales for row in by_channel.rows}
    # In-store came from the till, online from the marketplace — so a channel
    # split on a two-register shop is implicitly a cross-system answer.
    assert channels["in_store"] == two_registers.gross[FLOOR] - two_registers.refunds[FLOOR]
    assert channels["online"] == two_registers.gross[MARKET]

    by_product = await breakdown(db, ctx, JUNE, Dimension.PRODUCT)
    assert len(by_product.rows) == 2, "each register carries its own catalog row"


async def test_both_registers_can_use_the_same_external_id(
    db: AsyncSession, two_registers: TwoRegisterShop
) -> None:
    """A marketplace does not know the till's SKUs, and both call something SKU-1.

    Row identity is (tenant_id, source, external_id), so this has to be fine — and
    if it ever stops being fine, consolidation becomes impossible rather than
    merely wrong.
    """
    skus = (
        await db.execute(
            select(t.Variant.source, t.Variant.external_id).where(
                t.Variant.tenant_id == two_registers.tenant_id
            )
        )
    ).all()
    assert sorted(skus) == sorted([(FLOOR, "SKU-1"), (MARKET, "SKU-1")])


async def test_a_deactivated_register_leaves_the_context_but_not_the_history(
    db: AsyncSession, two_registers: TwoRegisterShop
) -> None:
    """Disconnecting a till is not deleting last year.

    `load_context` reads active integrations, so the register stops being listed —
    but its sales stay in the totals, because they happened. The breakdown falls
    back to the slug for the label, which is why that fallback exists.
    """
    await db.execute(
        text(
            "update integrations set is_active = false "
            "where tenant_id = :tenant and source = :source"
        ),
        {"tenant": two_registers.tenant_id, "source": MARKET},
    )
    await db.commit()

    ctx = await load_context(db, two_registers.slug)
    assert [ref.source for ref in ctx.sources] == [FLOOR]
    assert not ctx.has_more_than_one_source

    summary = await sales_summary(db, ctx, JUNE)
    assert summary.net_sales == two_registers.net, "history does not disappear"

    split = await source_breakdown(db, ctx, JUNE)
    rows = {row.key: row for row in split.rows}
    assert set(rows) == {FLOOR, MARKET}
    assert rows[MARKET].label == MARKET_NAME, "the integration row is still there, just inactive"


# --------------------------------------------------------------------------
# The month-end packet
#
# The plan's own open question was whether the packet survives two integrations.
# It did not: `ctx.has("has_payments")` is a union, so a shop with a till that
# reports payments and a marketplace export that does not produced takings
# covering half the shop, reconciled against net sales covering all of it. The
# packet told a bookkeeper their books were short by the entire marketplace.
# --------------------------------------------------------------------------


async def test_takings_are_scoped_to_the_registers_that_report_them(
    db: AsyncSession, two_registers: TwoRegisterShop
) -> None:
    ctx = await load_context(db, two_registers.slug)
    finance = await financial_summary(db, ctx, JUNE)

    # The union says yes, because of the till alone.
    assert ctx.has("has_payments")
    # But only one register can actually answer, and the summary says which.
    assert finance.payment_sources == (FLOOR,)
    assert not finance.payments_cover_everything
    assert ctx.sources_with("has_payments") == (FLOOR,)
    assert not ctx.covers_every_source("has_payments")

    # Takings are the till's, not the shop's.
    assert finance.payments_total == two_registers.gross[FLOOR]


async def test_the_packet_reconciles_on_a_two_register_shop(
    db: AsyncSession, two_registers: TwoRegisterShop
) -> None:
    """The check that would have accused a balanced shop of being short."""
    ctx = await load_context(db, two_registers.slug)
    packet = await build_packet(db, ctx, JUNE)

    takings = next(c for c in packet.checks.checks if c.name == "Takings match sales")
    assert takings.ok, (
        f"takings {takings.left} vs sales {takings.right}, gap {takings.gap}. "
        "A register that does not report payments must not be counted against the "
        "takings of one that does."
    )

    # And the comparison really was against the till's sales, not the shop's.
    assert packet.takings_net_sales == two_registers.gross[FLOOR] - two_registers.refunds[FLOOR]
    assert packet.takings_net_sales != packet.summary.net_sales

    # The gap that the old arithmetic would have reported, spelled out: the whole
    # marketplace. This is the assertion that fails if somebody "simplifies"
    # `_reconcile` back to comparing against the shop total.
    would_have_been_short_by = packet.summary.net_sales - packet.takings_net_sales
    assert would_have_been_short_by == two_registers.gross[MARKET]

    # The internal check is unaffected either way: it is the packet agreeing with
    # itself about its own headline, and that holds however many registers there are.
    adds_up = next(c for c in packet.checks.checks if c.name == "Net sales adds up")
    assert adds_up.ok


async def test_the_packet_carries_the_per_register_split(
    db: AsyncSession, two_registers: TwoRegisterShop
) -> None:
    """The section a bookkeeper would otherwise assemble by hand from two portals."""
    ctx = await load_context(db, two_registers.slug)
    packet = await build_packet(db, ctx, JUNE)

    assert packet.by_source is not None
    rows = {row.key: row for row in packet.by_source.rows}
    assert set(rows) == {FLOOR, MARKET}
    assert rows[FLOOR].label == FLOOR_NAME

    # It has to reconcile to the packet's own headline, or the section is a
    # second opinion rather than a breakdown.
    assert sum((row.net_sales for row in packet.by_source.rows), Decimal("0")) == (
        packet.summary.net_sales
    )

    # And it survives the trip through `figures()`, which is what the PDF and the
    # spreadsheet are built from.
    figures = packet.figures()
    assert figures["by_source"] is not None
    assert len(figures["by_source"]) == 2


async def test_a_single_register_shop_still_gets_a_source_section(
    db: AsyncSession, two_registers: TwoRegisterShop
) -> None:
    """One row, equal to net sales. Why the section needs no capability gate."""
    await db.execute(
        text(
            "update integrations set is_active = false "
            "where tenant_id = :tenant and source = :source"
        ),
        {"tenant": two_registers.tenant_id, "source": MARKET},
    )
    await db.execute(
        text("delete from orders where tenant_id = :tenant and source = :source"),
        {"tenant": two_registers.tenant_id, "source": MARKET},
    )
    await db.commit()

    ctx = await load_context(db, two_registers.slug)
    assert not ctx.has_more_than_one_source

    packet = await build_packet(db, ctx, JUNE)
    assert packet.by_source is not None
    assert len(packet.by_source.rows) == 1
    assert packet.by_source.rows[0].net_sales == packet.summary.net_sales
    # Takings now cover everything, so no subset comparison is needed.
    assert packet.takings_net_sales is None
