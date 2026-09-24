"""The screens, without a browser.

Three things are checked, and none of them is about how the page looks.

The first is that the dashboard does not do its own arithmetic: every KPI it
returns has to equal what the semantic layer says, or the chart and the
sentence have started to drift.

The second is capability gating. Panel & Pawn has one location and one channel,
and the widgets that would split by either must come back unavailable with a
sentence, not available with a single meaningless bar.

The third is isolation. No path, query string or id can be made to return
another shop's rows.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import inventory_value, load_context, sales_summary
from app.canonical import tables as t

POS_SHOP = "tsundoku"
SPREADSHEET_SHOP = "panel_and_pawn"


async def synced(db: AsyncSession, slug: str) -> t.Tenant:
    """Skip rather than fail when a shop has not been backfilled locally."""
    tenant = (await db.execute(select(t.Tenant).where(t.Tenant.slug == slug))).scalar_one_or_none()
    if tenant is None:
        pytest.skip(f"{slug} does not exist; run `python tasks.py backfill {slug}`")
    orders = (
        await db.execute(
            select(func.count()).select_from(t.Order).where(t.Order.tenant_id == tenant.id)
        )
    ).scalar_one()
    if not orders:
        pytest.skip(f"{slug} has not been synced; run `python tasks.py backfill {slug}`")
    return tenant


async def test_tenants_are_listed_with_their_capabilities(
    db: AsyncSession, client: httpx.AsyncClient
) -> None:
    await synced(db, POS_SHOP)
    response = await client.get("/tenants")
    assert response.status_code == 200

    shops = {row["tenant"]: row for row in response.json()}
    assert POS_SHOP in shops
    # The switcher needs these to decide what to even offer.
    assert set(shops[POS_SHOP]["capabilities"]) >= {"has_costs", "has_customers", "multi_location"}


async def test_kpis_are_the_semantic_layer_s_numbers(
    db: AsyncSession, client: httpx.AsyncClient
) -> None:
    """The dashboard reports; it does not compute."""
    await synced(db, POS_SHOP)
    ctx = await load_context(db, POS_SHOP)
    period = ctx.last_days(30)
    expected = await sales_summary(db, ctx, period)
    stock = await inventory_value(db, ctx)

    response = await client.get(f"/tenants/{POS_SHOP}/dashboard", params={"days": 30})
    assert response.status_code == 200
    body = response.json()

    assert body["period"]["start"] == period.start.isoformat()
    assert body["period"]["end"] == period.end.isoformat()

    kpis = {row["key"]: row for row in body["kpis"]}
    assert Decimal(kpis["net_sales"]["value"]) == expected.net_sales
    assert Decimal(kpis["order_count"]["value"]) == expected.order_count
    assert Decimal(kpis["average_order_value"]["value"]) == expected.average_order_value
    assert Decimal(kpis["inventory_value"]["value"]) == stock.retail_value

    # Stock on hand is a snapshot, so it has nothing to be compared against.
    assert kpis["inventory_value"]["previous"] is None
    assert kpis["inventory_value"]["change"] is None
    # Every KPI carries its own definition, which is what the hover shows.
    assert all(row["definition"] for row in body["kpis"])


async def test_widgets_a_shop_cannot_support_say_why(
    db: AsyncSession, client: httpx.AsyncClient
) -> None:
    await synced(db, SPREADSHEET_SHOP)
    response = await client.get(f"/tenants/{SPREADSHEET_SHOP}/dashboard")
    assert response.status_code == 200
    body = response.json()

    for widget in ("by_location", "by_channel"):
        assert body[widget]["available"] is False, widget
        assert body[widget]["data"] is None
        # Written for the owner, not for us.
        assert "Panel & Pawn" in body[widget]["reason"]

    # What it can do still arrives.
    assert body["top_products"]["available"] is True
    assert body["top_products"]["data"]["rows"]


async def test_multi_location_shop_gets_the_split(
    db: AsyncSession, client: httpx.AsyncClient
) -> None:
    await synced(db, POS_SHOP)
    body = (await client.get(f"/tenants/{POS_SHOP}/dashboard")).json()
    assert body["by_location"]["available"] is True
    assert body["by_channel"]["available"] is True


async def test_inventory_searches_sorts_and_pages(
    db: AsyncSession, client: httpx.AsyncClient
) -> None:
    await synced(db, POS_SHOP)

    everything = (await client.get(f"/tenants/{POS_SHOP}/inventory", params={"limit": 5})).json()
    assert everything["total"] > len(everything["rows"]) == 5

    # The count is of the whole catalogue, not of the page: "5 of 333".
    second = (
        await client.get(f"/tenants/{POS_SHOP}/inventory", params={"limit": 5, "offset": 5})
    ).json()
    assert second["total"] == everything["total"]
    first_ids = {row["variant_id"] for row in everything["rows"]}
    assert not first_ids & {row["variant_id"] for row in second["rows"]}

    dearest = (
        await client.get(
            f"/tenants/{POS_SHOP}/inventory", params={"sort": "price", "desc": True, "limit": 10}
        )
    ).json()
    prices = [Decimal(row["price"]) for row in dearest["rows"] if row["price"] is not None]
    assert prices == sorted(prices, reverse=True)

    hits = (await client.get(f"/tenants/{POS_SHOP}/inventory", params={"q": "sleeve"})).json()
    assert hits["total"] >= 1
    assert all("sleeve" in row["label"].lower() for row in hits["rows"])


async def test_inventory_refuses_a_sort_we_did_not_write(client: httpx.AsyncClient) -> None:
    """Sorting is a whitelist. Anything else is rejected before it reaches SQL."""
    response = await client.get(
        f"/tenants/{POS_SHOP}/inventory", params={"sort": "price; drop table orders"}
    )
    assert response.status_code == 422


async def test_margin_is_blank_rather_than_zero_without_a_cost(
    db: AsyncSession, client: httpx.AsyncClient
) -> None:
    await synced(db, SPREADSHEET_SHOP)
    body = (
        await client.get(f"/tenants/{SPREADSHEET_SHOP}/inventory", params={"limit": 100})
    ).json()

    uncosted = [row for row in body["rows"] if row["cost"] is None]
    assert uncosted, "this shop is supposed to be missing costs"
    assert all(row["unit_margin"] is None for row in uncosted)
    assert any(c["code"] == "missing_cost" for c in body["caveats"])


async def test_inventory_never_crosses_tenants(db: AsyncSession, client: httpx.AsyncClient) -> None:
    await synced(db, POS_SHOP)
    await synced(db, SPREADSHEET_SHOP)

    mine = (await client.get(f"/tenants/{POS_SHOP}/inventory", params={"limit": 200})).json()
    theirs = (
        await client.get(f"/tenants/{SPREADSHEET_SHOP}/inventory", params={"limit": 200})
    ).json()
    assert not {row["variant_id"] for row in mine["rows"]} & {
        row["variant_id"] for row in theirs["rows"]
    }


async def test_pinning_a_chart_round_trips(db: AsyncSession, client: httpx.AsyncClient) -> None:
    await synced(db, POS_SHOP)
    spec = {
        "type": "bar",
        "title": "Net sales by category",
        "x": "label",
        "y": ["net_sales"],
        "data": [{"label": "Manga", "net_sales": "1234.56"}],
    }

    created = await client.post(f"/tenants/{POS_SHOP}/charts", json={"spec": spec})
    assert created.status_code == 201
    chart_id = created.json()["id"]
    assert created.json()["title"] == spec["title"]

    listed = (await client.get(f"/tenants/{POS_SHOP}/charts")).json()
    assert chart_id in {row["id"] for row in listed}

    # Another shop can neither see it nor delete it: scoped in the query, so
    # from over there it simply does not exist.
    others = (await client.get(f"/tenants/{SPREADSHEET_SHOP}/charts")).json()
    assert chart_id not in {row["id"] for row in others}
    assert (
        await client.delete(f"/tenants/{SPREADSHEET_SHOP}/charts/{chart_id}")
    ).status_code == 404

    assert (await client.delete(f"/tenants/{POS_SHOP}/charts/{chart_id}")).status_code == 204
    after = (await client.get(f"/tenants/{POS_SHOP}/charts")).json()
    assert chart_id not in {row["id"] for row in after}


async def test_pinning_rejects_a_chart_that_is_not_one(client: httpx.AsyncClient) -> None:
    response = await client.post(
        f"/tenants/{POS_SHOP}/charts",
        json={"spec": {"type": "sculpture", "title": "?", "x": "label", "y": [], "data": []}},
    )
    assert response.status_code == 422


async def test_data_screen_reports_the_connection(
    db: AsyncSession, client: httpx.AsyncClient
) -> None:
    await synced(db, POS_SHOP)
    body = (await client.get(f"/tenants/{POS_SHOP}/data")).json()

    assert [row["adapter"] for row in body["integrations"]] == ["registerone"]
    assert body["last_sync"]["status"] in {"succeeded", "failed", "running"}
    assert body["quality"] is not None
    # Findings are the owner-facing version, so every one has a sentence.
    assert all(finding["message"] for finding in body["quality"]["findings"])


async def test_unknown_tenant_is_a_404_everywhere(client: httpx.AsyncClient) -> None:
    for path in ("profile", "dashboard", "inventory", "charts", "data"):
        response = await client.get(f"/tenants/not-a-shop/{path}")
        assert response.status_code == 404, path
