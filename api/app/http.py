"""What every feature's HTTP layer shares.

One tenant dependency rather than one per router, because the resolution rule —
a slug in the path, looked up here, never an id from the client — is a tenant
isolation rule (CLAUDE.md rule 3) and wants to live in exactly one place.

Authentication happens before any of this. `app.accounts.guard.guard` is
installed application-wide in `app.main`: by the time `shop_dependency` runs, the
caller has been identified from their bearer token and their membership of this
shop has been read from our own tables. That is why resolving a tenant from the
path is safe here — the entitlement question was already answered, and answered
in one place for every route.

`Shop` therefore carries the caller's membership alongside the analytics context,
so a route that needs to know whether this person may *write* has it to hand
without a second query.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.guard import (
    MANAGER_ONLY,
    OWNER_ONLY,
    ManagerDep,
    MemberDep,
    OwnerDep,
    ViewerDep,
    member_of,
    requires,
)
from app.accounts.roles import MemberRole
from app.accounts.service import Member
from app.analytics import AnalyticsContext, TenantNotFound, load_context
from app.db import get_session


@dataclass(frozen=True, slots=True)
class Shop:
    """One resolved shop, the caller's standing in it, and the request's session."""

    ctx: AnalyticsContext
    session: AsyncSession
    member: Member

    @property
    def slug(self) -> str:
        return self.ctx.slug

    @property
    def role(self) -> MemberRole:
        return self.member.role

    def require(self, role: MemberRole) -> None:
        """Refuse unless the caller holds at least `role` here.

        For the handful of places where the check depends on the request body
        rather than the route. Prefer `ManagerDep` / `OwnerDep` on the route
        itself, which puts the requirement in the OpenAPI schema.
        """
        if not self.member.at_least(role):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"This needs the {role.value} role at {self.slug}. "
                    f"You are {self.role.value} here."
                ),
            )


async def shop_dependency(
    tenant: Annotated[str, Path(description="the shop's slug")],
    session: Annotated[AsyncSession, Depends(get_session)],
    member: Annotated[Member, Depends(member_of)],
) -> AsyncIterator[Shop]:
    try:
        ctx = await load_context(session, tenant)
    except TenantNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    yield Shop(ctx=ctx, session=session, member=member)


ShopDep = Annotated[Shop, Depends(shop_dependency)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"no such {what}")


# Re-exported so a feature router imports its HTTP furniture from one place.
__all__ = [
    "MANAGER_ONLY",
    "OWNER_ONLY",
    "ManagerDep",
    "MemberDep",
    "OwnerDep",
    "SessionDep",
    "Shop",
    "ShopDep",
    "ViewerDep",
    "member_of",
    "not_found",
    "requires",
    "shop_dependency",
]
