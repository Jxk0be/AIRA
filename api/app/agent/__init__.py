"""The assistant.

    shop = await build_context(session, "animanga_knox")
    async for event in ClaudeAssistant().stream(session, shop, "How did December go?"):
        ...

It answers with numbers from `app.analytics` and text from `app.rag`, and it
knows nothing about where either came from — the tenant's capabilities reach it
as part of the shop's context, and a tool the shop's system cannot support is
never registered (CLAUDE.md rule 1).

`Assistant` is the interface the rest of the product depends on. Everything
model-shaped lives behind it, so replacing what drives the loop is a change in
one file.
"""

from app.agent.actions import ActionKind, ActionRejected, ActionSpec, EmailDraft
from app.agent.assistant import (
    Assistant,
    AssistantRefused,
    ClaudeAssistant,
    RunRecord,
    RunUsage,
    price_of,
)
from app.agent.charts import ChartRejected, ChartSpec, ChartType, Observed, validate
from app.agent.context import ShopContext, build_context
from app.agent.events import AgentEvent, EventType
from app.agent.prompts import starter_questions, system_blocks
from app.agent.tools import ALL_TOOLS, Tool, ToolContext, ToolError, tools_for

__all__ = [
    "ALL_TOOLS",
    "ActionKind",
    "ActionRejected",
    "ActionSpec",
    "AgentEvent",
    "Assistant",
    "AssistantRefused",
    "ChartRejected",
    "ChartSpec",
    "ChartType",
    "ClaudeAssistant",
    "EmailDraft",
    "EventType",
    "Observed",
    "RunRecord",
    "RunUsage",
    "ShopContext",
    "Tool",
    "ToolContext",
    "ToolError",
    "build_context",
    "price_of",
    "starter_questions",
    "system_blocks",
    "tools_for",
    "validate",
]
