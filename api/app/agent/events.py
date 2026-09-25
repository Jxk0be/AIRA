"""What the client sees while an answer is being built.

Seven event types, and the UI can be written against them without knowing
anything about how the agent loop works: text arrives as `token`, each tool call
brackets its own work with `tool_start` and `tool_end`, a validated chart comes
through as `chart`, a button the answer offers to press comes through as
`action`, and every run ends with exactly one `done` or one `error`.

`tool_start` and `tool_end` exist because a question that takes six seconds
needs to show its working. "Checking last December's sales" is the difference
between a slow answer and a broken one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    TOKEN = "token"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    CHART = "chart"
    ACTION = "action"
    DONE = "done"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)

    def sse(self) -> str:
        """One server-sent event.

        Named events (`event:` line) rather than a type field inside the JSON, so
        the browser's own EventSource dispatch can be used directly.
        """
        payload = json.dumps(self.data, default=str, separators=(",", ":"))
        return f"event: {self.type.value}\ndata: {payload}\n\n"


def token(text: str) -> AgentEvent:
    return AgentEvent(EventType.TOKEN, {"text": text})


def tool_start(name: str, args: dict[str, Any]) -> AgentEvent:
    return AgentEvent(EventType.TOOL_START, {"tool": name, "args": args})


def tool_end(name: str, ms: int, error: str | None = None, result: str | None = None) -> AgentEvent:
    """`result` is exactly what the model was handed, not a shortened version.

    The UI shortens it for a collapsed chip, and anything checking that an
    answer only names things a tool returned needs the whole thing — a
    reorder list truncated at two thousand characters makes half its own
    evidence look invented.
    """
    return AgentEvent(
        EventType.TOOL_END,
        {"tool": name, "ms": ms, "error": error, "result": result or None},
    )


def chart(spec: dict[str, Any]) -> AgentEvent:
    return AgentEvent(EventType.CHART, spec)


def action(spec: dict[str, Any]) -> AgentEvent:
    """One button the answer offers, already checked against the catalogue.

    Sent after the answer rather than during it, for the same reason a chart is:
    a button that appears mid-sentence invites a click on an answer that is
    still being written.
    """
    return AgentEvent(EventType.ACTION, spec)


def error(message: str) -> AgentEvent:
    return AgentEvent(EventType.ERROR, {"message": message})
