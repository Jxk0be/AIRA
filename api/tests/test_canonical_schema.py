"""Structural guards on the canonical schema.

These are cheap to run and catch the mistakes that are expensive later: a table
that forgot tenant_id, a naive timestamp column, money stored as a float.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.db import Base

# Tables holding shop data that came from a customer system.
SOURCED_TABLES = {
    "locations",
    "categories",
    "products",
    "variants",
    "inventory_levels",
    "inventory_movements",
    "customers",
    "orders",
    "order_lines",
    "refunds",
    "refund_lines",
}

# Everything except `tenants` itself must be tenant-scoped.
UNSCOPED_TABLES = {"tenants"}


def test_every_table_but_tenants_has_a_non_null_tenant_id() -> None:
    for name, table in Base.metadata.tables.items():
        if name in UNSCOPED_TABLES:
            continue
        assert "tenant_id" in table.c, f"{name} has no tenant_id"
        assert not table.c.tenant_id.nullable, f"{name}.tenant_id is nullable"


def test_sourced_tables_are_identified_by_tenant_source_external_id() -> None:
    for name in SOURCED_TABLES:
        table = Base.metadata.tables[name]
        assert "source" in table.c and "external_id" in table.c, name
        assert "deleted_at" in table.c, f"{name} must support soft deletes"
        identities = {
            tuple(c.name for c in uc.columns)
            for uc in table.constraints
            if uc.__class__.__name__ == "UniqueConstraint"
        }
        assert ("tenant_id", "source", "external_id") in identities, (
            f"{name} is missing the (tenant_id, source, external_id) unique constraint"
        )


def test_every_timestamp_column_is_timezone_aware() -> None:
    for name, table in Base.metadata.tables.items():
        for column in table.c:
            type_name = type(column.type).__name__
            if "TIMESTAMP" in type_name.upper() and type_name != "TSVECTOR":
                assert getattr(column.type, "timezone", False), (
                    f"{name}.{column.name} is a naive timestamp"
                )


def test_money_columns_are_exact_decimals() -> None:
    money_columns = [
        ("variants", "price"),
        ("variants", "cost"),
        ("orders", "subtotal"),
        ("orders", "total"),
        ("order_lines", "unit_price"),
        ("order_lines", "unit_cost_snapshot"),
        ("refunds", "amount"),
    ]
    for table_name, column_name in money_columns:
        column = Base.metadata.tables[table_name].c[column_name]
        assert type(column.type).__name__ == "Numeric", f"{table_name}.{column_name} is not NUMERIC"


def test_order_line_variant_is_nullable_for_custom_amount_sales() -> None:
    # "Misc singles" rung up as a bare price has no catalog item behind it.
    assert Base.metadata.tables["order_lines"].c.variant_id.nullable


def test_chunk_identity_includes_the_chunk_index() -> None:
    # A document splits into many passages; identity without chunk_index would
    # allow only one of them.
    identities = {
        tuple(c.name for c in uc.columns)
        for uc in Base.metadata.tables["chunks"].constraints
        if uc.__class__.__name__ == "UniqueConstraint"
    }
    assert ("tenant_id", "source", "source_id", "chunk_index") in identities


def test_embedding_dimension_stays_under_the_hnsw_ceiling() -> None:
    # pgvector's HNSW index cannot index a plain vector column above 2,000 dims.
    assert t.EMBEDDING_DIM <= 2000


# ---------------------------------------------------------------------------
# Live-database checks
# ---------------------------------------------------------------------------


def _vector(hot_index: int, dim: int = 1024) -> str:
    return "[" + ",".join("1" if i == hot_index else "0" for i in range(dim)) + "]"


@pytest.fixture
async def smoke_tenant(db: AsyncSession) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    await db.execute(
        text(
            "insert into tenants (id, slug, name, timezone, currency, settings) "
            "values (:id, :slug, 'Test Shop', 'America/New_York', 'USD', '{}')"
        ),
        {"id": tenant_id, "slug": f"test-{tenant_id.hex[:8]}"},
    )
    return tenant_id


async def _insert_chunk(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    content: str,
    hot_index: int,
    model: str = "voyage-4",
) -> None:
    await db.execute(
        text(
            "insert into chunks (id, tenant_id, source, source_id, chunk_index, content, "
            "content_hash, metadata, embedding_model, embedding, created_at, updated_at) "
            "values (:id, :tenant, 'product', :source_id, 0, :content, :hash, '{}'::jsonb, "
            ":model, cast(:embedding as vector), now(), now())"
        ),
        {
            "id": uuid.uuid4(),
            "tenant": tenant_id,
            "source_id": uuid.uuid4(),
            "content": content,
            "hash": uuid.uuid4().hex,
            "model": model,
            "embedding": _vector(hot_index),
        },
    )


async def test_pgvector_is_enabled(db: AsyncSession) -> None:
    version = (
        await db.execute(text("select extversion from pg_extension where extname = 'vector'"))
    ).scalar_one_or_none()
    assert version is not None, "the vector extension is not installed"


async def test_hybrid_search_ranks_a_double_hit_above_a_single_hit(
    db: AsyncSession, smoke_tenant: uuid.UUID
) -> None:
    """The whole point of fusion: a chunk both rankers like wins.

    The query vector is an exact match for the *first* chunk, while the query
    text only matches the second. Reciprocal rank fusion should still put the
    second one on top, because it places in both lists.
    """
    await _insert_chunk(db, smoke_tenant, "Chainsaw Man Vol. 3 manga paperback", hot_index=0)
    await _insert_chunk(db, smoke_tenant, "Store return policy: 14 days with receipt", hot_index=1)

    rows = (
        await db.execute(
            text(
                "select content, score, vector_rank, text_rank from hybrid_search("
                ":tenant, cast(:embedding as vector), :query, 'voyage-4', 5, '{}'::jsonb)"
            ),
            {
                "tenant": smoke_tenant,
                "embedding": _vector(0),
                "query": "return policy receipt",
            },
        )
    ).all()

    assert len(rows) == 2
    assert rows[0].content.startswith("Store return policy")
    assert rows[0].vector_rank is not None and rows[0].text_rank is not None
    assert rows[1].text_rank is None
    assert rows[0].score > rows[1].score


async def test_hybrid_search_never_crosses_tenants(
    db: AsyncSession, smoke_tenant: uuid.UUID
) -> None:
    await _insert_chunk(db, smoke_tenant, "Store return policy: 14 days", hot_index=0)

    rows = (
        await db.execute(
            text(
                "select count(*) from hybrid_search("
                ":tenant, cast(:embedding as vector), :query, 'voyage-4', 5, '{}'::jsonb)"
            ),
            {"tenant": uuid.uuid4(), "embedding": _vector(0), "query": "return policy"},
        )
    ).scalar_one()

    assert rows == 0


async def test_hybrid_search_never_mixes_embedding_models(
    db: AsyncSession, smoke_tenant: uuid.UUID
) -> None:
    """Vectors from two models are not comparable, so a search under one model
    must not see the other's rows — even for the same tenant."""
    await _insert_chunk(db, smoke_tenant, "Store return policy: 14 days", hot_index=0)

    rows = (
        await db.execute(
            text(
                "select count(*) from hybrid_search("
                ":tenant, cast(:embedding as vector), :query, 'bge-large-en-v1.5', 5, '{}'::jsonb)"
            ),
            {"tenant": smoke_tenant, "embedding": _vector(0), "query": "return policy"},
        )
    ).scalar_one()

    assert rows == 0


