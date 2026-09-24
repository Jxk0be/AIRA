"""hybrid_search: fall back to any-word matching when every-word finds nothing

`websearch_to_tsquery` ANDs everything it is given. That is right for a search
box and wrong for the way people talk to an assistant: "how much do you pay for
single cards" becomes `'much' & 'pay' & 'singl' & 'card'`, the shop's FAQ does
not contain the word "much", and the half of hybrid search that exists to catch
exact wording returns nothing at all.

Simply ORing the terms instead is worse, and measurably so. It rescues those
questions but floods the lexical top 30 with passages that share one common
word, and because reciprocal rank fusion only looks at rank, that noise pushes
correct answers off the end of the list. On Panel & Pawn it took hybrid from
95% to 80%.

So: AND first, and only when that matches nothing at all, OR. Precision where
the words are really there, recall where they are not, and the fusion keeps
what it had.

Revision ID: d7e2f1a9c845
Revises: c3a71d24e90b
Create Date: 2026-09-23 23:12:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d7e2f1a9c845"
down_revision: str | None = "c3a71d24e90b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The `params` CTE is the only thing this revision changes. Everything below it
# is the function as it was.
STRICT_ONLY = """
        select
            websearch_to_tsquery('english', coalesce(p_query_text, '')) as q,
            coalesce(p_filters, '{}'::jsonb) as filters
"""

# `websearch_to_tsquery` renders as `'a' & 'b'`, so swapping the separator
# turns it into `'a' | 'b'`. Quoted phrases render with `<->` and survive
# untouched. A negated term (`-word` becomes `!'word'`) keeps its sense but
# stops narrowing the result, which is a fair trade in a fallback nobody
# reaches when their words are actually in the text.
WITH_FALLBACK = """
        select
            case
                when exists (
                    select 1
                    from public.chunks c
                    where c.tenant_id = p_tenant_id
                      and c.embedding_model = p_embedding_model
                      and c.metadata @> coalesce(p_filters, '{}'::jsonb)
                      and c.fts @@ websearch_to_tsquery('english', coalesce(p_query_text, ''))
                )
                then websearch_to_tsquery('english', coalesce(p_query_text, ''))
                else replace(
                    websearch_to_tsquery('english', coalesce(p_query_text, ''))::text,
                    ' & ',
                    ' | '
                )::tsquery
            end as q,
            coalesce(p_filters, '{}'::jsonb) as filters
"""


def _function(params_body: str) -> str:
    return f"""
create or replace function public.hybrid_search(
    p_tenant_id uuid,
    p_query_embedding vector(1024),
    p_query_text text,
    p_embedding_model text,
    p_k int default 10,
    p_filters jsonb default '{{}}'::jsonb
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
{params_body}
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


COMMENT = """
comment on function public.hybrid_search is
    'Tenant-scoped hybrid retrieval: top 30 by cosine distance and top 30 by '
    'ts_rank, fused with reciprocal rank fusion (k=60). The text half matches '
    'every query word, falling back to any word when that finds nothing. Both '
    'halves filter on tenant_id and embedding_model: vectors from different '
    'models are not comparable. p_filters is a jsonb containment filter '
    'against chunk metadata.';
"""


def upgrade() -> None:
    op.execute(_function(WITH_FALLBACK))
    op.execute(COMMENT)


def downgrade() -> None:
    op.execute(_function(STRICT_ONLY))
