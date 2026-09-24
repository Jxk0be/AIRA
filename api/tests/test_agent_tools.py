"""The tools, without a model in the loop.

Two things are checked here and neither is about how well the assistant writes.
The first is that no tool can be made to return another shop's data — there is
no argument for it, and these run every tool against both shops to prove it.
The second is that a shop only gets the tools its system can support, because a
tool the model can see is a tool the model will call.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import ShopContext, build_context
from app.agent.tools import (
    ALL_TOOLS,
    DeadStockArgs,
    LowStockArgs,
    Period,
    RankedArgs,
    SalesArgs,
    SearchArgs,
    SeriesArgs,
    SplitArgs,
    StockArgs,
    ToolContext,
    ToolError,
    resolve_period,
    tools_for,
)
from app.analytics import CapabilityUnavailable
from app.canonical import tables as t
from tests.fake_embedder import FakeEmbedder

POS_SHOP = "tsundoku"
SPREADSHEET_SHOP = "panel_and_pawn"

# Arguments that exercise each tool without depending on the fixture's contents.
CALLS: dict[str, dict[str, object]] = {
    "search_catalog": {"query": "card sleeves", "limit": 3},
    "sales_summary": {},
    "sales_over_time": {"grain": "month"},
    "top_products": {"limit": 5},
    "category_breakdown": {"limit": 5},
    "location_channel_breakdown": {"by": "channel"},
    "low_stock": {"limit": 5},
    "dead_stock": {"limit": 5},
    "sell_through": {"limit": 5},
    "inventory_value": {},
    "margin_report": {},
    "customer_stats": {},
}


async def shop_named(db: AsyncSession, slug: str) -> ShopContext:
    tenant = (await db.execute(select(t.Tenant).where(t.Tenant.slug == slug))).scalar_one_or_none()
    if tenant is None:
        pytest.skip(f"{slug} does not exist; run `python tasks.py backfill {slug}`")
    orders = (
        await db.execute(
            select(func.count()).select_from(t.Order).where(t.Order.tenant_id == tenant.id)
        )
    ).scalar_one()
    if not orders:
        pytest.skip(f"{slug} has not been synced; run `python tasks.py backfill {slug}`")
    return await build_context(db, slug)


@pytest.fixture
async def pos(db: AsyncSession) -> ShopContext:
    return await shop_named(db, POS_SHOP)


@pytest.fixture
async def spreadsheet(db: AsyncSession) -> ShopContext:
    return await shop_named(db, SPREADSHEET_SHOP)


def context_for(db: AsyncSession, shop: ShopContext) -> ToolContext:
    return ToolContext(session=db, shop=shop, embedder=FakeEmbedder())


async def product_names(db: AsyncSession, shop: ShopContext) -> set[str]:
    return {
        name
        for (name,) in (
            await db.execute(select(t.Product.name).where(t.Product.tenant_id == shop.tenant_id))
        ).all()
        if name
    }


def strings_in(value: object) -> set[str]:
    """Every whole string value in a tool result, at any depth."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return set().union(*(strings_in(item) for item in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(strings_in(item) for item in value)) if value else set()
    return set()


# ---------------------------------------------------------------------------
# The briefing
# ---------------------------------------------------------------------------


async def test_the_briefing_is_built_from_the_shop_not_from_a_constant(
    pos: ShopContext, spreadsheet: ShopContext
) -> None:
    assert pos.name == "Tsundoku & Tabletop"
    assert len(pos.locations) > 1
    assert pos.first_sale is not None and pos.last_sale is not None
    assert pos.first_sale < pos.last_sale

    assert len(spreadsheet.locations) == 1
    assert spreadsheet.analytics.capabilities.has_customers is False
    # The last sync noticed things worth saying out loud.
    assert pos.caveats


# ---------------------------------------------------------------------------
# Capability gating
# ---------------------------------------------------------------------------


async def test_a_shop_is_only_given_tools_its_system_can_answer(
    pos: ShopContext, spreadsheet: ShopContext
) -> None:
    """Panel & Pawn's export has no customer column.

    Registering `customer_stats` anyway would mean the model calls it and
    apologises, every time, instead of answering the question it was asked.
    """
    theirs = {tool.name for tool in tools_for(spreadsheet)}
    ours = {tool.name for tool in tools_for(pos)}

    assert "customer_stats" not in theirs
    assert "customer_stats" in ours
    # Everything that does not depend on a missing capability is still there.
    assert {"sales_summary", "top_products", "low_stock", "make_chart"} <= theirs


async def test_every_registered_tool_has_a_description_and_a_schema(
    pos: ShopContext,
) -> None:
    """These strings are the model's only documentation."""
    for tool in tools_for(pos):
        param = tool.as_param()
        assert len(param["description"]) > 60, tool.name
        assert param["input_schema"]["type"] == "object"


async def test_a_capability_the_shop_lacks_refuses_in_the_owners_words(
    db: AsyncSession, spreadsheet: ShopContext
) -> None:
    """A single-location shop asked for a location split gets a sentence, not a
    chart with one bar and not a zero."""
    ctx = context_for(db, spreadsheet)
    split = next(tool for tool in ALL_TOOLS if tool.name == "location_channel_breakdown")

    with pytest.raises(CapabilityUnavailable) as raised:
        await split.call(ctx, {"by": "location"})

    assert spreadsheet.name in raised.value.message


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


