"""Async SQLAlchemy engine and session factory.

Rule 6: we talk to Supabase Postgres over DATABASE_URL with SQLAlchemy 2 async,
not supabase-py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

from sqlalchemy import MetaData, Table
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

# Predictable constraint and index names keep Alembic migrations readable and
# make them reversible without hand-written names everywhere.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every canonical table."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def table_of(model: type[Base]) -> Table:
    """The Table behind a declarative model.

    SQLAlchemy types `Model.__table__` as the looser `FromClause`; for our
    models it is always a `Table`, and Core inserts and updates need that.
    """
    return cast(Table, model.__table__)


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with get_sessionmaker()() as session:
        yield session


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
