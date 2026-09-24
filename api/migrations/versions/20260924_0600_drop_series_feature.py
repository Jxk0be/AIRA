"""drop the series feature

The "next volume is in" feature was dropped before it shipped: messaging a
shop's customers means running a consent regime — prior express consent, a
working STOP, A2P 10DLC registration on the sending number — and that is not
a commitment this product is taking on.

Written forward-only rather than folded back into the migration that created
these tables, because that one had already been applied. A migration that has
run is history; you add to it, you do not edit it.

`downgrade` recreates the tables as they were, so the revision is reversible,
but nothing in the application reads or writes them any more.

Revision ID: 8c4a11f0d2b7
Revises: 35e1506805f2
Create Date: 2026-09-24 06:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8c4a11f0d2b7"
down_revision: str | None = "35e1506805f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Children first: releases and followers both point at series_items or
    # series, and dropping a parent out from under them would fail.
    op.drop_index("ix_series_releases_tenant_detected", table_name="series_releases")
    op.drop_table("series_releases")

    op.drop_index("ix_series_followers_series_source", table_name="series_followers")
    op.drop_table("series_followers")

    op.drop_index("ix_series_items_series", table_name="series_items")
    op.drop_table("series_items")

    op.drop_index("ix_series_tenant_status", table_name="series")
    op.drop_table("series")


def downgrade() -> None:
    op.create_table(
        "series",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("normalised_name", sa.String(length=300), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "proposed",
                "confirmed",
                "rejected",
                name="series_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("detected_by", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("confirmed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("auto_send", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_series_tenant_id_tenants", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_series"),
        sa.UniqueConstraint("tenant_id", "normalised_name", name="uq_series_tenant_name"),
    )
    op.create_index("ix_series_tenant_status", "series", ["tenant_id", "status"], unique=False)

    op.create_table(
        "series_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("series_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("volume_number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "proposed",
                "confirmed",
                "rejected",
                name="series_item_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("confidence", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("parsed_from", sa.String(length=512), nullable=True),
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
        sa.CheckConstraint("volume_number > 0", name="ck_series_items_volume_number_positive"),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["series.id"],
            name="fk_series_items_series_id_series",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_series_items_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["variants.id"],
            name="fk_series_items_variant_id_variants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_series_items"),
        sa.UniqueConstraint("tenant_id", "variant_id", name="uq_series_items_variant"),
    )
    op.create_index("ix_series_items_series", "series_items", ["series_id"], unique=False)

    op.create_table(
        "series_followers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("series_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=True),
        sa.Column("contact_key", sa.String(length=320), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "inferred",
                "opt_in",
                name="follower_source",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("volumes_bought", sa.Integer(), nullable=False),
        sa.Column("consent_text", sa.Text(), nullable=True),
        sa.Column("consent_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("consent_source", sa.String(length=32), nullable=True),
        sa.Column("consent_channels", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("unsubscribed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("unsubscribe_token", sa.String(length=64), nullable=True),
        sa.Column("last_notified_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_series_followers_customer_id_customers",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["series.id"],
            name="fk_series_followers_series_id_series",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_series_followers_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_series_followers"),
        sa.UniqueConstraint(
            "tenant_id", "series_id", "contact_key", name="uq_series_followers_contact"
        ),
    )
    op.create_index(
        "ix_series_followers_series_source", "series_followers", ["series_id", "source"]
    )

    op.create_table(
        "series_releases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("series_id", sa.UUID(), nullable=False),
        sa.Column("series_item_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("insight_id", sa.UUID(), nullable=True),
        sa.Column("detected_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("approved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("sent_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("recipients", sa.Integer(), nullable=False),
        sa.Column("message_text", sa.Text(), nullable=True),
        sa.Column(
            "notified_contact_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
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
        sa.ForeignKeyConstraint(
            ["insight_id"],
            ["insights.id"],
            name="fk_series_releases_insight_id_insights",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["series.id"],
            name="fk_series_releases_series_id_series",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["series_item_id"],
            ["series_items.id"],
            name="fk_series_releases_series_item_id_series_items",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_series_releases_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["variants.id"],
            name="fk_series_releases_variant_id_variants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_series_releases"),
        sa.UniqueConstraint("tenant_id", "series_item_id", name="uq_series_releases_item"),
    )
    op.create_index(
        "ix_series_releases_tenant_detected", "series_releases", ["tenant_id", "detected_at"]
    )
