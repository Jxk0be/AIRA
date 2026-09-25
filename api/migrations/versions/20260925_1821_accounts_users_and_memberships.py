"""people, and which shops they belong to

Our side of Supabase Auth. The account itself — password, confirmation, reset,
sessions — stays in the `auth` schema, which Alembic does not touch. `users` is
the join row we own, and `auth_user_id` is a pointer rather than a foreign key
on purpose: a cross-schema FK would put a Supabase-managed table into the graph
autogenerate compares against, and the next migration would offer to manage it.

`memberships` is where "may this person see this shop" is answered. Not in the
token: a token is refreshed at most once an hour, so a membership carried inside
one would mean revoking access took up to an hour to bite. It is a row, read on
every request, and revoking is a delete.

`users` is the only table of ours with no `tenant_id`, which is a deliberate
exception to rule 3 rather than an oversight: one person can be the owner of one
shop and weekend staff at another, and that is two memberships, not two
accounts. Nothing hangs off `users` except memberships, and every query that
reaches shop data still goes through one.

Revision ID: 5db7501b8290
Revises: 6f9b2c0a4d13
Create Date: 2026-09-25 18:21:40.476386+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5db7501b8290"
down_revision: str | None = "6f9b2c0a4d13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("auth_user_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("last_seen_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("auth_user_id", name="uq_users_auth_user_id"),
        # Emails are lowercased before they are written, so this is a real
        # guarantee rather than a case-sensitive near-miss.
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "owner",
                "manager",
                "staff",
                name="member_role",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("invited_by", sa.UUID(), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Nulled rather than cascaded: losing the person who did the inviting
        # must not remove the people they invited.
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["users.id"],
            name=op.f("fk_memberships_invited_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_memberships_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_memberships_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memberships")),
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_memberships_user_tenant"),
    )
    # "who is at this shop", for the members list.
    op.create_index("ix_memberships_tenant", "memberships", ["tenant_id"], unique=False)
    # "which shops is this person at", read on every single request.
    op.create_index("ix_memberships_user", "memberships", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_memberships_user", table_name="memberships")
    op.drop_index("ix_memberships_tenant", table_name="memberships")
    op.drop_table("memberships")
    op.drop_table("users")
