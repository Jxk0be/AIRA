"""buttons an answer offered, kept with the answer

The assistant may now end a turn by offering an action — open the reorder list,
draft an email to a vendor. Those offers are part of what was said, so they are
stored on the message alongside its charts rather than living only in the
stream: an owner who reopens yesterday's conversation should find the same
buttons under the same answer.

Not null with a server default of `[]`, so every message written before this
reads back as "offered nothing" rather than as null, and nothing in the
dashboard has to tell the difference.

Revision ID: 6f9b2c0a4d13
Revises: 8c4a11f0d2b7
Create Date: 2026-09-24 07:30:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6f9b2c0a4d13"
down_revision: str | None = "8c4a11f0d2b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "actions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("messages", "actions")
