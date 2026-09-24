"""HTTP for retrieval: upload a document, see what search returns.

Debugging surface, and the two endpoints the Data & sync screen in phase 10
will call. There is no authentication yet, so the tenant is named in the path
and resolved to an id here — never taken from the client as an id, and never
interpolated into SQL. Real auth, and Postgres row-level security as a second
guard, arrive with deployment.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.db import get_session
from app.rag import documents as docs
from app.rag.embeddings import EmbeddingError, shared_embedder
from app.rag.ingest import ingest_tenant
from app.rag.search import SearchHit, SearchMode, search

router = APIRouter(tags=["retrieval"])

# Uploads are policies and FAQs, not media. Anything much bigger than this is a
# mistake, and reading it into memory to embed it would be a worse one.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class Hit(BaseModel):
    chunk_id: uuid.UUID
    source: str
    source_id: uuid.UUID
    title: str
    content: str
    metadata: dict[str, Any]
    score: float
    vector_rank: int | None = None
    text_rank: int | None = None

    @classmethod
    def of(cls, hit: SearchHit) -> Hit:
        return cls(
            chunk_id=hit.chunk_id,
            source=hit.source,
            source_id=hit.source_id,
            title=hit.title,
            content=hit.content,
            metadata=hit.metadata,
            score=hit.score,
            vector_rank=hit.vector_rank,
            text_rank=hit.text_rank,
        )


class SearchResponse(BaseModel):
    tenant: str
    query: str
    mode: SearchMode
    model: str
    hits: list[Hit]


class DocumentResponse(BaseModel):
    document_id: uuid.UUID
    tenant: str
    title: str
    filename: str
    created: bool
    chunks_embedded: int = Field(description="Chunks embedded by the ingest this upload triggered")
    tokens: int


async def _tenant(session: AsyncSession, slug: str) -> t.Tenant:
    tenant = (
        await session.execute(
            select(t.Tenant).where(t.Tenant.slug == slug, t.Tenant.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"no tenant {slug!r}")
    return tenant


@router.get("/tenants/{slug}/search", response_model=SearchResponse)
async def search_chunks(
    slug: str,
    q: Annotated[str, Query(min_length=1, description="what to search for")],
    session: Annotated[AsyncSession, Depends(get_session)],
    k: Annotated[int, Query(ge=1, le=50)] = 10,
    mode: SearchMode = SearchMode.HYBRID,
    kind: Annotated[str | None, Query(description="product | document")] = None,
) -> SearchResponse:
    """Run a query the way the agent will run it.

    `mode` exists so the three retrieval strategies can be compared by hand on
    a real question, which is usually faster than reading the eval CSV.
    """
    tenant = await _tenant(session, slug)
    embedder = shared_embedder()
    try:
        hits = await search(
            session,
            tenant.id,
            q,
            embedder,
            k=k,
            filters={"kind": kind} if kind else None,
            mode=mode,
        )
    except EmbeddingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return SearchResponse(
        tenant=tenant.slug,
        query=q,
        mode=mode,
        model=embedder.name,
        hits=[Hit.of(hit) for hit in hits],
    )


@router.post("/tenants/{slug}/documents", response_model=DocumentResponse, status_code=201)
async def upload_document(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File(description=".md, .txt or .pdf")],
) -> DocumentResponse:
    """Store a policy, FAQ or schedule, and make it searchable immediately.

    The ingest that follows only embeds this document's own passages: every
    other chunk hashes the same as it did a moment ago and is left alone.
    """
    tenant = await _tenant(session, slug)
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{file.filename} is {len(raw):,} bytes; the limit is {MAX_UPLOAD_BYTES:,}",
        )

    try:
        loaded = docs.read_bytes(file.filename or "document.txt", raw)
    except docs.UnsupportedDocument as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    document_id, created = await docs.store(session, tenant.id, loaded)
    await session.flush()

    try:
        report = await ingest_tenant(session, tenant, shared_embedder())
    except EmbeddingError as exc:
        await session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    await session.commit()
    return DocumentResponse(
        document_id=document_id,
        tenant=tenant.slug,
        title=loaded.title,
        filename=loaded.filename,
        created=created,
        chunks_embedded=report.written,
        tokens=report.tokens,
    )
