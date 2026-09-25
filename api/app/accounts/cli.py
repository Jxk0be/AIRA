"""Managing who may open a shop, from the terminal.

    python tasks.py members animanga_knox
    python tasks.py invite owner@shop.com animanga_knox owner
    python tasks.py revoke owner@shop.com animanga_knox

This is how the first owner of a shop gets in, because there is no way to do it
through the API: adding a member is owner-only, and a brand-new shop has no
owner. It is also the hand-held half of onboarding — for the first ten local
shops, somebody connecting a register for a customer is going to want this rather
than a self-serve flow.

`invite` resolves an email in three steps, and says which one it took:

1. Our own `users` table — somebody who has signed in here before.
2. `auth.users` — they have a Supabase account but have not used AIRA yet, so we
   create our row for them. This is the only place in the codebase that reads a
   Supabase-managed schema, and it only ever reads.
3. Neither — nothing is granted. With `SUPABASE_SERVICE_KEY` set, `--send` will
   ask the auth server to email them an invitation first.

It never creates an account itself. An account made here would have no password
and no confirmed address, and confirming an address is exactly what the auth
service is for.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.roles import MemberRole
from app.accounts.service import grant, members_of
from app.accounts.tables import Membership, User
from app.canonical import tables as t
from app.config import get_settings
from app.db import dispose_engine, get_sessionmaker

INVITE_TIMEOUT = 20.0


@dataclass(frozen=True, slots=True)
class Resolved:
    user: User
    how: str


async def _tenant(session: AsyncSession, slug: str) -> t.Tenant:
    tenant = (
        await session.execute(
            select(t.Tenant).where(t.Tenant.slug == slug, t.Tenant.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if tenant is None:
        known = (
            (await session.execute(select(t.Tenant.slug).order_by(t.Tenant.slug))).scalars().all()
        )
        raise SystemExit(f"no shop {slug!r}. Known shops: {', '.join(known) or 'none'}")
    return tenant


async def _auth_user_id(session: AsyncSession, email: str) -> uuid.UUID | None:
    """Look an email up in Supabase's own user table.

    Read-only, and the one place we touch the `auth` schema. Alembic still
    ignores it entirely (CLAUDE.md rule 6) — reading a row is not owning a table.
    """
    row = (
        await session.execute(
            text("select id from auth.users where lower(email) = :email limit 1"),
            {"email": email},
        )
    ).scalar_one_or_none()
    return uuid.UUID(str(row)) if row else None


async def _resolve(session: AsyncSession, email: str) -> Resolved | None:
    ours = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if ours is not None:
        return Resolved(ours, "already has an AIRA account")

    auth_id = await _auth_user_id(session, email)
    if auth_id is None:
        return None

    row = User(auth_user_id=auth_id, email=email)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return Resolved(row, "has a Supabase account; created their AIRA row")


async def _send_invitation(email: str) -> str:
    """Ask the auth server to email an invitation.

    Uses the admin key, which is why this is a terminal command and not a route.
    """
    settings = get_settings()
    if not settings.supabase_url:
        raise SystemExit("SUPABASE_URL is not set; see .env.example")
    if not settings.supabase_service_key:
        raise SystemExit(
            "SUPABASE_SERVICE_KEY is not set, so no invitation can be sent.\n"
            "`npx supabase status` prints the local one. Or ask them to sign up "
            "and run this again without --send."
        )

    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/invite"
    async with httpx.AsyncClient(timeout=INVITE_TIMEOUT) as client:
        response = await client.post(
            url,
            json={"email": email},
            headers={
                "apikey": settings.supabase_service_key,
                "Authorization": f"Bearer {settings.supabase_service_key}",
                "Content-Type": "application/json",
            },
        )
    if response.status_code >= 400:
        raise SystemExit(f"the auth server refused the invitation: {response.text}")
    return str(response.json().get("id") or "")


async def cmd_members(args: argparse.Namespace) -> int:
    async with get_sessionmaker()() as session:
        tenant = await _tenant(session, args.tenant)
        rows = await members_of(session, tenant.id)
        if not rows:
            print(f"{tenant.name} has no members. Nobody can open it yet.")
            print(f"  python tasks.py invite <email> {tenant.slug} owner")
            return 0
        print(f"{tenant.name} ({tenant.slug})")
        for user, membership in rows:
            seen = user.last_seen_at.date().isoformat() if user.last_seen_at else "never"
            print(f"  {membership.role.value:<8} {user.email:<40} last seen {seen}")
    return 0


async def cmd_invite(args: argparse.Namespace) -> int:
    email = args.email.strip().lower()
    role = MemberRole(args.role)

    async with get_sessionmaker()() as session:
        tenant = await _tenant(session, args.tenant)

        resolved = await _resolve(session, email)
        if resolved is None and args.send:
            invited = await _send_invitation(email)
            print(f"invitation emailed to {email}" + (f" (auth user {invited})" if invited else ""))
            resolved = await _resolve(session, email)

        if resolved is None:
            print(f"{email} has no account.", file=sys.stderr)
            print(
                "Ask them to sign up, then run this again — or re-run with --send "
                "to have the auth server email them an invitation.",
                file=sys.stderr,
            )
            return 1

        await grant(session, user_id=resolved.user.id, tenant_id=tenant.id, role=role)
        print(f"{email} is now {role.value} at {tenant.name} ({resolved.how})")
    return 0


async def cmd_revoke(args: argparse.Namespace) -> int:
    email = args.email.strip().lower()
    async with get_sessionmaker()() as session:
        tenant = await _tenant(session, args.tenant)
        row = (
            await session.execute(
                select(Membership)
                .join(User, User.id == Membership.user_id)
                .where(User.email == email, Membership.tenant_id == tenant.id)
            )
        ).scalar_one_or_none()
        if row is None:
            print(f"{email} is not a member of {tenant.slug}", file=sys.stderr)
            return 1

        if row.role is MemberRole.OWNER:
            owners = [
                m for _, m in await members_of(session, tenant.id) if m.role is MemberRole.OWNER
            ]
            if len(owners) <= 1 and not args.force:
                print(
                    f"{email} is the only owner of {tenant.slug}. Removing them leaves "
                    "nobody who can add anyone back. Pass --force if that is really "
                    "what you want.",
                    file=sys.stderr,
                )
                return 1

        await session.delete(row)
        await session.commit()
        print(f"{email} can no longer open {tenant.name}. Takes effect on their next request.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="accounts", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("members", help="who may open a shop")
    listing.add_argument("tenant", help="the shop's slug")
    listing.set_defaults(run=cmd_members)

    invite = sub.add_parser("invite", help="add somebody to a shop")
    invite.add_argument("email")
    invite.add_argument("tenant", help="the shop's slug")
    invite.add_argument(
        "role",
        nargs="?",
        default=MemberRole.STAFF.value,
        choices=[role.value for role in MemberRole],
        help="owner writes everything; manager runs the day job; staff reads (default)",
    )
    invite.add_argument(
        "--send",
        action="store_true",
        help="email an invitation if they have no account yet (needs SUPABASE_SERVICE_KEY)",
    )
    invite.set_defaults(run=cmd_invite)

    revoke = sub.add_parser("revoke", help="remove somebody from a shop")
    revoke.add_argument("email")
    revoke.add_argument("tenant", help="the shop's slug")
    revoke.add_argument("--force", action="store_true", help="allow removing the last owner")
    revoke.set_defaults(run=cmd_revoke)

    args = parser.parse_args(argv)

    async def run() -> int:
        try:
            code: int = await args.run(args)
            return code
        finally:
            await dispose_engine()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
