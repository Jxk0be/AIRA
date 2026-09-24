"""The agent loop, driven by a scripted model.

What is checked here is the machinery around the model rather than the model:
that a tool call actually runs and its result goes back, that a chart built from
invented numbers is refused, that every run is written down with what it cost,
and that a shop is never handed a tool its system cannot answer.

Everything runs inside the test transaction and against a fake client, so no
request leaves the machine and nothing is billed.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.assistant import ClaudeAssistant, RunUsage, price_of
from app.agent.context import ShopContext, build_context
from app.agent.events import AgentEvent, EventType
from app.canonical import tables as t
from app.canonical.enums import MessageRole
from app.config import get_settings
from tests.fake_model import FakeAnthropic, message, text_block, tool_block


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
    return await shop_named(db, "tsundoku")


@pytest.fixture
async def spreadsheet(db: AsyncSession) -> ShopContext:
    return await shop_named(db, "panel_and_pawn")


def assistant_with(client: FakeAnthropic) -> ClaudeAssistant:
    return ClaudeAssistant(settings=get_settings(), client=client)  # type: ignore[arg-type]


async def collect(
    assistant: ClaudeAssistant, db: AsyncSession, shop: ShopContext, question: str, **kwargs: object
) -> list[AgentEvent]:
    return [event async for event in assistant.stream(db, shop, question, **kwargs)]  # type: ignore[arg-type]


def of_type(events: list[AgentEvent], kind: EventType) -> list[AgentEvent]:
    return [event for event in events if event.type is kind]


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


async def test_a_question_becomes_a_tool_call_and_an_answer(
    db: AsyncSession, pos: ShopContext
) -> None:
    """The whole shape of a turn, from question to `done`."""
    client = FakeAnthropic.scripted(
        message(
            [tool_block("sales_summary", {"start_date": "2025-12-01", "end_date": "2025-12-31"})],
            stop_reason="tool_use",
        ),
        message([text_block("December was your best month.")]),
    )
    events = await collect(assistant_with(client), db, pos, "How did December go?")

    kinds = [event.type for event in events]
    assert EventType.TOOL_START in kinds
    assert EventType.TOOL_END in kinds
    assert kinds[-1] is EventType.DONE

    start = of_type(events, EventType.TOOL_START)[0]
    assert start.data["tool"] == "sales_summary"
    end = of_type(events, EventType.TOOL_END)[0]
    assert end.data["error"] is None

    spoken = "".join(event.data["text"] for event in of_type(events, EventType.TOKEN))
    assert "December was your best month." in spoken


async def test_the_tool_result_actually_reaches_the_model(
    db: AsyncSession, pos: ShopContext
) -> None:
    """The point of the loop.

    A tool that runs and whose answer never makes it into the next request is
    the failure mode that looks exactly like a model that ignores its tools.
    """
    client = FakeAnthropic.scripted(
        message([tool_block("sales_summary", {})], stop_reason="tool_use"),
        message([text_block("Net sales were as reported.")]),
    )
    await collect(assistant_with(client), db, pos, "How are sales?")

    second_request = client.stream_calls[1]
    sent_back = second_request["messages"][-1]
    assert sent_back["role"] == "user"
    result = sent_back["content"][0]
    assert result["type"] == "tool_result"
    assert result["is_error"] is False
    assert "net_sales" in result["content"]


async def test_a_shop_is_only_offered_the_tools_it_can_answer_with(
    db: AsyncSession, spreadsheet: ShopContext
) -> None:
    """Gating happens where the request is built, not where the tool runs."""
    client = FakeAnthropic.scripted(message([text_block("Here you go.")]))
    await collect(assistant_with(client), db, spreadsheet, "How many customers come back?")

    offered = {tool["name"] for tool in client.stream_calls[0]["tools"]}
    assert "customer_stats" not in offered
    assert "sales_summary" in offered


async def test_the_briefing_is_this_shops_and_is_cached(db: AsyncSession, pos: ShopContext) -> None:
    """The shop's own name and dates go in the system prompt, and the cache
    breakpoint sits at the end of it so the tools are cached with it."""
    client = FakeAnthropic.scripted(message([text_block("Hello.")]))
    await collect(assistant_with(client), db, pos, "Hello")

    system = client.stream_calls[0]["system"]
    assert pos.name in system[-1]["text"]
    assert system[-1]["cache_control"] == {"type": "ephemeral"}


async def test_a_tool_that_fails_is_reported_and_the_turn_continues(
    db: AsyncSession, pos: ShopContext
) -> None:
    """A bad argument should cost one round trip, not the answer."""
    client = FakeAnthropic.scripted(
        message(
            [tool_block("sales_summary", {"category": "Vinyl Records"})], stop_reason="tool_use"
        ),
        message([text_block("You do not have a vinyl category.")]),
    )
    events = await collect(assistant_with(client), db, pos, "How are vinyl sales?")

    end = of_type(events, EventType.TOOL_END)[0]
    assert end.data["error"] is not None
    assert of_type(events, EventType.DONE)

    result = client.stream_calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True
    assert "Manga" in result["content"], "the model was not told which categories exist"


async def test_a_tool_the_shop_cannot_support_answers_in_words(
    db: AsyncSession, spreadsheet: ShopContext
) -> None:
    """`CapabilityUnavailable` is an answer, not an error: the model is meant
    to relay it, so it must not come back flagged as a tool failure."""
    client = FakeAnthropic.scripted(
        message(
            [tool_block("location_channel_breakdown", {"by": "location"})], stop_reason="tool_use"
        ),
        message([text_block("You only have the one shop.")]),
    )
    events = await collect(assistant_with(client), db, spreadsheet, "How did each shop do?")

    end = of_type(events, EventType.TOOL_END)[0]
    assert end.data["error"] is None
    result = client.stream_calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] is False
    assert "single location" in result["content"]


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


def chart_the_last_result(request: dict[str, object]) -> object:
    """Chart whatever the previous tool actually returned.

    Scripted as a function of the request rather than fixed rows, because the
    whole point is that the numbers are the tool's own — hardcoding them here
    would test the fixture instead of the loop.
    """
    messages = request["messages"]
    result = json.loads(messages[-1]["content"][0]["content"])  # type: ignore[index]
    rows = [{"label": row["label"], "net_sales": row["net_sales"]} for row in result["rows"][:3]]
    return message(
        [
            tool_block(
                "make_chart",
                {
                    "type": "bar",
                    "title": "Sales by category",
                    "x": "label",
                    "y": ["net_sales"],
                    "data": rows,
                },
                call_id="toolu_2",
            )
        ],
        stop_reason="tool_use",
    )


async def test_a_chart_built_from_real_numbers_is_emitted(
    db: AsyncSession, pos: ShopContext
) -> None:
    client = FakeAnthropic.scripted(
        message([tool_block("category_breakdown", {"limit": 3})], stop_reason="tool_use"),
        chart_the_last_result,
        message([text_block("Here is the split.")]),
    )
    events = await collect(assistant_with(client), db, pos, "Show me category sales")

    charts = of_type(events, EventType.CHART)
    assert len(charts) == 1
    assert charts[0].data["title"] == "Sales by category"
    assert charts[0].data["type"] == "bar"
    assert len(charts[0].data["data"]) == 3

    chart_end = [
        event for event in of_type(events, EventType.TOOL_END) if event.data["tool"] == "make_chart"
    ]
    assert chart_end[0].data["error"] is None

    # And it is kept with the message, so pinning it later is possible.
    stored = (
        await db.execute(
            select(t.Message)
            .where(t.Message.tenant_id == pos.tenant_id, t.Message.role == MessageRole.ASSISTANT)
            .order_by(t.Message.created_at.desc())
            .limit(1)
        )
    ).scalar_one()
    assert stored.charts and stored.charts[0]["title"] == "Sales by category"


async def test_a_chart_with_an_invented_number_is_refused(
    db: AsyncSession, pos: ShopContext
) -> None:
    """The check the whole chart mechanism exists for.

    The model calls a real tool, then charts a number that tool never returned.
    No chart event may reach the client, and the model has to be told why.
    """
    client = FakeAnthropic.scripted(
        message([tool_block("sales_summary", {})], stop_reason="tool_use"),
        message(
            [
                tool_block(
                    "make_chart",
                    {
                        "type": "bar",
                        "title": "Sales",
                        "x": "period_end",
                        "y": ["net_sales"],
                        "data": [{"period_end": "2026-12-31", "net_sales": "999999.00"}],
                    },
                    call_id="toolu_2",
                )
            ],
            stop_reason="tool_use",
        ),
        message([text_block("Sorry, I cannot chart that.")]),
    )
    events = await collect(assistant_with(client), db, pos, "Chart my sales")

    assert not of_type(events, EventType.CHART), "an invented chart reached the client"
    chart_end = [
        event for event in of_type(events, EventType.TOOL_END) if event.data["tool"] == "make_chart"
    ]
    assert chart_end and chart_end[0].data["error"] is not None
    told = client.stream_calls[2]["messages"][-1]["content"][0]
    assert "did not come from any tool" in told["content"]


# ---------------------------------------------------------------------------
# What it cost, and what was kept
# ---------------------------------------------------------------------------


def test_a_run_is_priced_from_the_published_rates() -> None:
    """Cached tokens are a tenth of the price and written ones a quarter more,
    so folding them into the input count would misprice every conversation."""
    usage = RunUsage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    sonnet = price_of("claude-sonnet-5")
    assert sonnet is not None
    expected = sonnet.input + sonnet.output + sonnet.cache_read + sonnet.cache_write

    assert usage.cost("claude-sonnet-5") == expected.quantize(Decimal("0.000001"))
    # A dated snapshot bills at its family's rate rather than silently at zero.
    assert usage.cost("claude-haiku-4-5-20251001") > 0
    assert usage.cost("some-model-we-have-no-price-for") == Decimal("0")


async def test_every_run_is_written_down(db: AsyncSession, pos: ShopContext) -> None:
    """A question with no recorded cost is a question nobody can price."""
    client = FakeAnthropic.scripted(
        message([tool_block("sales_summary", {})], stop_reason="tool_use"),
        message(
            [text_block("Steady.")],
            input_tokens=2_000,
            output_tokens=300,
            cache_read=1_500,
            cache_write=800,
        ),
    )
    events = await collect(assistant_with(client), db, pos, "How are sales?")
    done = of_type(events, EventType.DONE)[0]

    run = (
        await db.execute(
            select(t.AgentRun)
            .where(t.AgentRun.tenant_id == pos.tenant_id)
            .order_by(t.AgentRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one()

    assert run.question == "How are sales?"
    assert run.status == "ok"
    assert run.cost_usd > 0
    assert run.cache_read_tokens == 1_500
    assert [call["tool"] for call in run.tool_calls] == ["sales_summary"]
    assert run.tool_calls[0]["args"] == {}
    assert done.data["cost_usd"] == str(run.cost_usd)


async def test_the_conversation_is_kept_and_can_be_continued(
    db: AsyncSession, pos: ShopContext
) -> None:
    client = FakeAnthropic.scripted(
        message([text_block("It was a good month.")]), title="How December went"
    )
    events = await collect(assistant_with(client), db, pos, "How did December go?")
    done = of_type(events, EventType.DONE)[0]
    conversation_id = uuid.UUID(done.data["conversation_id"])

    rows = (
        (
            await db.execute(
                select(t.Message)
                .where(t.Message.conversation_id == conversation_id)
                .order_by(t.Message.created_at, t.Message.id)
            )
        )
        .scalars()
        .all()
    )
    assert [row.role for row in rows] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert rows[0].content == "How did December go?"

    conversation = (
        await db.execute(select(t.Conversation).where(t.Conversation.id == conversation_id))
    ).scalar_one()
    assert conversation.title == "How December went"
    assert conversation.tenant_id == pos.tenant_id

    # A second turn replays what was said, so the model has the thread.
    again = FakeAnthropic.scripted(message([text_block("Better than November.")]))
    await collect(
        assistant_with(again), db, pos, "And compared to November?", conversation_id=conversation_id
    )
    replayed = again.stream_calls[0]["messages"]
    assert [entry["role"] for entry in replayed] == ["user", "assistant", "user"]
    assert replayed[0]["content"] == "How did December go?"
