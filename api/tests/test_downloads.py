"""The four files the app hands a person, fetched over HTTP.

These are the end of two features: the month-end packet exists so a bookkeeper
gets a PDF and a workbook, and the reorder assistant exists so an owner can put
a purchase order in front of a supplier. Everything upstream can be right and
the feature still be worth nothing if the download comes back as JSON.

They are grouped in their own file because they share one failure that no unit
test can see. Each download route spells its format as a dotted suffix on the
id -- `/month-end/{packet_id}.pdf` -- and a path parameter will happily swallow
that suffix. FastAPI matches routes in declaration order, so a plain
`/month-end/{packet_id}` declared above the download routes takes every request
for them, tries to read `"<uuid>.pdf"` as a UUID, and returns 422. The handlers
are never reached, and their own tests all still pass.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.db import get_sessionmaker
from app.reorder.tables import PurchaseOrder, PurchaseOrderLine

TENANT = "tsundoku"
SETUP = "python tasks.py sources && python tasks.py seed && python tasks.py backfill"

# What the first bytes of a real one of these looks like. A PDF reader and
# Excel both go by this rather than by the extension.
MAGIC = {
    "pdf": b"%PDF-",
    "xlsx": b"PK\x03\x04",
}


@pytest.fixture
async def tenant_id(db: AsyncSession) -> object:
    tenant = (
        await db.execute(select(t.Tenant).where(t.Tenant.slug == TENANT))
    ).scalar_one_or_none()
    if tenant is None:
        pytest.skip(f"{TENANT} does not exist; run: {SETUP}")
    orders = (
        await db.execute(
            select(func.count()).select_from(t.Order).where(t.Order.tenant_id == tenant.id)
        )
    ).scalar_one()
    if not orders:
        pytest.skip(f"{TENANT} has no sales synced; run: {SETUP}")
    return tenant.id


def _check(response: httpx.Response, kind: str, extension: str) -> None:
    assert response.status_code == 200, (
        f"{kind} came back {response.status_code}: {response.text[:200]}. "
        "A 422 here means a bare-id route is shadowing the download route -- "
        "the download routes have to be declared first."
    )
    body = response.content
    assert body, f"{kind} was empty"
    if extension in MAGIC:
        assert body.startswith(MAGIC[extension]), (
            f"{kind} does not start like a {extension}: {body[:8]!r}"
        )
    disposition = response.headers.get("content-disposition", "")
    assert f".{extension}" in disposition, (
        f"{kind} was not offered as a {extension} download: {disposition!r}"
    )


async def test_the_month_end_packet_downloads_as_both_files(
    client: httpx.AsyncClient, tenant_id: object
) -> None:
    """The bookkeeper's PDF and workbook.

    A bookkeeper handed a PDF of a table has been handed a worse version of
    nothing, so the workbook matters as much as the PDF does.
    """
    made = await client.post(f"/tenants/{TENANT}/month-end", json={})
    assert made.status_code == 201, made.text
    packet_id = made.json()["id"]

    for extension in ("pdf", "xlsx"):
        _check(
            await client.get(f"/tenants/{TENANT}/month-end/{packet_id}.{extension}"),
            "the month-end packet",
            extension,
        )

    # The route the downloads used to steal still answers.
    assert (await client.get(f"/tenants/{TENANT}/month-end/{packet_id}")).status_code == 200


async def test_a_purchase_order_downloads_as_a_pdf_and_a_csv(
    client: httpx.AsyncClient, tenant_id: object
) -> None:
    # Drafts are grouped per vendor and reused, so a second call on a fixture
    # that already has them returns nothing new. Any existing one exports the
    # same way, so take whatever is there.
    drafts = await client.post(f"/tenants/{TENANT}/reorder/drafts")
    assert drafts.status_code == 200, drafts.text
    ours = [uuid.UUID(str(draft)) for draft in drafts.json()]

    try:
        orders = await client.get(f"/tenants/{TENANT}/purchase-orders")
        assert orders.status_code == 200, orders.text
        rows = orders.json()
        if not rows:
            pytest.skip("nothing needs reordering in this fixture, so there is no PO to export")
        order_id = rows[0]["id"]

        for extension in ("pdf", "csv"):
            _check(
                await client.get(f"/tenants/{TENANT}/purchase-orders/{order_id}.{extension}"),
                "the purchase order",
                extension,
            )

        assert (
            await client.get(f"/tenants/{TENANT}/purchase-orders/{order_id}")
        ).status_code == 200
    finally:
        await _discard(ours)


async def _discard(order_ids: list[uuid.UUID]) -> None:
    """Take back the drafts this test raised.

    Unlike the `db` fixture, a request through `client` runs in the app's own
    session and commits, so anything this test creates outlives it. Leaving
    drafts behind is not harmless: the reorder assistant stops suggesting a
    variant that is already on an open order, which is correct behaviour and
    which quietly empties the planted "about to run out" scenario that
    `test_planted_scenarios` looks for afterwards.
    """
    if not order_ids:
        return
    async with get_sessionmaker()() as session:
        await session.execute(
            delete(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id.in_(order_ids))
        )
        await session.execute(delete(PurchaseOrder).where(PurchaseOrder.id.in_(order_ids)))
        await session.commit()


async def test_a_download_for_an_unknown_id_is_a_404_not_a_422(
    client: httpx.AsyncClient, tenant_id: object
) -> None:
    """The id is well formed and simply does not exist.

    Worth its own test: a 422 here would mean the routing is wrong again, and
    the difference between "no such packet" and "that is not a packet id" is
    the difference between a bug in the caller and a bug in us.
    """
    missing = "00000000-0000-4000-8000-000000000000"
    for path in (
        f"/tenants/{TENANT}/month-end/{missing}.pdf",
        f"/tenants/{TENANT}/month-end/{missing}.xlsx",
        f"/tenants/{TENANT}/purchase-orders/{missing}.pdf",
        f"/tenants/{TENANT}/purchase-orders/{missing}.csv",
    ):
        response = await client.get(path)
        assert response.status_code == 404, f"{path} came back {response.status_code}"
