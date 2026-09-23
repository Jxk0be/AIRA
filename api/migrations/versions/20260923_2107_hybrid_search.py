"""hybrid_search

Reciprocal rank fusion over pgvector cosine distance and Postgres full-text
search. Lives in SQL rather than Python so the whole fusion happens in one round
trip, and so the dashboard, the agent and the eval scripts cannot drift apart on
what "search" means.

Revision ID: b1f0c2d3e4a5
Revises: a85b168d8b31
Create Date: 2026-09-23 21:07:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b1f0c2d3e4a5"
down_revision: str | None = "a85b168d8b31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# RRF constant. 60 is the value from the original Cormack et al. paper and the
# one nearly every hybrid-search implementation uses; it damps the influence of
# any single ranker's top hit.
HYBRID_SEARCH_SQL = """
create or replace function public.hybrid_search(
    p_tenant_id uuid,
    p_query_embedding vector(1024),
    p_query_text text,
    p_embedding_model text,
    p_k int default 10,
    p_filters jsonb default '{}'::jsonb
)
returns table (
    id uuid,
    source text,
    source_id uuid,
    chunk_index int,
    content text,
    metadata jsonb,
    score double precision,
    vector_rank int,
    text_rank int
)
language sql
stable
set search_path = pg_catalog, public, extensions
as $$
    with params as (
        select
            websearch_to_tsquery('english', coalesce(p_query_text, '')) as q,
            coalesce(p_filters, '{}'::jsonb) as filters
    ),
    -- Top 30 by cosine distance. The inner order-by + limit is what lets the
    -- HNSW index do the work; ranking happens over the 30 rows that survive.
    vector_hits as (
        select s.id, row_number() over (order by s.distance) as rank
        from (
            select c.id, c.embedding <=> p_query_embedding as distance
            from public.chunks c, params
            where c.tenant_id = p_tenant_id
              and c.embedding_model = p_embedding_model
              and c.metadata @> params.filters
            order by c.embedding <=> p_query_embedding
            limit 30
        ) s
    ),
    -- Top 30 by text relevance. Catches exact SKUs, series names and numbers
    -- that embeddings blur together.
    text_hits as (
        select s.id, row_number() over (order by s.rank_score desc) as rank
        from (
            select c.id, ts_rank(c.fts, params.q) as rank_score
            from public.chunks c, params
            where c.tenant_id = p_tenant_id
              and c.embedding_model = p_embedding_model
              and c.metadata @> params.filters
              and params.q is not null
              and c.fts @@ params.q
            order by ts_rank(c.fts, params.q) desc
            limit 30
        ) s
    ),
    fused as (
        select
            coalesce(v.id, t.id) as id,
            (
                case when v.rank is not null then 1.0 / (60 + v.rank) else 0.0 end
                + case when t.rank is not null then 1.0 / (60 + t.rank) else 0.0 end
            )::double precision as score,
            v.rank::int as vector_rank,
            t.rank::int as text_rank
        from vector_hits v
        full outer join text_hits t on t.id = v.id
    )
    select
        c.id,
        c.source::text,
        c.source_id,
        c.chunk_index,
        c.content,
        c.metadata,
        f.score,
        f.vector_rank,
        f.text_rank
    from fused f
    join public.chunks c on c.id = f.id
    order by f.score desc, c.id
    limit greatest(coalesce(p_k, 10), 0);
$$;
"""

HYBRID_SEARCH_COMMENT_SQL = """
comment on function public.hybrid_search is
    'Tenant-scoped hybrid retrieval: top 30 by cosine distance and top 30 by '
    'ts_rank, fused with reciprocal rank fusion (k=60). Both halves filter on '
    'tenant_id and embedding_model: vectors from different models are not '
    'comparable. p_filters is a jsonb containment filter against chunk metadata.';
"""


def upgrade() -> None:
    # One statement per execute: asyncpg prepares each, and a prepared
    # statement cannot carry two commands.
    op.execute(HYBRID_SEARCH_SQL)
    op.execute(HYBRID_SEARCH_COMMENT_SQL)


def downgrade() -> None:
    op.execute("drop function if exists public.hybrid_search(uuid, vector, text, text, int, jsonb)")
