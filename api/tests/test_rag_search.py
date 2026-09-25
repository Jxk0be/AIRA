"""Search: the promises, not the ranking quality.

How good the results are is the eval script's job — `python tasks.py
eval-retrieval` measures hit@5 against real embeddings and a golden set. What
is checked here is everything that has to be true regardless of which model
made the vectors: one tenant never sees another's text, a vector is never
compared against one from a different model, and the lexical half answers a
question whose words are not all in the corpus.

Chunks are re-embedded with a fake provider inside the test transaction, so
these run offline and cost nothing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.rag.ingest import ingest_tenant
from app.rag.search import SearchMode, search
from tests.fake_embedder import FakeEmbedder


async def indexed(db: AsyncSession, slug: str, embedder: FakeEmbedder) -> t.Tenant:
    tenant = (await db.execute(select(t.Tenant).where(t.Tenant.slug == slug))).scalar_one_or_none()
    if tenant is None:
        pytest.skip(f"{slug} does not exist; run `python tasks.py backfill {slug}`")
    products = (
        await db.execute(
            select(func.count()).select_from(t.Product).where(t.Product.tenant_id == tenant.id)
        )
    ).scalar_one()
    if not products:
        pytest.skip(f"{slug} has no products; run `python tasks.py backfill {slug}`")
    await ingest_tenant(db, tenant, embedder)
    return tenant


@pytest.fixture
async def embedder() -> FakeEmbedder:
    return FakeEmbedder()


async def test_search_only_ever_returns_the_tenant_asked_for(
    db: AsyncSession, embedder: FakeEmbedder
) -> None:
    """Rule 3 again, on the table where a mistake would be most visible: a
    comic shop's board games turning up in an anime shop's assistant."""
    mine = await indexed(db, "animanga_knox", embedder)
    theirs = await indexed(db, "panel_and_pawn", embedder)

    their_products = {
        name
        for (name,) in (
            await db.execute(select(t.Product.name).where(t.Product.tenant_id == theirs.id))
        ).all()
    }

    # Words both shops use: card sleeves and storage sit on both counters, so a
    # missing tenant filter would show up immediately rather than by luck.
    for mode in SearchMode:
        hits = await search(db, mine.id, "card sleeves and storage", embedder, k=10, mode=mode)
        assert hits, f"{mode} found nothing at all"
        assert not {hit.title for hit in hits} & their_products
        ids = {hit.chunk_id for hit in hits}
        owners = (
            (await db.execute(select(t.Chunk.tenant_id).where(t.Chunk.id.in_(ids)).distinct()))
            .scalars()
            .all()
        )
        assert owners == [mine.id]


async def test_a_vector_from_another_model_is_never_compared(
    db: AsyncSession, embedder: FakeEmbedder
) -> None:
    """The corpus was embedded by one model; asking with another finds nothing
    rather than finding nonsense.

    Cosine distance between two different embedding spaces is not a weaker
    answer, it is a meaningless one, and silently returning it would look like
    search working badly rather than search being misconfigured.
    """
    tenant = await indexed(db, "animanga_knox", embedder)

    assert await search(db, tenant.id, "manga", embedder, k=5)
    stranger = FakeEmbedder(model="some-other-model")
    assert await search(db, tenant.id, "manga", stranger, k=5) == []


async def test_a_question_whose_words_are_not_all_there_still_finds_something(
    db: AsyncSession, embedder: FakeEmbedder
) -> None:
    """`websearch_to_tsquery` ANDs its terms, so one absent word is enough to
    return nothing at all. The fallback to any-word is what makes the lexical
    half work on a question rather than on a search box."""
    tenant = await indexed(db, "animanga_knox", embedder)

    # "supercalifragilistic" is in nothing, so every-word matching cannot work.
    hits = await search(
        db, tenant.id, "supercalifragilistic manga paperback", embedder, k=5, mode=SearchMode.TEXT
    )
    assert hits, "the text half gave up instead of falling back"


async def test_exact_terms_still_beat_the_fallback(
    db: AsyncSession, embedder: FakeEmbedder
) -> None:
    """The fallback only applies when the strict query matches nothing: a real
    phrase must still be matched strictly, or the lexical half becomes noise
    that drowns the vector half in the fusion."""
    tenant = await indexed(db, "animanga_knox", embedder)

    hits = await search(db, tenant.id, "Jujutsu Kaisen", embedder, k=5, mode=SearchMode.TEXT)

    assert hits
    assert all("jujutsu kaisen" in hit.content.lower() for hit in hits)


async def test_results_can_be_narrowed_to_documents(
    db: AsyncSession, embedder: FakeEmbedder
) -> None:
    """The metadata filter is a jsonb containment match, and the agent will use
    it to keep a policy question out of the catalogue."""
    tenant = await indexed(db, "animanga_knox", embedder)
    documents = (
        await db.execute(
            select(func.count())
            .select_from(t.Document)
            .where(t.Document.tenant_id == tenant.id, t.Document.deleted_at.is_(None))
        )
    ).scalar_one()
    if not documents:
        pytest.skip("no documents uploaded; run `python tasks.py documents`")

    hits = await search(
        db, tenant.id, "returns policy", embedder, k=5, filters={"kind": "document"}
    )

    assert hits
    assert all(hit.source == "document" for hit in hits)


async def test_an_empty_query_costs_nothing(db: AsyncSession, embedder: FakeEmbedder) -> None:
    tenant = await indexed(db, "animanga_knox", embedder)
    before = embedder.query_calls

    assert await search(db, tenant.id, "   ", embedder) == []
    assert embedder.query_calls == before


async def test_hybrid_reports_which_half_found_each_hit(
    db: AsyncSession, embedder: FakeEmbedder
) -> None:
    """The ranks are what make a surprising result explainable."""
    tenant = await indexed(db, "animanga_knox", embedder)

    hits = await search(db, tenant.id, "booster box", embedder, k=10)

    assert hits
    assert all(hit.vector_rank is not None or hit.text_rank is not None for hit in hits)
    assert any(hit.text_rank is not None for hit in hits), "the text half contributed nothing"
    # Fused scores come back in order, best first.
    assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)
