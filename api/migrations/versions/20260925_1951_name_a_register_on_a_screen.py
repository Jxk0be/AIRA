"""name a register on a screen

A consolidated P&L has to label its rows, and the label cannot be the source
slug: "registerone" is not what a shop calls the till by the door.

The name lives here, on a canonical table, rather than being looked up from the
adapter that knows the platform. `app.analytics` is not allowed to import
`app.connectors` or know which platform a tenant runs (CLAUDE.md rule 1), so the
platform-aware layer writes the name down once when the integration is created
and the platform-blind layer reads a string.

Nullable, and every existing row stays null: a tenant with one register has
nothing to disambiguate, and the breakdown falls back to the source slug rather
than showing a blank. Backfilling the two dev tenants would put fixture data in
a migration.

Revision ID: 762f94f90f90
Revises: 9a4c7e1f5b20
Create Date: 2026-09-25 19:51:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "762f94f90f90"
down_revision: str | None = "9a4c7e1f5b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("integrations", sa.Column("display_name", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("integrations", "display_name")
