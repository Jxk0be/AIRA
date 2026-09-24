from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db import dispose_engine
from app.main import app

# Errors that mean "the stack isn't running", as opposed to "the query is wrong".
CONNECTION_ERRORS = (OSError, ConnectionError, OperationalError, InterfaceError, DBAPIError)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A session against the local Supabase stack.

    Its own engine with NullPool, not the application's: pytest-asyncio gives
    each test a fresh event loop, and an asyncpg connection pooled on a loop
    that has since closed blows up in confusing ways when the next test
    borrows it.

    Skips rather than fails when the stack is down, so `task test` still runs
    the pure-Python tests on a laptop with no Docker running.
    """
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        try:
            await session.execute(text("select 1"))
        except CONNECTION_ERRORS as exc:
            pytest.skip(f"no database reachable ({type(exc).__name__}); run `python tasks.py db`")
        yield session
    finally:
        # Nothing a test writes is kept: every fixture row dies with the rollback.
        await session.rollback()
        await session.close()
        await engine.dispose()


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client wired straight into the app, with no server in between.

    The app's own engine is disposed afterwards. It pools connections on
    whichever event loop first opened them, pytest-asyncio hands every test a
    new loop, and a pooled asyncpg connection borrowed across that boundary
    fails inside SQLAlchemy's teardown with an error that names neither cause.
    """
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        yield c
    await dispose_engine()
