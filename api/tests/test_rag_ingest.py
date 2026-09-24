"""Ingest: what gets embedded, and more importantly what does not.

Embedding is the one part of this system that costs money per run, and ingest
is wired to run after every sync. The property that makes that safe is that an
ingest with nothing to do makes no calls at all, and these tests hold it to
that with a fake provider that counts.

Everything here runs inside the test transaction and is rolled back, so it
overwrites the real chunks for the length of a test and leaves them untouched.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.rag.ingest import build_product_chunks, ingest_tenant, product_text
from tests.fake_embedder import FakeEmbedder

TENANTS = ("tsundoku", "panel_and_pawn")


async def tenant_named(db: AsyncSession, slug: str) -> t.Tenant:
    found = (await db.execute(select(t.Tenant).where(t.Tenant.slug == slug))).scalar_one_or_none()
    if found is None:
        pytest.skip(f"{slug} does not exist; run `python tasks.py backfill {slug}`")
    products = (
        await db.execute(
            select(func.count()).select_from(t.Product).where(t.Product.tenant_id == found.id)
        )
    ).scalar_one()
    if not products:
        pytest.skip(f"{slug} has no products; run `python tasks.py backfill {slug}`")
    return found


@pytest.fixture(params=TENANTS)
async def tenant(db: AsyncSession, request: pytest.FixtureRequest) -> t.Tenant:
    return await tenant_named(db, request.param)


# ---------------------------------------------------------------------------
# What a chunk says
# ---------------------------------------------------------------------------


def test_a_product_with_nothing_but_a_name_still_says_something() -> None:
    """Panel & Pawn's whole catalogue looks like this.

    A chunk of just "Harborline" retrieves almost nothing. The category, the
    price and the shelf are what make "what board games do you have" work on an
    export with no description column at all.
    """
    thin = product_text(
        {"name": "Harborline", "category": "Board Games", "description": None, "vendor_name": None},
        [{"name": None, "sku": None, "price": None}],
        [{"location": "Panel & Pawn", "on_hand": 4}],
    )

    assert "Harborline" in thin
    assert "Board Games" in thin
    assert "4 at Panel & Pawn" in thin


def test_a_chunk_carries_the_sku_for_the_text_side_to_find() -> None:
    """An embedding turns a part number into mush; full-text does not."""
    rich = product_text(
        {
            "name": "Saltwater Samurai Vol. 4",
            "category": "Manga",
            "description": "Volume 4, paperback.",
            "vendor_name": None,
        },
        [{"name": "Paperback", "sku": "MNG-SS-04", "price": 12.99}],
        [],
    )

    assert "MNG-SS-04" in rich
    assert "$12.99" in rich


def test_stock_reads_like_a_person_wrote_it() -> None:
    """Decimal quantities come out of the database as "17.0000"."""
    line = product_text(
        {
            "name": "9-Pocket Binder",
            "category": "Supplies",
            "description": None,
            "vendor_name": None,
        },
        [{"name": None, "sku": None, "price": None}],
        [{"location": "Gay St", "on_hand": Decimal("17.0000")}],
    )
    assert "17 at Gay St" in line
    assert "17.0000" not in line


async def test_every_product_gets_exactly_one_chunk(db: AsyncSession, tenant: t.Tenant) -> None:
    chunks = await build_product_chunks(db, tenant.id)
    products = (
        await db.execute(
            select(func.count())
            .select_from(t.Product)
            .where(t.Product.tenant_id == tenant.id, t.Product.deleted_at.is_(None))
        )
    ).scalar_one()

    assert len(chunks) == products
    assert len({chunk.source_id for chunk in chunks}) == products
    assert all(chunk.content.strip() for chunk in chunks)


# ---------------------------------------------------------------------------
# What it costs to stay current
# ---------------------------------------------------------------------------


async def test_a_second_ingest_with_no_changes_embeds_nothing(
    db: AsyncSession, tenant: t.Tenant
) -> None:
    """The property that makes it safe to run after every sync.

    A shop syncing every fifteen minutes would otherwise re-embed its whole
    catalogue ninety-six times a day for nothing.
    """
    embedder = FakeEmbedder()
    first = await ingest_tenant(db, tenant, embedder)
    assert first.written > 0

    calls_after_first = embedder.document_calls
    second = await ingest_tenant(db, tenant, embedder)

    assert second.written == 0
    assert second.unchanged == first.written + first.unchanged
    assert second.requests == 0
    assert embedder.document_calls == calls_after_first, "it called the provider anyway"


async def test_only_the_product_that_changed_is_re_embedded(
    db: AsyncSession, tenant: t.Tenant
) -> None:
    embedder = FakeEmbedder()
    await ingest_tenant(db, tenant, embedder)

    product = (
        await db.execute(
            select(t.Product)
            .where(t.Product.tenant_id == tenant.id, t.Product.deleted_at.is_(None))
            .limit(1)
        )
    ).scalar_one()
    await db.execute(
        update(t.Product).where(t.Product.id == product.id).values(name=f"{product.name} (2nd ed.)")
    )

    after = await ingest_tenant(db, tenant, embedder)

    assert after.written == 1
    assert "2nd ed." in embedder.documents_embedded[-1]


async def test_changing_the_model_makes_every_chunk_stale(
    db: AsyncSession, tenant: t.Tenant
) -> None:
    """Vectors from two models cannot be compared, so the hash includes the
    model: swapping providers re-embeds rather than leaving a corpus half in
    one space and half in another."""
    await ingest_tenant(db, tenant, FakeEmbedder(model="first-model"))
    second = await ingest_tenant(db, tenant, FakeEmbedder(model="second-model"))

    assert second.unchanged == 0
    assert second.written > 0

    models = (
        (
            await db.execute(
                select(t.Chunk.embedding_model).where(t.Chunk.tenant_id == tenant.id).distinct()
            )
        )
        .scalars()
        .all()
    )
    assert models == ["second-model@1024"]


async def test_a_deleted_product_loses_its_chunk(db: AsyncSession, tenant: t.Tenant) -> None:
    """Soft-deleted upstream means gone from search, even though the row stays
    for the order lines that still point at it."""
    embedder = FakeEmbedder()
    await ingest_tenant(db, tenant, embedder)

    product = (
        await db.execute(
            select(t.Product)
            .where(t.Product.tenant_id == tenant.id, t.Product.deleted_at.is_(None))
            .limit(1)
        )
    ).scalar_one()
    await db.execute(
        update(t.Product).where(t.Product.id == product.id).values(deleted_at=func.now())
    )

    after = await ingest_tenant(db, tenant, embedder)

    assert after.deleted == 1
    remaining = (
        await db.execute(
            select(func.count())
            .select_from(t.Chunk)
            .where(t.Chunk.tenant_id == tenant.id, t.Chunk.source_id == product.id)
        )
    ).scalar_one()
    assert remaining == 0


async def test_documents_become_several_chunks_that_keep_their_title(
    db: AsyncSession, tenant: t.Tenant
) -> None:
    """A passage four screens into a returns policy that never repeats the word
    "returns" is unfindable without its title."""
    body = "\n\n".join(
        f"Section {i}. Sealed product is final sale once it leaves the shop, "
        "because we cannot resell a box that has been out of our sight." * 3
        for i in range(12)
    )
    document_id = uuid.uuid4()
    db.add(
        t.Document(
            id=document_id,
            tenant_id=tenant.id,
            title="Returns and exchanges",
            filename="returns-test.md",
            content=body,
            uploaded_at=func.now(),
        )
    )
    await db.flush()

    await ingest_tenant(db, tenant, FakeEmbedder())

    chunks = (
        (
            await db.execute(
                select(t.Chunk)
                .where(t.Chunk.tenant_id == tenant.id, t.Chunk.source_id == document_id)
                .order_by(t.Chunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )

    assert len(chunks) > 1
    assert all(chunk.content.startswith("Returns and exchanges") for chunk in chunks)
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))


async def test_one_tenant_ingest_leaves_the_other_alone(db: AsyncSession) -> None:
    """Rule 3, on the one table both shops' text ends up in."""
    mine = await tenant_named(db, "tsundoku")
    theirs = await tenant_named(db, "panel_and_pawn")

    before = (
        await db.execute(
            select(func.count()).select_from(t.Chunk).where(t.Chunk.tenant_id == theirs.id)
        )
    ).scalar_one()
    await ingest_tenant(db, mine, FakeEmbedder())
    after = (
        (
            await db.execute(
                select(t.Chunk.embedding_model).where(t.Chunk.tenant_id == theirs.id).distinct()
            )
        )
        .scalars()
        .all()
    )
    count = (
        await db.execute(
            select(func.count()).select_from(t.Chunk).where(t.Chunk.tenant_id == theirs.id)
        )
    ).scalar_one()

    assert count == before
    assert "fake-test-model@1024" not in after


async def test_ingest_leaves_no_orphan_chunks(db: AsyncSession, tenant: t.Tenant) -> None:
    """Every chunk points at something that still exists."""
    await ingest_tenant(db, tenant, FakeEmbedder())

    orphans = (
        await db.execute(
            text(
                """
                select count(*) from chunks c
                where c.tenant_id = :tenant
                  and (
                    (c.source = 'product' and not exists (
                        select 1 from products p
                        where p.id = c.source_id and p.deleted_at is null))
                    or (c.source = 'document' and not exists (
                        select 1 from documents d
                        where d.id = c.source_id and d.deleted_at is null))
                  )
                """
            ),
            {"tenant": tenant.id},
        )
    ).scalar_one()

    assert orphans == 0
