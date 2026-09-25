"""HTTP for accounts: who am I, and who else is at this shop.

`GET /me` is the first call the web app makes after signing in. It is also the
only route that answers usefully for somebody with no memberships at all — they
get their own name and an empty list of shops, which is what the "you have not
been added to a shop yet" screen is built from.

Everything under `/tenants/{tenant}/members` is owner-only, and the checks that
matter are not about roles: a shop must never end up with no owner, and nobody
may quietly promote themselves.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.accounts.guard import MemberDep, OwnerDep, ViewerDep
from app.accounts.roles import MemberRole
from app.accounts.service import (
    Member,
    ShopMembership,
    grant,
    members_of,
    owner_count,
    shops_for,
)
from app.accounts.tables import Membership, User
from app.http import SessionDep

router = APIRouter(tags=["accounts"])


class ShopOut(BaseModel):
    tenant: str
    name: str
    role: MemberRole


class MeOut(BaseModel):
    """The signed-in person and the shops they may open."""

    email: str
    display_name: str | None
    shops: list[ShopOut]


class MemberOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    role: MemberRole
    # True for the caller's own row, so the UI can stop them removing themselves
    # by accident and can label it "you".
    is_you: bool


class AddMember(BaseModel):
    # The same shape `notify/routes.py` accepts a recipient in: enough to catch a
    # typo, not an attempt to out-argue RFC 5322.
    email: str = Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: MemberRole = MemberRole.STAFF


class ChangeRole(BaseModel):
    role: MemberRole


class UpdateMe(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)


def _shop_out(shop: ShopMembership) -> ShopOut:
    return ShopOut(tenant=shop.tenant, name=shop.name, role=shop.role)


@router.get("/me", response_model=MeOut)
async def me(viewer: ViewerDep, session: SessionDep) -> MeOut:
    """Who you are, and which shops you may open.

    An empty `shops` is a normal answer, not an error: it means the account
    exists but nobody has added it to a shop yet.
    """
    shops = await shops_for(session, viewer)
    return MeOut(
        email=viewer.email,
        display_name=viewer.display_name,
        shops=[_shop_out(shop) for shop in shops],
    )


@router.put("/me", response_model=MeOut)
async def update_me(body: UpdateMe, viewer: ViewerDep, session: SessionDep) -> MeOut:
    """Set your own display name. The only thing about yourself you can change
    here — email and password belong to the auth service, not to us."""
    row = (await session.execute(select(User).where(User.id == viewer.id))).scalar_one()
    name = (body.display_name or "").strip()
    row.display_name = name or None
    await session.commit()

    shops = await shops_for(session, viewer)
    return MeOut(
        email=row.email,
        display_name=row.display_name,
        shops=[_shop_out(shop) for shop in shops],
    )


@router.get("/tenants/{tenant}/members", response_model=list[MemberOut])
async def list_members(member: MemberDep, session: SessionDep) -> list[MemberOut]:
    """Everybody at this shop. Readable by any member: a staff account being
    able to see who else has access is a feature, not a leak."""
    rows = await members_of(session, member.tenant_id)
    return [
        MemberOut(
            id=membership.id,
            email=user.email,
            display_name=user.display_name,
            role=membership.role,
            is_you=user.id == member.viewer.id,
        )
        for user, membership in rows
    ]


@router.post("/tenants/{tenant}/members", response_model=MemberOut, status_code=201)
async def add_member(body: AddMember, owner: OwnerDep, session: SessionDep) -> MemberOut:
    """Add somebody who already has an AIRA account to this shop.

    We do not create the account: that is the auth service's job, and an account
    created here would have no password and no confirmed address. If they have
    never signed in, the answer says so and says what to do about it.
    """
    email = str(body.email).strip().lower()
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"{email} has no AIRA account yet. Ask them to sign up first, then add them here."
            ),
        )

    membership = await grant(
        session,
        user_id=user.id,
        tenant_id=owner.tenant_id,
        role=body.role,
        invited_by=owner.viewer.id,
    )
    return MemberOut(
        id=membership.id,
        email=user.email,
        display_name=user.display_name,
        role=membership.role,
        is_you=user.id == owner.viewer.id,
    )


async def _membership(
    session: SessionDep, owner: Member, membership_id: uuid.UUID
) -> tuple[User, Membership]:
    """One membership at *this* shop, or a 404.

    The tenant filter is the point: a membership id from another shop must not
    be editable just because the caller owns some shop somewhere.
    """
    row = (
        await session.execute(
            select(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.id == membership_id, Membership.tenant_id == owner.tenant_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such member")
    return row[0], row[1]


@router.put("/tenants/{tenant}/members/{membership_id}", response_model=MemberOut)
async def change_role(
    membership_id: uuid.UUID,
    body: ChangeRole,
    owner: OwnerDep,
    session: SessionDep,
) -> MemberOut:
    user, membership = await _membership(session, owner, membership_id)

    losing_an_owner = membership.role is MemberRole.OWNER and body.role is not MemberRole.OWNER
    if losing_an_owner and await owner_count(session, owner.tenant_id) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This is the shop's only owner. Make somebody else an owner first, "
                "then change this one."
            ),
        )

    membership.role = body.role
    await session.commit()
    return MemberOut(
        id=membership.id,
        email=user.email,
        display_name=user.display_name,
        role=membership.role,
        is_you=user.id == owner.viewer.id,
    )


@router.delete("/tenants/{tenant}/members/{membership_id}", status_code=204)
async def remove_member(membership_id: uuid.UUID, owner: OwnerDep, session: SessionDep) -> None:
    """Revoke access. Takes effect on their next request.

    Their Supabase session is untouched and their token stays valid until it
    expires — which is exactly why the membership is checked per request rather
    than trusted from the token.
    """
    _, membership = await _membership(session, owner, membership_id)

    if membership.role is MemberRole.OWNER and await owner_count(session, owner.tenant_id) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This is the shop's only owner. Add another owner before removing this one.",
        )

    await session.delete(membership)
    await session.commit()
