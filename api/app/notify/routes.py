"""HTTP for who hears from us, and what we sent them.

The message log is exposed on purpose. "Did you email me about that?" is a
question the support conversation has to be able to answer without a database
client, and showing the owner every suppressed message is what makes the alert
budget feel like a setting rather than a shrug.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.canonical.enums import NotifyChannel
from app.http import MANAGER_ONLY, SessionDep, ShopDep
from app.notify import (
    recent_messages,
    recipients,
    unsubscribe,
    upsert_recipient,
)

router = APIRouter(tags=["notifications"])
public_router = APIRouter(tags=["notifications (public)"])


class RecipientOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    name: str | None
    email: str
    phone: str | None
    channels: list[str]
    quiet_hours_start: time | None
    quiet_hours_end: time | None
    max_per_day: int
    wants_digest: bool


def _email_only() -> list[Literal["email", "sms"]]:
    """The default channel list. A lambda here types as `list[str]`."""
    return ["email"]


class RecipientIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    channels: list[Literal["email", "sms"]] = Field(default_factory=_email_only)
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    # 0 means no ceiling. Urgent alerts ignore it either way.
    max_per_day: int = Field(default=3, ge=0, le=50)
    wants_digest: bool = True

    @model_validator(mode="after")
    def _quiet_hours_are_a_pair(self) -> RecipientIn:
        if (self.quiet_hours_start is None) != (self.quiet_hours_end is None):
            raise ValueError("quiet hours need both a start and an end")
        return self


class MessageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    channel: str
    kind: str
    to_address: str
    subject: str | None
    status: str
    # Why it was held back, or why sending failed. Written for a person.
    detail: str | None
    created_at: datetime
    sent_at: datetime | None


@router.get("/tenants/{tenant}/notifications", response_model=list[RecipientOut])
async def people(shop: ShopDep) -> list[RecipientOut]:
    return [
        RecipientOut(
            id=person.id,
            name=person.name,
            email=person.email,
            phone=person.phone,
            channels=[channel.value for channel in person.channels],
            quiet_hours_start=person.quiet_hours_start,
            quiet_hours_end=person.quiet_hours_end,
            max_per_day=person.max_per_day,
            wants_digest=person.wants_digest,
        )
        for person in await recipients(shop.session, shop.ctx)
    ]


@router.put("/tenants/{tenant}/notifications", response_model=uuid.UUID, dependencies=MANAGER_ONLY)
async def save_person(shop: ShopDep, body: RecipientIn) -> uuid.UUID:
    quiet = (
        (body.quiet_hours_start, body.quiet_hours_end)
        if body.quiet_hours_start and body.quiet_hours_end
        else None
    )
    return await upsert_recipient(
        shop.session,
        shop.ctx.tenant_id,
        email=body.email,
        name=body.name,
        phone=body.phone,
        channels=[channel for channel in body.channels if channel in set(NotifyChannel)],
        max_per_day=body.max_per_day,
        wants_digest=body.wants_digest,
        quiet_hours=quiet,
    )


@router.get("/tenants/{tenant}/notifications/messages", response_model=list[MessageOut])
async def messages(shop: ShopDep, days: int = 30, limit: int = 50) -> list[MessageOut]:
    return [
        MessageOut(
            id=row.id,
            channel=str(row.channel),
            kind=row.kind,
            to_address=row.to_address,
            subject=row.subject,
            status=str(row.status),
            detail=row.detail,
            created_at=row.created_at,
            sent_at=row.sent_at,
        )
        for row in await recent_messages(shop.session, shop.ctx, days=days, limit=limit)
    ]


@public_router.get("/unsubscribe/{token}")
async def stop(token: str, session: SessionDep) -> dict[str, str]:
    """One click, no login, idempotent. A prefetch must not turn it into a 404."""
    matched = await unsubscribe(session, token)
    return {"status": "unsubscribed" if matched else "already unsubscribed"}
