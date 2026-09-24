"""A scripted stand-in for the Anthropic client.

The agent loop is the part of this system with the most moving pieces and the
least tolerance for a surprise in production, so it is tested against a model
whose every answer is written down in advance. No network, no cost, no
flakiness, and a test can say "now the model calls make_chart with a number
nobody returned" and watch what happens.

It imitates only the surface `ClaudeAssistant` uses: `messages.stream(...)` as
an async context manager that yields text events and then a final message, and
`messages.create(...)` for conversation titles.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from anthropic.lib.streaming import TextEvent
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

# A scripted turn is either a fixed message or a function of the request that
# produced it — which is how a test says "chart whatever the last tool actually
# returned" without knowing the fixture's numbers in advance.
Turn = Message | Callable[[dict[str, Any]], Message]


def text_block(text: str) -> TextBlock:
    return TextBlock(type="text", text=text, citations=None)


def tool_block(name: str, args: dict[str, Any], call_id: str = "toolu_1") -> ToolUseBlock:
    return ToolUseBlock(type="tool_use", id=call_id, name=name, input=args)


def message(
    content: Sequence[TextBlock | ToolUseBlock],
    stop_reason: str = "end_turn",
    *,
    input_tokens: int = 1_200,
    output_tokens: int = 180,
    cache_read: int = 0,
    cache_write: int = 0,
) -> Message:
    return Message(
        id="msg_fake",
        model="claude-sonnet-5",
        role="assistant",
        type="message",
        content=list(content),
        stop_reason=stop_reason,  # type: ignore[arg-type]
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
    )


class FakeStream:
    def __init__(self, final: Message) -> None:
        self._final = final

    async def __aenter__(self) -> FakeStream:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def __aiter__(self) -> AsyncIterator[TextEvent]:
        for block in self._final.content:
            if block.type == "text":
                # One event per word, which is close enough to how real text
                # arrives and makes "did the client see a stream?" testable.
                for word in block.text.split(" "):
                    yield TextEvent(type="text", text=word + " ", snapshot=block.text)

    async def get_final_message(self) -> Message:
        return self._final


@dataclass
class FakeMessages:
    turns: list[Turn]
    title: str = "December sales"
    calls: list[dict[str, Any]] = field(default_factory=list)
    _index: int = 0

    def stream(self, **kwargs: Any) -> FakeStream:
        """Not async: the real SDK returns the context manager synchronously."""
        # The message list is the assistant's own, and it keeps appending to it.
        # Recording the reference would make every call look like the last one.
        self.calls.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        if self._index >= len(self.turns):
            raise AssertionError("the assistant asked for more turns than the script has")
        scripted = self.turns[self._index]
        self._index += 1
        final = scripted(kwargs) if callable(scripted) else scripted
        return FakeStream(final)

    async def create(self, **kwargs: Any) -> Message:
        """Only ever used for conversation titles."""
        self.calls.append(kwargs)
        return message([text_block(self.title)])


@dataclass
class FakeAnthropic:
    messages: FakeMessages

    @classmethod
    def scripted(cls, *turns: Turn, title: str = "December sales") -> FakeAnthropic:
        return cls(messages=FakeMessages(turns=list(turns), title=title))

    @property
    def stream_calls(self) -> list[dict[str, Any]]:
        return [call for call in self.messages.calls if "tools" in call]
