"""Building the searchable copy of a shop, and keeping it cheap to keep current.

Two kinds of thing get embedded: every product in the canonical catalogue, and
every document the shop uploaded. Both end up in `chunks`, which is the only
table search reads.

The part that matters operationally is what does *not* happen. Every chunk
stores a hash of its content plus the model that embedded it, so a re-ingest
after a sync that changed three prices embeds three chunks, not four hundred. A
no-op ingest makes zero embedding calls and costs nothing, which is what makes
it safe to run after every single sync.

Nothing here knows which POS a tenant runs: products and documents are
canonical tables (CLAUDE.md rule 1).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.canonical.enums import ChunkSource
from app.db import table_of
from app.rag.chunking import split_text
from app.rag.embeddings import Embedder, get_embedder

log = logging.getLogger(__name__)

# One chunk per product: a catalogue entry is already about one thing, and
# splitting it would put the price in a different vector from the name.
PRODUCT_CHUNK_INDEX = 0


@dataclass(frozen=True, slots=True)
class DesiredChunk:
    """A chunk as it ought to be, before we look at what is already stored."""

    source: ChunkSource
    source_id: uuid.UUID
    chunk_index: int
    content: str
    metadata: dict[str, Any]

    @property
    def key(self) -> tuple[str, uuid.UUID, int]:
        return (self.source.value, self.source_id, self.chunk_index)

    def content_hash(self, model: str) -> str:
        """Identity of "this text, embedded by this model".

        The model is inside the hash on purpose: change the model or the
        dimension and every chunk is stale by definition, and the next ingest
        re-embeds rather than leaving vectors that cannot be compared.
        """
        digest = hashlib.sha256()
        digest.update(model.encode())
        digest.update(b"\x00")
        digest.update(self.content.encode())
        return digest.hexdigest()


@dataclass
class IngestReport:
    tenant: str
    model: str
    products: int = 0
    documents: int = 0
    written: int = 0
    unchanged: int = 0
    deleted: int = 0
    requests: int = 0
    tokens: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def lines(self) -> list[str]:
        return [
            f"products {self.products:,}   documents {self.documents:,}",
            f"embedded {self.written:,}   unchanged {self.unchanged:,}   removed {self.deleted:,}",
            f"{self.requests:,} embedding requests, {self.tokens:,} tokens",
        ]


# --------------------------------------------------------------------------
# What a product reads like
# --------------------------------------------------------------------------


def _money(value: Decimal | None) -> str:
    return f"${value:,.2f}" if value is not None else ""


def _price_line(prices: list[Decimal], variable: bool) -> str:
    if not prices:
        return "Price: set at the register" if variable else ""
    low, high = min(prices), max(prices)
    if low == high:
        return f"Price: {_money(low)}"
    return f"Price: {_money(low)} to {_money(high)}"


def _variant_line(variants: list[dict[str, Any]]) -> str:
    """Names and SKUs.

    SKUs are here because full-text search is the half of hybrid retrieval that
    can find "MNG-SS-04" exactly; an embedding turns a part number into mush.
    """
    parts = []
    for variant in variants:
        name = variant["name"] or "Standard"
        parts.append(f"{name} ({variant['sku']})" if variant["sku"] else name)
    if not parts:
        return ""
    if len(parts) == 1 and not variants[0]["sku"]:
        return ""  # a single unnamed variant says nothing the title did not
    return "Variants: " + ", ".join(parts)


def _units(value: Decimal | int | float) -> str:
    """Whole numbers read as whole numbers.

    `Decimal("17.0000")` formats as "17.0000" under every format spec, and
    "17.0000 at Gay St" in a chunk is noise the embedding has to look past.
    """
    quantity = Decimal(str(value))
    if quantity == quantity.to_integral_value():
        return str(int(quantity))
    return str(quantity.normalize())


def _stock_line(stock: list[dict[str, Any]]) -> str:
    if not stock:
        return ""
    total = sum((row["on_hand"] for row in stock), Decimal("0"))
    if total <= 0:
        return "In stock: none left"
    where = ", ".join(f"{_units(row['on_hand'])} at {row['location']}" for row in stock)
    return f"In stock: {where}"


def product_text(
    product: dict[str, Any], variants: list[dict[str, Any]], stock: list[dict[str, Any]]
) -> str:
    """What gets embedded for one product.

    Written so a product with nothing but a name still says something worth
    matching. Panel & Pawn has no descriptions at all, so the category, the
    price and the shelf are what has to carry "what board games do you have".
    """
    prices = [v["price"] for v in variants if v["price"] is not None]
    lines = [
        product["name"],
        f"Category: {product['category']}" if product["category"] else "",
        f"Vendor: {product['vendor_name']}" if product["vendor_name"] else "",
        product["description"] or "",
        _price_line(prices, variable=any(v["price"] is None for v in variants)),
        _variant_line(variants),
        _stock_line(stock),
    ]
    return "\n".join(line for line in lines if line)


async def build_product_chunks(session: AsyncSession, tenant_id: uuid.UUID) -> list[DesiredChunk]:
    params = {"tenant": tenant_id}

    products = (
        await session.execute(
            text(
                """
                select p.id, p.name, p.description, p.product_type, p.vendor_name,
                       c.name as category
                from products p
                left join categories c on c.id = p.category_id
                where p.tenant_id = :tenant and p.deleted_at is null
                order by p.name
                """
            ),
            params,
        )
    ).all()

    variants: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in (
        await session.execute(
            text(
                """
                select v.product_id, v.name, v.sku, v.price
                from variants v
                join products p on p.id = v.product_id and p.deleted_at is null
                where v.tenant_id = :tenant and v.deleted_at is null
                order by v.name nulls first, v.sku nulls last
                """
            ),
            params,
        )
    ).all():
        variants[row.product_id].append({"name": row.name, "sku": row.sku, "price": row.price})

    stock: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in (
        await session.execute(
            text(
                """
                select v.product_id, l.name as location, sum(i.on_hand) as on_hand
                from inventory_levels i
                join variants v on v.id = i.variant_id and v.deleted_at is null
                join locations l on l.id = i.location_id
                where i.tenant_id = :tenant and i.deleted_at is null
                group by v.product_id, l.name
                order by l.name
                """
            ),
            params,
        )
    ).all():
        stock[row.product_id].append({"location": row.location, "on_hand": row.on_hand})

    chunks = []
    for row in products:
        product = dict(row._mapping)
        mine = variants[product["id"]]
        shelf = stock[product["id"]]
        prices = [v["price"] for v in mine if v["price"] is not None]
        chunks.append(
            DesiredChunk(
                source=ChunkSource.PRODUCT,
                source_id=product["id"],
                chunk_index=PRODUCT_CHUNK_INDEX,
                content=product_text(product, mine, shelf),
                metadata={
                    "kind": "product",
                    "name": product["name"],
                    "category": product["category"],
                    "in_stock": sum((r["on_hand"] for r in shelf), Decimal("0")) > 0,
                    "price": float(min(prices)) if prices else None,
                    "skus": [v["sku"] for v in mine if v["sku"]],
                },
            )
        )
    return chunks


async def build_document_chunks(session: AsyncSession, tenant_id: uuid.UUID) -> list[DesiredChunk]:
    """Uploaded prose, split into passages.

    Every passage keeps the document's title at the top of its text. Chunk four
    of a returns policy that never says "returns" in its own words is otherwise
    unfindable.
    """
    documents = (
        await session.execute(
            text(
                """
                select id, title, filename, content
                from documents
                where tenant_id = :tenant and deleted_at is null
                order by uploaded_at
                """
            ),
            {"tenant": tenant_id},
        )
    ).all()

    chunks = []
    for document in documents:
        passages = split_text(document.content)
        for index, passage in enumerate(passages):
            chunks.append(
                DesiredChunk(
                    source=ChunkSource.DOCUMENT,
                    source_id=document.id,
                    chunk_index=index,
                    content=f"{document.title}\n\n{passage}",
                    metadata={
                        "kind": "document",
                        "title": document.title,
                        "filename": document.filename,
                        "part": index + 1,
                        "parts": len(passages),
                    },
                )
            )
    return chunks


# --------------------------------------------------------------------------
# Writing them down
# --------------------------------------------------------------------------


async def _existing(
    session: AsyncSession, tenant_id: uuid.UUID
) -> dict[tuple[str, uuid.UUID, int], tuple[uuid.UUID, str]]:
    rows = (
        await session.execute(
            text(
                """
                select id, source, source_id, chunk_index, content_hash
                from chunks where tenant_id = :tenant
                """
            ),
            {"tenant": tenant_id},
        )
    ).all()
    return {
        (row.source, row.source_id, row.chunk_index): (row.id, row.content_hash) for row in rows
    }


async def _write(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    chunk: DesiredChunk,
    vector: list[float],
    model: str,
) -> None:
    table = table_of(t.Chunk)
    now = datetime.now(tz=UTC)
    statement = insert(table).values(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        source=chunk.source,
        source_id=chunk.source_id,
        chunk_index=chunk.chunk_index,
        content=chunk.content,
        content_hash=chunk.content_hash(model),
        metadata=chunk.metadata,
        embedding_model=model,
        embedding=vector,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["tenant_id", "source", "source_id", "chunk_index"],
            set_={
                "content": statement.excluded.content,
                "content_hash": statement.excluded.content_hash,
                "metadata": statement.excluded.metadata,
                "embedding_model": statement.excluded.embedding_model,
                "embedding": statement.excluded.embedding,
                "updated_at": now,
            },
        )
    )


async def ingest_tenant(
    session: AsyncSession,
    tenant: t.Tenant,
    embedder: Embedder | None = None,
    *,
    force: bool = False,
    batch_size: int = 128,
) -> IngestReport:
    """Bring a tenant's chunks in line with its catalogue and documents.

    Unchanged chunks are left alone, chunks whose source has gone are deleted,
    and only what is new or changed is embedded. `force` re-embeds everything,
    which is what `reembed` is for.
    """
    own_embedder = embedder is None
    worker = embedder or get_embedder()
    report = IngestReport(tenant=tenant.slug, model=worker.name)
    # A shared embedder counts across the whole process, so this run's cost is
    # the difference, not the total.
    spent_before = (worker.usage.requests, worker.usage.tokens)

    try:
        products = await build_product_chunks(session, tenant.id)
        documents = await build_document_chunks(session, tenant.id)
        desired = products + documents
        report.products = len(products)
        report.documents = len({chunk.source_id for chunk in documents})

        existing = await _existing(session, tenant.id)
        stale = [
            chunk
            for chunk in desired
            if force
            or chunk.key not in existing
            or existing[chunk.key][1] != chunk.content_hash(worker.name)
        ]
        report.unchanged = len(desired) - len(stale)

        for start in range(0, len(stale), batch_size):
            batch = stale[start : start + batch_size]
            vectors = await worker.embed_documents([chunk.content for chunk in batch])
            for chunk, vector in zip(batch, vectors, strict=True):
                await _write(session, tenant.id, chunk, vector, worker.name)
            report.written += len(batch)
            log.info("%s: embedded %d/%d chunks", tenant.slug, report.written, len(stale))

        # Whatever is left in the table but not in the catalogue: a deleted
        # product, a removed document, or a document that got shorter.
        wanted = {chunk.key for chunk in desired}
        chunks = table_of(t.Chunk)
        orphans = [row[0] for key, row in existing.items() if key not in wanted]
        if orphans:
            await session.execute(delete(chunks).where(chunks.c.id.in_(orphans)))
            report.deleted = len(orphans)

        report.requests = worker.usage.requests - spent_before[0]
        report.tokens = worker.usage.tokens - spent_before[1]
        return report
    finally:
        if own_embedder:
            await worker.aclose()
