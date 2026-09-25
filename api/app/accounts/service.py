"""Turning a verified token into a person, and a person into their shops.

The one thing to know about this module: a valid token gets you a `users` row
and nothing else. Signing up grants no access to any shop. Access is a
`memberships` row, and only an existing owner (or `tasks.py invite`) can create
one.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.roles import MemberRole
from app.accounts.tables import Membership, User
from app.accounts.tokens import AccessToken
from app.canonical import tables as t

log = logging.getLogger(__name__)

# `last_seen_at` is a nicety on the members list, not an audit log — the
# outbound message log and `agent_runs` are the audit trail. Writing it on every
# request would add a write to every read, so it moves at most once an hour.
LAST_SEEN_INTERVAL = timedelta(hours=1)


class EmailAlreadyClaimed(Exception):
    """This email already belongs to a different auth account.

    Rare, and deliberately not resolved automatically. It means an account was
    deleted and recreated, or two sign-in providers landed on the same address.
    Re-pointing the existing row would hand the new account every membership the
    old one had, and we cannot prove from a token that the same person is behind
    both — `email_verified` arrives in `user_metadata`, which the user can write.
    """


@dataclass(frozen=True, slots=True)
class Viewer:
    """The signed-in person, as our own tables know them."""

    id: uuid.UUID
    auth_user_id: uuid.UUID
    email: str
    display_name: str | None

    @property
    def name(self) -> str:
        return self.display_name or self.email


@dataclass(frozen=True, slots=True)
class Member:
    """A viewer, at one shop, with the role they hold there."""

    viewer: Viewer
    tenant_id: uuid.UUID
    tenant_slug: str
    role: MemberRole

    def at_least(self, needed: MemberRole) -> bool:
        return self.role.at_least(needed)


@dataclass(frozen=True, slots=True)
class ShopMembership:
    """One row of "your shops", for the picker and the /me response."""

    tenant: str
    name: str
    role: MemberRole


def _viewer(row: User) -> Viewer:
    return Viewer(
        id=row.id,
        auth_user_id=row.auth_user_id,
        email=row.email,
        display_name=row.display_name,
    )


async def resolve_viewer(session: AsyncSession, token: AccessToken) -> Viewer:
    """Our row for this auth user, created the first time we see them.

    Creating it is not a grant: a brand-new user has no memberships and every
    tenant-scoped route answers 404 for them.
    """
    existing = (
        await session.execute(select(User).where(User.auth_user_id == token.subject))
    ).scalar_one_or_none()

    if existing is not None:
        changed = False
        if existing.email != token.email:
            # They changed it in the auth app; follow along unless the new
            # address already belongs to somebody else.
            taken = (
                await session.execute(
                    select(User.id).where(User.email == token.email, User.id != existing.id)
                )
            ).scalar_one_or_none()
            if taken is not None:
                raise EmailAlreadyClaimed(
                    "That email address is already in use by another account here. "
                    "Ask your shop's owner to sort it out before signing in."
                )
            existing.email = token.email
            changed = True

        now = datetime.now(UTC)
        if existing.last_seen_at is None or now - existing.last_seen_at > LAST_SEEN_INTERVAL:
            existing.last_seen_at = now
            changed = True
        if changed:
            await session.commit()
        return _viewer(existing)

    clash = (
        await session.execute(select(User).where(User.email == token.email))
    ).scalar_one_or_none()
    if clash is not None:
        log.warning(
            "auth user %s signed in with %s, already held by user %s",
            token.subject,
            token.email,
            clash.id,
        )
        raise EmailAlreadyClaimed(
            "That email address is already in use by another account here. "
            "Ask your shop's owner to sort it out before signing in."
        )

    row = User(
        auth_user_id=token.subject,
        email=token.email,
        last_seen_at=datetime.now(UTC),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _viewer(row)


async def member_for_slug(session: AsyncSession, viewer: Viewer, slug: str) -> Member | None:
    """This viewer's membership at the shop with this slug, or None.

    One query, and it is on the request path for every tenant-scoped route.
    `None` covers both "no such shop" and "not your shop" on purpose — the
    caller turns both into the same 404, so a stranger cannot use the difference
    between 403 and 404 to enumerate which shops exist.
    """
    row = (
        await session.execute(
            select(Membership.tenant_id, Membership.role, t.Tenant.slug)
            .join(t.Tenant, t.Tenant.id == Membership.tenant_id)
            .where(
                Membership.user_id == viewer.id,
                t.Tenant.slug == slug,
                t.Tenant.deleted_at.is_(None),
            )
        )
    ).one_or_none()
    if row is None:
        return None
    return Member(viewer=viewer, tenant_id=row.tenant_id, tenant_slug=row.slug, role=row.role)


async def shops_for(session: AsyncSession, viewer: Viewer) -> list[ShopMembership]:
    """Every shop this viewer may see, in the order a picker should show them."""
    rows = (
        await session.execute(
            select(t.Tenant.slug, t.Tenant.name, Membership.role)
            .join(Membership, Membership.tenant_id == t.Tenant.id)
            .where(Membership.user_id == viewer.id, t.Tenant.deleted_at.is_(None))
            .order_by(t.Tenant.name)
        )
    ).all()
    return [ShopMembership(tenant=row.slug, name=row.name, role=row.role) for row in rows]


async def members_of(session: AsyncSession, tenant_id: uuid.UUID) -> list[tuple[User, Membership]]:
    """Everybody at one shop, owners first."""
    rows = (
        await session.execute(
            select(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.tenant_id == tenant_id)
            .order_by(Membership.role, User.email)
        )
    ).all()
    # `role` sorts alphabetically in SQL ("manager" < "owner" < "staff"), which
    # is not the order a person expects to read them in, so the ordering that
    # matters happens here against `MemberRole.rank`.
    pairs = [(user, membership) for user, membership in rows]
    return sorted(pairs, key=lambda pair: (-pair[1].role.rank, pair[0].email))


async def grant(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    role: MemberRole,
    invited_by: uuid.UUID | None = None,
) -> Membership:
    """Add or change somebody's membership at one shop.

    Idempotent: inviting a person who is already a member changes their role
    rather than failing, which is what "add them as a manager" means when they
    are currently staff.
    """
    existing = (
        await session.execute(
            select(Membership).where(
                Membership.user_id == user_id, Membership.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.role = role
        await session.commit()
        await session.refresh(existing)
        return existing

    row = Membership(user_id=user_id, tenant_id=tenant_id, role=role, invited_by=invited_by)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def owner_count(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    """How many owners a shop has.

    Read before removing or demoting one: a shop with no owner has nobody who
    can connect a register, change the plan or add anyone back.
    """
    rows = (
        await session.execute(
            select(Membership.id).where(
                Membership.tenant_id == tenant_id, Membership.role == MemberRole.OWNER
            )
        )
    ).all()
    return len(rows)
