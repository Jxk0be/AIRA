"""Saving a contact, over HTTP, and then reading it back.

One narrow thing, for one reason: `get_session` hands a route a session and
never commits for it, so a write that does not commit itself is rolled back
when the request ends. Nothing about that failure looks like a failure. The
route returns 200 with the new row's id, because the INSERT really did run and
really did return one; the screen shows "saved"; the database is untouched.

The only way to catch it is to read the row back through a *different*
request, which is what this does. A service-level test would share the one
session and pass whether or not anything was committed.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.db import get_sessionmaker
from app.notify.tables import NotificationPref

TENANT = "tsundoku"
SETUP = "python tasks.py sources && python tasks.py seed && python tasks.py backfill"

# Nobody's real address, and undeliverable by design: RFC 2606 reserves
# `.example`, so a bug that somehow sent this for real still could not reach a
# person.
ADDRESS = "regression-test@aira.example"


@pytest.fixture
async def shop(db: AsyncSession) -> None:
    tenant = (
        await db.execute(select(t.Tenant).where(t.Tenant.slug == TENANT))
    ).scalar_one_or_none()
    if tenant is None:
        pytest.skip(f"{TENANT} does not exist; run: {SETUP}")


async def _forget(email: str) -> None:
    """Take the contact back out, whatever the test did."""
    async with get_sessionmaker()() as session:
        await session.execute(delete(NotificationPref).where(NotificationPref.email == email))
        await session.commit()


async def _stored(email: str) -> int:
    """Count straight from the database, in a session of its own."""
    async with get_sessionmaker()() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(NotificationPref)
                .where(NotificationPref.email == email)
            )
        ).scalar_one()


async def test_saving_a_contact_actually_saves_it(client: httpx.AsyncClient, shop: None) -> None:
    await _forget(ADDRESS)
    try:
        saved = await client.put(
            f"/tenants/{TENANT}/notifications",
            json={"email": ADDRESS, "name": "Regression test"},
        )
        assert saved.status_code == 200, saved.text

        # The row is there in the database, not just in the response.
        assert await _stored(ADDRESS) == 1, (
            "the API returned an id but nothing was written — the write did not commit"
        )

        # And a later request can see it, which is what the screen does.
        listed = await client.get(f"/tenants/{TENANT}/notifications")
        assert listed.status_code == 200, listed.text
        assert ADDRESS in [person["email"] for person in listed.json()]
    finally:
        await _forget(ADDRESS)


async def test_saving_the_same_address_twice_updates_rather_than_duplicates(
    client: httpx.AsyncClient, shop: None
) -> None:
    """The screen says as much under the form, so it had better be true."""
    await _forget(ADDRESS)
    try:
        first = await client.put(
            f"/tenants/{TENANT}/notifications", json={"email": ADDRESS, "name": "First"}
        )
        second = await client.put(
            f"/tenants/{TENANT}/notifications", json={"email": ADDRESS, "name": "Second"}
        )
        assert first.status_code == 200 and second.status_code == 200
        assert first.json() == second.json(), "the second save made a new contact"
        assert await _stored(ADDRESS) == 1

        listed = (await client.get(f"/tenants/{TENANT}/notifications")).json()
        mine = [person for person in listed if person["email"] == ADDRESS]
        assert len(mine) == 1
        assert mine[0]["name"] == "Second", "the update did not take"
    finally:
        await _forget(ADDRESS)
