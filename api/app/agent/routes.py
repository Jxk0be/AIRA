"""HTTP for the assistant.

`POST /tenants/{slug}/chat` streams server-sent events while the answer is
built; the rest is the conversation history the sidebar needs.

The chat endpoint opens its own database session inside the generator rather
than taking one from a dependency. A dependency's session is closed when the
handler returns, and for a streaming response the handler returns before a
single event has been sent — the session would be gone by the time the first
tool ran.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.assistant import Assistant, ClaudeAssistant
from app.agent.context import ShopContext, build_context
from app.agent.events import error as error_event
from app.agent.prompts import starter_questions
from app.agent.tools import tools_for
from app.analytics import TenantNotFound
from app.canonical import tables as t
from app.db import get_session, get_sessionmaker

log = logging.getLogger(__name__)

router = APIRouter(tags=["assistant"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # nginx buffers responses by default, which turns a stream into one lump.
    "X-Accel-Buffering": "no",
}


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str | None
    updated_at: Any


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    tool_calls: list[Any]
    charts: list[Any]
    created_at: Any


class AssistantInfo(BaseModel):
    tenant: str
    shop: str
    timezone: str
    currency: str
    today: str
    data_from: str | None
    data_to: str | None
    capabilities: dict[str, bool]
    tools: list[str]
    caveats: list[str]
    starters: list[str]


async def _shop(session: AsyncSession, slug: str) -> ShopContext:
    try:
        return await build_context(session, slug)
    except TenantNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def build_assistant() -> Assistant:
    """Swapped out in tests for one driven by a scripted model."""
    return ClaudeAssistant()


@router.get("/tenants/{slug}/assistant", response_model=AssistantInfo)
async def assistant_info(
    slug: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> AssistantInfo:
    """What this shop's assistant can do, for the UI to render honestly."""
    shop = await _shop(session, slug)
    return AssistantInfo(
        tenant=shop.slug,
        shop=shop.name,
        timezone=shop.analytics.timezone,
        currency=shop.analytics.currency,
        today=shop.today.isoformat(),
        data_from=shop.first_sale.isoformat() if shop.first_sale else None,
        data_to=shop.last_sale.isoformat() if shop.last_sale else None,
        capabilities=shop.analytics.capabilities.model_dump(),
        tools=[tool.name for tool in tools_for(shop)],
        caveats=list(shop.caveats),
        starters=starter_questions(shop),
    )


@router.get("/tenants/{slug}/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[ConversationSummary]:
    shop = await _shop(session, slug)
    rows = (
        (
            await session.execute(
                select(t.Conversation)
                .where(
                    t.Conversation.tenant_id == shop.tenant_id,
                    t.Conversation.deleted_at.is_(None),
                )
                .order_by(t.Conversation.updated_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        ConversationSummary(id=row.id, title=row.title, updated_at=row.updated_at) for row in rows
    ]


@router.get("/tenants/{slug}/conversations/{conversation_id}", response_model=list[MessageOut])
async def conversation_messages(
    slug: str,
    conversation_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[MessageOut]:
    shop = await _shop(session, slug)
    conversation = (
        await session.execute(
            select(t.Conversation).where(
                t.Conversation.id == conversation_id,
                # Scoped to the tenant in the query, not checked afterwards:
                # an id from another shop simply does not exist here.
                t.Conversation.tenant_id == shop.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="no such conversation")

    rows = (
        (
            await session.execute(
                select(t.Message)
                .where(t.Message.conversation_id == conversation_id)
                .order_by(t.Message.created_at, t.Message.id)
            )
        )
        .scalars()
        .all()
    )
    return [
        MessageOut(
            id=row.id,
            role=row.role.value,
            content=row.content,
            tool_calls=row.tool_calls,
            charts=row.charts,
            created_at=row.created_at,
        )
        for row in rows
    ]


async def _conversation(
    session: AsyncSession, shop: ShopContext, conversation_id: uuid.UUID
) -> t.Conversation:
    """One conversation, scoped to the shop in the query rather than checked
    after the fact: another shop's id simply does not exist here."""
    conversation = (
        await session.execute(
            select(t.Conversation).where(
                t.Conversation.id == conversation_id,
                t.Conversation.tenant_id == shop.tenant_id,
                t.Conversation.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="no such conversation")
    return conversation


@router.patch("/tenants/{slug}/conversations/{conversation_id}", response_model=ConversationSummary)
async def rename_conversation(
    slug: str,
    conversation_id: uuid.UUID,
    body: RenameRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationSummary:
    """Retitle a conversation.

    Haiku names them, and it is mostly right; this is for when it is not.
    """
    shop = await _shop(session, slug)
    conversation = await _conversation(session, shop, conversation_id)
    conversation.title = body.title.strip()
    await session.commit()
    await session.refresh(conversation)
    return ConversationSummary(
        id=conversation.id, title=conversation.title, updated_at=conversation.updated_at
    )


@router.delete("/tenants/{slug}/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    slug: str,
    conversation_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Soft delete, like everything else here.

    The messages stay, and so do the `agent_runs` rows behind them: what a
    question cost is ours to keep even once the shop has tidied its sidebar.
    """
    shop = await _shop(session, slug)
    conversation = await _conversation(session, shop, conversation_id)
    conversation.deleted_at = datetime.now(tz=UTC)
    await session.commit()


async def _events(slug: str, body: ChatRequest, assistant: Assistant) -> AsyncIterator[str]:
    """One chat turn, as server-sent events.

    Every failure past this point becomes an `error` event rather than a broken
    response: the client is already reading a 200 and cannot be told otherwise.
    """
    try:
        async with get_sessionmaker()() as session:
            shop = await build_context(session, slug)
            async for event in assistant.stream(session, shop, body.message, body.conversation_id):
                yield event.sse()
    except TenantNotFound:
        yield error_event(f"no tenant {slug!r}").sse()
    except Exception as exc:  # the stream has already started; say so in it
        log.exception("chat failed for %s", slug)
        yield error_event(f"Something went wrong: {type(exc).__name__}").sse()


@router.post("/tenants/{slug}/chat")
async def chat(
    slug: str,
    body: ChatRequest,
    assistant: Annotated[Assistant, Depends(build_assistant)],
) -> StreamingResponse:
    """Ask the shop's analyst something.

    Events: `token` as the answer is written, `tool_start` / `tool_end` around
    each tool call, `chart` for a validated chart, then exactly one `done` or
    one `error`.
    """
    return StreamingResponse(
        _events(slug, body, assistant),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
