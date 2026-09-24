"""Retrieval.

Everything a shop knows that is not a number: what a product is, what the
returns policy says, when the next draft night is. Numbers come from
`app.analytics`; this is the other half.

    embedder = get_embedder()
    await ingest_tenant(session, tenant, embedder)
    hits = await search(session, tenant.id, "something cosy for two players", embedder)

It reads the canonical `products`, `variants`, `inventory_levels` and
`documents` tables and writes `chunks`, and it never learns which platform a
tenant runs on (CLAUDE.md rule 1).
"""

from app.rag.chunking import split_text
from app.rag.documents import LoadedDocument, UnsupportedDocument, read_bytes, read_file
from app.rag.embeddings import (
    Embedder,
    EmbeddingError,
    LocalEmbedder,
    Usage,
    VoyageEmbedder,
    get_embedder,
    shared_embedder,
)
from app.rag.ingest import IngestReport, ingest_tenant, product_text
from app.rag.search import SearchHit, SearchMode, search

__all__ = [
    "Embedder",
    "EmbeddingError",
    "IngestReport",
    "LoadedDocument",
    "LocalEmbedder",
    "SearchHit",
    "SearchMode",
    "UnsupportedDocument",
    "Usage",
    "VoyageEmbedder",
    "get_embedder",
    "ingest_tenant",
    "product_text",
    "read_bytes",
    "read_file",
    "search",
    "shared_embedder",
    "split_text",
]
