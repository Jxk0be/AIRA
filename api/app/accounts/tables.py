"""People, and which shops they belong to.

Two tables, and the interesting thing about both is what they deliberately do
not do.

`users` is not the account record. Supabase Auth owns that — the password, the
email confirmation, the reset flow, the sessions — and it lives in the `auth`
schema, which Alembic is not allowed to touch (CLAUDE.md rule 6). This table is
our side of the join: one row per auth user we have ever seen, holding the
handful of fields our own screens need. `auth_user_id` is therefore a pointer
rather than a foreign key. A cross-schema FK into `auth.users` is a supported
Supabase pattern, but it would put a Supabase-managed table into the graph
Alembic autogenerates against, and the first migration after that would offer to
manage it for us.

`memberships` is where authorisation actually lives. Not in the token: a token
is issued by a service we do not control and is only refreshed once an hour, so
"this person may see this shop" being a claim inside it would mean removing
somebody's access took effect up to an hour later. It is a row here, read on
every request, and revoking access is a delete.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.accounts.roles import MemberRole
from app.db import Base
from app.schema import TenantMixin, TimestampMixin, enum_column, pk


class User(TimestampMixin, Base):
    """One person who has signed in at least once.

    Not tenant-scoped, and the only table of ours that is not: the same person
    can be the owner of one shop and weekend staff at another, and that is a
    membership each, not two accounts.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("auth_user_id", name="uq_users_auth_user_id"),
        # Lowercased on the way in, so this is a real uniqueness guarantee and
        # not a case-sensitive near-miss.
        UniqueConstraint("email", name="uq_users_email"),
    )

    id: Mapped[uuid.UUID] = pk()
    # `auth.users.id` — the `sub` claim of every token this person sends.
    auth_user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    # Updated at most once an hour rather than on every request; see
    # `app.accounts.service.LAST_SEEN_INTERVAL` for why.
    last_seen_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class Membership(TenantMixin, Base):
    """This person, at this shop, in this role.

    Deleting the row is how access is revoked, and it takes effect on the next
    request. Note that it does **not** invalidate the token itself — Supabase
    would still call it valid — which is exactly why the membership is checked
    per request instead of being trusted from the token.
    """

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_memberships_user_tenant"),
        # "who is at this shop", for the members list.
        Index("ix_memberships_tenant", "tenant_id"),
        # "which shops is this person at", read on every single request.
        Index("ix_memberships_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MemberRole] = mapped_column(
        enum_column(MemberRole, "member_role"), nullable=False, default=MemberRole.STAFF
    )
    # Who added them, for the audit question that follows any access surprise.
    # Nulled rather than cascaded when that person's own account goes away.
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
