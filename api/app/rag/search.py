"""Finding the right chunk.

The fusion itself lives in the `hybrid_search` SQL function, so the dashboard,
the agent and the eval script cannot drift apart on what "search" means. This
module embeds the query, calls it, and hands back typed hits.

Vector-only and text-only are here too, and not because anyone should ship
them: the eval script needs all three to show that hybrid is actually earning
its keep. They matter in opposite cases — an embedding finds "something cosy to
play with my kids" and misses "MNG-SS-04", and full-text does the reverse.

`tenant_id` is a parameter this module binds, never something a caller pastes
into SQL, and `embedding_model` is filtered on every path: vectors from
different models are not comparable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.embeddings import Embedder


class SearchMode(StrEnum):
    HYBRID = "hybrid"
    VECTOR = "vector"
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: uuid.UUID
    source: str
    source_id: uuid.UUID
    chunk_index: int
    content: str
    metadata: dict[str, Any]
    score: float
    # Where each half of the hybrid ranked this chunk, or None when that half
    # did not return it at all. Worth surfacing: it is the difference between
    # "both agreed" and "one of them insisted".
    vector_rank: int | None = None
    text_rank: int | None = None

    @property
    def title(self) -> str:
        meta = self.metadata or {}
        return str(meta.get("name") or meta.get("title") or self.content.split("\n", 1)[0])


def vector_literal(vector: Sequence[float]) -> str:
    """pgvector's text form.

    Passed as text and cast in the statement rather than registered as an
    asyncpg codec: one fewer piece of driver setup to get wrong, and the cast
    is visible in the SQL.
    """
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


HYBRID_SQL = """
select id, source, source_id, chunk_index, content, metadata, score, vector_rank, text_rank
from hybrid_search(
    :tenant, cast(:embedding as vector), :query, :model, :k, cast(:filters as jsonb)
)
"""

VECTOR_SQL = """
select c.id, c.source::text as source, c.source_id, c.chunk_index, c.content, c.metadata,
       1 - (c.embedding <=> cast(:embedding as vector)) as score
from chunks c
where c.tenant_id = :tenant
  and c.embedding_model = :model
  and c.metadata @> cast(:filters as jsonb)
order by c.embedding <=> cast(:embedding as vector)
limit :k
"""

# Every word first, any word only when that finds nothing — the same rule
# `hybrid_search` applies inside, so text-only mode measures the half that is
# actually in the fusion rather than a different one. The migration that
# introduced it explains why it is not simply ORed.
TEXT_SQL = """
with strict_query as (
    select websearch_to_tsquery('english', :query) as q
),
query as (
    select case
        when exists (
            select 1 from chunks c, strict_query
            where c.tenant_id = :tenant
              and c.embedding_model = :model
              and c.metadata @> cast(:filters as jsonb)
              and c.fts @@ strict_query.q
        )
        then (select q from strict_query)
        else replace((select q from strict_query)::text, ' & ', ' | ')::tsquery
    end as q
)
select c.id, c.source::text as source, c.source_id, c.chunk_index, c.content, c.metadata,
       ts_rank(c.fts, query.q) as score
from chunks c, query
where c.tenant_id = :tenant
  and c.embedding_model = :model
  and c.metadata @> cast(:filters as jsonb)
  and c.fts @@ query.q
order by score desc
limit :k
"""


async def search(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    query: str,
    embedder: Embedder,
    *,
    k: int = 10,
    filters: dict[str, Any] | None = None,
    mode: SearchMode = SearchMode.HYBRID,
    query_vector: Sequence[float] | None = None,
) -> list[SearchHit]:
    """Search one tenant's chunks.

    `filters` is a containment match against chunk metadata, e.g.
    `{"kind": "product"}` or `{"category": "Manga"}`.

    `query_vector` is for callers that already embedded this query — the eval
    script runs every question through three modes and would otherwise pay for
    the same vector three times.
    """
    query = (query or "").strip()
    if not query:
        return []

    params: dict[str, Any] = {
        "tenant": tenant_id,
        "query": query,
        "model": embedder.name,
        "k": k,
        "filters": json.dumps(filters or {}),
    }

    if mode is SearchMode.TEXT:
        # No embedding call at all: text-only is the one path that needs no
        # vector, and the eval runs it hundreds of times.
        statement = TEXT_SQL
    else:
        vector = query_vector if query_vector is not None else await embedder.embed_query(query)
        params["embedding"] = vector_literal(vector)
        statement = HYBRID_SQL if mode is SearchMode.HYBRID else VECTOR_SQL

    rows = (await session.execute(text(statement), params)).all()
    return [
        SearchHit(
            chunk_id=row.id,
            source=row.source,
            source_id=row.source_id,
            chunk_index=row.chunk_index,
            content=row.content,
            metadata=row.metadata or {},
            score=float(row.score),
            vector_rank=getattr(row, "vector_rank", None),
            text_rank=getattr(row, "text_rank", None),
        )
        for row in rows
    ]