async def test_no_tool_can_be_made_to_return_another_shops_data(
    db: AsyncSession, pos: ShopContext, spreadsheet: ShopContext
) -> None:
    """Every tool, both ways round.

    `tenant_id` is not an argument, so there is nothing a model could put in a
    call to cross the line — but a missing filter in one query would show a
    comic shop's board games to an anime shop, and this is where that surfaces.
    """
    for shop, other in ((pos, spreadsheet), (spreadsheet, pos)):
        mine = await product_names(db, shop)
        theirs = await product_names(db, other)
        # Both shops sell card sleeves. Only a name that is theirs alone proves
        # anything, and the comparison is on whole values rather than
        # substrings — "Card Sleeves, 100ct" is inside "Matte Card Sleeves,
        # 100ct", and that is a coincidence, not a leak.
        only_theirs = theirs - mine
        assert only_theirs, "the two shops share every product name; this proves nothing"

        ctx = context_for(db, shop)
        for tool in tools_for(shop):
            if tool.name == "make_chart":
                continue
            try:
                result = await tool.call(ctx, CALLS[tool.name])
            except CapabilityUnavailable:
                continue
            leaked = sorted(only_theirs & strings_in(result))
            assert not leaked, f"{tool.name} returned {other.slug}'s products: {leaked[:3]}"


async def test_a_tool_result_is_small_enough_to_put_in_a_prompt(
    db: AsyncSession, pos: ShopContext
) -> None:
    """Tool results are re-read on every later turn of the conversation, so a
    fat one is a bill that keeps arriving."""
    ctx = context_for(db, pos)
    for tool in tools_for(pos):
        if tool.name == "make_chart":
            continue
        result = await tool.call(ctx, CALLS[tool.name])
        size = len(json.dumps(result, default=str))
        assert size < 12_000, f"{tool.name} returned {size:,} characters"


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


async def test_dates_default_to_the_last_thirty_shop_days(
    db: AsyncSession, pos: ShopContext
) -> None:
    ctx = context_for(db, pos)
    period = resolve_period(ctx, Period())

    assert period.days == 30
    assert period.end == pos.today


async def test_one_date_is_enough(db: AsyncSession, pos: ShopContext) -> None:
    """ "Since March" and "up to the end of June" are both things people say."""
    ctx = context_for(db, pos)
    from datetime import date

    ending = resolve_period(ctx, Period(end_date=date(2026, 6, 30)))
    assert ending.end == date(2026, 6, 30)
    assert ending.days == 30

    starting = resolve_period(ctx, Period(start_date=date(2026, 6, 1)))
    assert starting.start == date(2026, 6, 1)
    assert starting.end == pos.today


async def test_a_backwards_range_is_refused_with_a_usable_message(
    db: AsyncSession, pos: ShopContext
) -> None:
    from datetime import date

    ctx = context_for(db, pos)
    with pytest.raises(ToolError, match="before"):
        resolve_period(ctx, Period(start_date=date(2026, 6, 30), end_date=date(2026, 6, 1)))


async def test_a_name_the_shop_does_not_use_comes_back_with_the_ones_it_does(
    db: AsyncSession, pos: ShopContext
) -> None:
    """The model has never seen this shop's categories. Listing them beats
    refusing, because the next call gets it right."""
    ctx = context_for(db, pos)
    summary = next(tool for tool in ALL_TOOLS if tool.name == "sales_summary")

    with pytest.raises(ToolError) as raised:
        await summary.call(ctx, {"category": "Vinyl Records"})
    assert "Manga" in str(raised.value)

    with pytest.raises(ToolError) as location_error:
        await summary.call(ctx, {"location": "Nashville"})
    assert "Con Booth" in str(location_error.value)


async def test_a_partial_category_name_is_resolved(db: AsyncSession, pos: ShopContext) -> None:
    """Somebody asking about "sealed" means Sealed Product."""
    ctx = context_for(db, pos)
    summary = next(tool for tool in ALL_TOOLS if tool.name == "sales_summary")

    result = await summary.call(ctx, {"category": "sealed"})
    assert result["net_sales"] is not None


async def test_bad_arguments_come_back_as_something_the_model_can_fix(
    db: AsyncSession, pos: ShopContext
) -> None:
    ctx = context_for(db, pos)
    summary = next(tool for tool in ALL_TOOLS if tool.name == "sales_summary")

    with pytest.raises(ToolError, match="not valid"):
        await summary.call(ctx, {"start_date": "the first of December"})


async def test_arguments_the_model_invented_are_rejected(
    db: AsyncSession, pos: ShopContext
) -> None:
    """`extra="forbid"` on every argument model.

    A tool silently ignoring `tenant_id` or `limit_rows` would answer a
    different question than the one asked, and look like it had complied.
    """
    ctx = context_for(db, pos)
    summary = next(tool for tool in ALL_TOOLS if tool.name == "sales_summary")

    with pytest.raises(ToolError, match="not valid"):
        await summary.call(ctx, {"tenant_id": "00000000-0000-0000-0000-000000000000"})


def test_every_argument_field_tells_the_model_what_it_is() -> None:
    """A schema without descriptions is a schema the model guesses at."""
    for args in (
        SalesArgs,
        RankedArgs,
        SeriesArgs,
        SearchArgs,
        StockArgs,
        LowStockArgs,
        DeadStockArgs,
        SplitArgs,
    ):
        schema = args.model_json_schema()
        undocumented = [
            name
            for name, field in schema["properties"].items()
            if "description" not in field and name not in {"limit"}
        ]
        assert not undocumented, f"{args.__name__}: {undocumented}"
