"""AIRA API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text

from app.config import get_settings
from app.db import dispose_engine, get_sessionmaker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


app = FastAPI(title="AIRA", version="0.1.0", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DatabaseHealth(BaseModel):
    reachable: bool
    error: str | None = None
    server_version: str | None = None
    pgvector: bool | None = None
    pgvector_version: str | None = None


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    service: str = "aira-api"
    version: str = "0.1.0"
    database: DatabaseHealth
    embedding_model: str
    embedding_dim: int


async def _check_database() -> DatabaseHealth:
    try:
        async with get_sessionmaker()() as session:
            version = (await session.execute(text("show server_version"))).scalar_one()
            vector_version = (
                await session.execute(
                    text("select extversion from pg_extension where extname = 'vector'")
                )
            ).scalar_one_or_none()
    except Exception as exc:
        return DatabaseHealth(reachable=False, error=f"{type(exc).__name__}: {exc}")

    return DatabaseHealth(
        reachable=True,
        server_version=str(version),
        pgvector=vector_version is not None,
        pgvector_version=vector_version,
    )


@app.get("/health", response_model=Health)
async def health() -> Health:
    db = await _check_database()
    healthy = db.reachable and bool(db.pgvector)
    return Health(
        status="ok" if healthy else "degraded",
        database=db,
        embedding_model=settings.embedding_model,
        embedding_dim=settings.embedding_dim,
    )


@app.get("/")
async def root() -> dict[str, Any]:
    return {"service": "aira-api", "docs": "/docs", "health": "/health"}