async def test_hybrid_search_applies_metadata_filters(
    db: AsyncSession, smoke_tenant: uuid.UUID
) -> None:
    await db.execute(
        text(
            "insert into chunks (id, tenant_id, source, source_id, chunk_index, content, "
            "content_hash, metadata, embedding_model, embedding, created_at, updated_at) "
            "values (:id, :tenant, 'product', :source_id, 0, 'Sleeves, 100 count', :hash, "
            "'{\"category\": \"supplies\"}'::jsonb, 'voyage-4', cast(:embedding as vector), "
            "now(), now())"
        ),
        {
            "id": uuid.uuid4(),
            "tenant": smoke_tenant,
            "source_id": uuid.uuid4(),
            "hash": uuid.uuid4().hex,
            "embedding": _vector(0),
        },
    )

    def count(filters: str) -> str:
        return (
            "select count(*) from hybrid_search(:tenant, cast(:embedding as vector), "
            f":query, 'voyage-4', 5, '{filters}'::jsonb)"
        )

    params = {"tenant": smoke_tenant, "embedding": _vector(0), "query": "sleeves"}
    matching = (await db.execute(text(count('{"category": "supplies"}')), params)).scalar_one()
    other = (await db.execute(text(count('{"category": "manga"}')), params)).scalar_one()

    assert matching == 1
    assert other == 0
