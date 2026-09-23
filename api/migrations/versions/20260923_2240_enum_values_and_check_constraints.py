"""enum columns store values, and the vocabulary is enforced

Two problems, one fix.

SQLAlchemy's non-native `Enum` stores the member *name* by default, so a
channel arrived in the database as `IN_STORE` while the canonical model, the
docs and every hand-written query say `in_store`. Nothing broke loudly: the ORM
converts back on read, so only raw SQL — which is most of analytics, the data
quality report and the evals — silently matched nothing.

And `native_enum=False` does not create a CHECK constraint unless asked, so the
columns were bare VARCHARs. An adapter emitting an unknown channel would simply
have had it stored.

Every member name here is the uppercase of its value, so lowercasing the stored
data is the whole data migration.

Revision ID: c3a71d24e90b
Revises: b5c0673a8afc
Create Date: 2026-09-23 22:40:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c3a71d24e90b"
down_revision: str | None = "b5c0673a8afc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table, column, constraint name, allowed values)
ENUM_COLUMNS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("orders", "status", "ck_orders_order_status", ("open", "completed", "canceled")),
    ("orders", "channel", "ck_orders_channel", ("in_store", "online", "event", "other")),
    (
        "inventory_movements",
        "kind",
        "ck_inventory_movements_movement_kind",
        ("received", "sold", "returned", "adjusted", "damaged", "transferred", "counted"),
    ),
    ("sync_runs", "mode", "ck_sync_runs_sync_mode", ("backfill", "incremental")),
    ("sync_runs", "status", "ck_sync_runs_sync_status", ("running", "succeeded", "failed")),
    ("chunks", "source", "ck_chunks_chunk_source", ("product", "document")),
    ("messages", "role", "ck_messages_message_role", ("user", "assistant", "system", "tool")),
)


def upgrade() -> None:
    for table, column, constraint, values in ENUM_COLUMNS:
        op.execute(f"update {table} set {column} = lower({column})")
        allowed = ", ".join(f"'{value}'" for value in values)
        op.execute(
            f"alter table {table} add constraint {constraint} check ({column} in ({allowed}))"
        )


def downgrade() -> None:
    for table, column, constraint, _values in ENUM_COLUMNS:
        op.execute(f"alter table {table} drop constraint if exists {constraint}")
        op.execute(f"update {table} set {column} = upper({column})")
