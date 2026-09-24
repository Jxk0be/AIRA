"""What every feature's HTTP layer shares.

One tenant dependency rather than one per router, because the resolution rule —
a slug in the path, looked up here, never an id from the client — is a tenant
isolation rule (CLAUDE.md rule 3) and wants to live in exactly one place.

There is still no authentication. When it lands, this is the function that
learns about it, and every route that already depends on `Shop` gets it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext, TenantNotFound, load_context
from app.db import get_session


@dataclass(frozen=True, slots=True)
class Shop:
    """One resolved shop, plus the session the request runs on."""

    ctx: AnalyticsContext
    session: AsyncSession

    @property
    def slug(self) -> str:
        return self.ctx.slug


async def shop_dependency(
    tenant: Annotated[str, Path(description="the shop's slug")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AsyncIterator[Shop]:
    try:
        ctx = await load_context(session, tenant)
    except TenantNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    yield Shop(ctx=ctx, session=session)


ShopDep = Annotated[Shop, Depends(shop_dependency)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"no such {what}")
