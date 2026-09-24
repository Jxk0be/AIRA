"""The agent loop, behind an interface of our own.

`Assistant` is the whole surface the rest of the product sees: give it a
question and a shop, get a stream of events. `ClaudeAssistant` is the one
implementation, and everything vendor-shaped — the message format, prompt
caching, tool blocks, usage accounting — stops here.

The loop itself is the plain one: ask, run whatever tools come back, ask again
with the results, until the model answers without calling a tool. It is written
out rather than delegated to a framework because the three things that matter
most are all in the seams — a cache breakpoint that has to sit after the tools,
per-call timings and token counts for `agent_runs`, and a chart validator that
needs every tool result from this turn. Each of those is a line here and an
argument with an abstraction otherwise.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from anthropic import APIError, AsyncAnthropic
from anthropic.types import MessageParam, ToolResultBlockParam
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import events
from app.agent.context import ShopContext
from app.agent.events import AgentEvent
from app.agent.prompts import TITLE_PROMPT, system_blocks
from app.agent.tools import ToolContext, ToolError, tools_for
from app.analytics import CapabilityUnavailable
from app.canonical import tables as t
from app.canonical.enums import MessageRole
from app.config import Settings, get_settings
from app.rag.embeddings import EmbeddingError, shared_embedder

log = logging.getLogger(__name__)

# How many times round the loop before we stop. A real question takes one or two
# tool rounds; anything past this is the model stuck in a circle, and the cost of
# letting it keep going is real money.
MAX_STEPS = 8

# Generous: adaptive thinking spends from the same budget, and a truncated
# answer costs a whole retry.
MAX_TOKENS = 16_000

# Turns of history replayed to the model. Enough for a conversation to hold
# context, bounded so an afternoon of chat does not quietly get expensive.
HISTORY_TURNS = 20

# Tool results are already trimmed by the tools, but a runaway one would be
# paid for on every later turn of the conversation.
MAX_RESULT_CHARS = 20_000


@dataclass(frozen=True, slots=True)
class Price:
    """Dollars per million tokens."""

    input: Decimal
    output: Decimal

    @property
    def cache_write(self) -> Decimal:
        # Writing to the cache costs 1.25x the input rate.
        return self.input * Decimal("1.25")

    @property
    def cache_read(self) -> Decimal:
        # Reading from it costs a tenth.
        return self.input * Decimal("0.1")


# Keyed by model-id prefix so a dated snapshot bills at its family's rate.
PRICES: dict[str, Price] = {
    "claude-sonnet-5": Price(Decimal("2"), Decimal("10")),
    "claude-haiku-4-5": Price(Decimal("1"), Decimal("5")),
    "claude-opus-5": Price(Decimal("5"), Decimal("25")),
}
MILLION = Decimal("1000000")


def price_of(model: str) -> Price | None:
    for prefix, price in PRICES.items():
        if model.startswith(prefix):
            return price
    return None


@dataclass
class RunUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, usage: Any) -> None:
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.cache_read_tokens += usage.cache_read_input_tokens or 0
        self.cache_write_tokens += usage.cache_creation_input_tokens or 0

    def cost(self, model: str) -> Decimal:
        """What this run cost, in dollars.

        Returns zero for a model we have no price for rather than guessing: a
        made-up cost in a billing table is worse than a missing one.
        """
        price = price_of(model)
        if price is None:
            log.warning("no price for model %s; recording cost 0", model)
            return Decimal("0")
        total = (
            self.input_tokens * price.input
            + self.output_tokens * price.output
            + self.cache_read_tokens * price.cache_read
            + self.cache_write_tokens * price.cache_write
        )
        return (total / MILLION).quantize(Decimal("0.000001"))


@dataclass
class ToolTrace:
    name: str
    args: dict[str, Any]
    ms: int
    error: str | None = None
    # What the tool said, for the UI's tool chip and for anything checking that
    # the answer only names things a tool actually returned. Not persisted:
    # `agent_runs` keeps the call, not its output.
    result: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"tool": self.name, "args": self.args, "ms": self.ms, "error": self.error}


@dataclass
class RunRecord:
    """What happened, for `agent_runs` and for the `done` event."""

    question: str
    model: str
    started_at: datetime
    usage: RunUsage = field(default_factory=RunUsage)
    tools: list[ToolTrace] = field(default_factory=list)
    answer: str = ""
    charts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    conversation_id: uuid.UUID | None = None
    title: str | None = None

    @property
    def latency_ms(self) -> int:
        return int((datetime.now(tz=UTC) - self.started_at).total_seconds() * 1000)

    def done_payload(self) -> dict[str, Any]:
        return {
            "conversation_id": str(self.conversation_id) if self.conversation_id else None,
            "title": self.title,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "cache_read_tokens": self.usage.cache_read_tokens,
            "cost_usd": str(self.usage.cost(self.model)),
            "tools": [trace.name for trace in self.tools],
        }


class AssistantRefused(RuntimeError):
    """The model declined the request outright."""


class Assistant(Protocol):
    """The only thing the API and the dashboard know about the agent."""

    def stream(
        self,
        session: AsyncSession,
        shop: ShopContext,
        question: str,
        conversation_id: uuid.UUID | None = None,
    ) -> AsyncIterator[AgentEvent]: ...


async def load_history(
    session: AsyncSession, conversation_id: uuid.UUID | None, limit: int = HISTORY_TURNS
) -> list[MessageParam]:
    """Earlier turns, as plain text.

    Tool calls and their results are persisted for the UI but not replayed: the
    model does not need December's numbers a second time, and a conversation that
    carries every tool result grows without bound. What it needs is what it
    already said about them.
    """
    if conversation_id is None:
        return []

    rows = (
        (
            await session.execute(
                select(t.Message)
                .where(t.Message.conversation_id == conversation_id)
                .order_by(t.Message.created_at.desc(), t.Message.id)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    history: list[MessageParam] = []
    for row in reversed(rows):
        if row.role not in (MessageRole.USER, MessageRole.ASSISTANT) or not row.content.strip():
            continue
        history.append(MessageParam(role=row.role.value, content=row.content))  # type: ignore[typeddict-item]
    return history


class ClaudeAssistant:
    """Claude, with this product's tools and this product's rules."""

    def __init__(
        self, settings: Settings | None = None, client: AsyncAnthropic | None = None
    ) -> None:
        self.settings = settings or get_settings()
        # Injectable so tests can drive the loop with a scripted model and never
        # touch the network.
        self.client = client or AsyncAnthropic(api_key=self.settings.anthropic_api_key)

    # -- the loop ----------------------------------------------------------

    async def stream(
        self,
        session: AsyncSession,
        shop: ShopContext,
        question: str,
        conversation_id: uuid.UUID | None = None,
    ) -> AsyncIterator[AgentEvent]:
        record = RunRecord(
            question=question,
            model=self.settings.agent_model,
            started_at=datetime.now(tz=UTC),
            conversation_id=conversation_id,
        )
        tools = tools_for(shop)
        by_name = {tool.name: tool for tool in tools}
        params = [tool.as_param() for tool in tools]
        ctx = ToolContext(session=session, shop=shop, embedder=self._embedder())

        messages = await load_history(session, conversation_id)
        messages.append(MessageParam(role="user", content=question))
        answer: list[str] = []

        try:
            for _step in range(MAX_STEPS):
                final, text_events = await self._one_turn(shop, messages, params)
                for event in text_events:
                    yield event

                record.usage.add(final.usage)
                messages.append(MessageParam(role="assistant", content=final.content))
                spoken = "".join(
                    block.text for block in final.content if block.type == "text"
                ).strip()
                if spoken:
                    answer.append(spoken)

                if final.stop_reason == "refusal":
                    raise AssistantRefused(
                        "The model declined to answer this one. Try rephrasing it."
                    )
                if final.stop_reason != "tool_use":
                    break

                calls = [block for block in final.content if block.type == "tool_use"]
                results: list[ToolResultBlockParam] = []
                for call in calls:
                    args = dict(call.input) if isinstance(call.input, dict) else {}
                    yield events.tool_start(call.name, args)
                    result, trace = await self._run_tool(ctx, by_name, call.name, args, call.id)
                    record.tools.append(trace)
                    yield events.tool_end(trace.name, trace.ms, trace.error, trace.result)
                    results.append(result)

                messages.append(MessageParam(role="user", content=results))
            else:
                log.warning("%s: hit the %d-step ceiling", shop.slug, MAX_STEPS)

            for spec in ctx.charts:
                payload = spec.model_dump(mode="json")
                record.charts.append(payload)
                yield events.chart(payload)

            record.answer = "\n\n".join(answer)
            await self._persist(session, shop, record, question)
            yield AgentEvent(events.EventType.DONE, record.done_payload())

        except (AssistantRefused, APIError) as exc:
            record.error = f"{type(exc).__name__}: {exc}"
            record.answer = "\n\n".join(answer)
            await self._persist(session, shop, record, question)
            yield events.error(str(exc))

    async def _one_turn(
        self, shop: ShopContext, messages: list[MessageParam], params: list[Any]
    ) -> tuple[Any, list[AgentEvent]]:
        """One request to the model, collecting text as it arrives.

        The text events are gathered rather than yielded from here because an
        async generator cannot be delegated through a helper without losing the
        ability to return the final message alongside them.
        """
        collected: list[AgentEvent] = []
        async with self.client.messages.stream(
            model=self.settings.agent_model,
            max_tokens=MAX_TOKENS,
            system=system_blocks(shop),
            tools=params,
            messages=messages,
            thinking={"type": "adaptive"},
        ) as stream:
            async for event in stream:
                if event.type == "text":
                    collected.append(events.token(event.text))
            final = await stream.get_final_message()
        return final, collected

    async def _run_tool(
        self,
        ctx: ToolContext,
        by_name: dict[str, Any],
        name: str,
        args: dict[str, Any],
        call_id: str,
    ) -> tuple[ToolResultBlockParam, ToolTrace]:
        """Run one tool call, turning every failure into a result the model can act on.

        Nothing here raises. A tool that blows up mid-conversation should look
        to the model like a tool that said "I could not do that, because —",
        which it can then explain or work around; an exception would lose the
        turn and everything the model had already worked out.
        """
        started = time.monotonic()

        def finish(text: str, error: str | None) -> tuple[ToolResultBlockParam, ToolTrace]:
            ms = int((time.monotonic() - started) * 1000)
            trimmed = text[:MAX_RESULT_CHARS]
            block = ToolResultBlockParam(
                type="tool_result",
                tool_use_id=call_id,
                content=trimmed,
                is_error=error is not None,
            )
            return block, ToolTrace(name=name, args=args, ms=ms, error=error, result=trimmed)

        tool = by_name.get(name)
        if tool is None:
            return finish(f"There is no tool called {name!r}.", "unknown tool")

        try:
            result = await tool.call(ctx, args)
        except ToolError as exc:
            return finish(str(exc), str(exc))
        except CapabilityUnavailable as exc:
            # Not an error in the code: the shop's system genuinely cannot
            # answer this, and the message is already written for the owner.
            return finish(exc.message, None)
        except Exception as exc:
            # Broad on purpose: a tool that blows up must not take the turn
            # with it, and the model can work around "that tool failed".
            log.exception("tool %s failed", name)
            return finish(
                f"That tool failed: {type(exc).__name__}.", f"{type(exc).__name__}: {exc}"
            )

        ctx.observed.record(result)
        return finish(json.dumps(result, default=str, separators=(",", ":")), None)

    def _embedder(self) -> Any:
        try:
            return shared_embedder()
        except EmbeddingError as exc:
            log.warning("catalogue search unavailable: %s", exc)
            return None

    # -- titles ------------------------------------------------------------

    async def title(self, question: str) -> str | None:
        """A short title for the conversation list, on the cheap model."""
        try:
            response = await self.client.messages.create(
                model=self.settings.utility_model,
                max_tokens=32,
                messages=[{"role": "user", "content": TITLE_PROMPT.format(question=question)}],
            )
        except APIError as exc:
            log.warning("could not title conversation: %s", exc)
            return None
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        return text[:200] or None

    # -- persistence -------------------------------------------------------

    async def _persist(
        self, session: AsyncSession, shop: ShopContext, record: RunRecord, question: str
    ) -> None:
        """Write the conversation, the two messages and the run.

        Deliberately after the answer has streamed: the person has their answer
        either way, and a failure to write history should not cost them it.
        """
        now = datetime.now(tz=UTC)
        conversation_id = record.conversation_id
        if conversation_id is None:
            conversation_id = uuid.uuid4()
            record.title = await self.title(question)
            session.add(
                t.Conversation(id=conversation_id, tenant_id=shop.tenant_id, title=record.title)
            )
            await session.flush()
            record.conversation_id = conversation_id

        # Timestamps are set here rather than left to the database default:
        # both rows would otherwise share one statement timestamp, and a
        # conversation replayed in an arbitrary order is worse than no history.
        session.add(
            t.Message(
                id=uuid.uuid4(),
                tenant_id=shop.tenant_id,
                conversation_id=conversation_id,
                role=MessageRole.USER,
                content=question,
                created_at=record.started_at,
            )
        )
        assistant_message_id = uuid.uuid4()
        session.add(
            t.Message(
                id=assistant_message_id,
                tenant_id=shop.tenant_id,
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=record.answer,
                tool_calls=[trace.as_dict() for trace in record.tools],
                charts=record.charts,
                created_at=now,
            )
        )
        # The run points at the message it produced, and nothing declares that
        # as a relationship, so the insert order has to be made explicit.
        await session.flush()

        session.add(
            t.AgentRun(
                id=uuid.uuid4(),
                tenant_id=shop.tenant_id,
                conversation_id=conversation_id,
                message_id=assistant_message_id,
                question=question,
                model=record.model,
                tool_calls=[trace.as_dict() for trace in record.tools],
                input_tokens=record.usage.input_tokens,
                output_tokens=record.usage.output_tokens,
                cache_read_tokens=record.usage.cache_read_tokens,
                cache_write_tokens=record.usage.cache_write_tokens,
                cost_usd=record.usage.cost(record.model),
                latency_ms=record.latency_ms,
                status="error" if record.error else "ok",
                error=record.error,
                started_at=record.started_at,
                finished_at=now,
            )
        )
        await session.commit()


__all__ = [
    "MAX_STEPS",
    "PRICES",
    "Assistant",
    "AssistantRefused",
    "ClaudeAssistant",
    "Price",
    "RunRecord",
    "RunUsage",
    "load_history",
    "price_of",
]
