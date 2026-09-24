"""The catalogue, as a shop owner browses it.

Not a metric — a listing. It lives here rather than in a route because it still
reads the canonical schema and still has to answer "what does this item make?",
and unit margin is exactly the kind of number that comes to mean two different
things the moment two screens work it out for themselves.

`gross_margin` in metrics.py is margin on what *sold*. `unit_margin` here is
margin on what is *on the shelf*: price against cost, per item, with no sales in
it at all. The two disagree for any shop whose discounts or mix move, and that
is not a bug in either — they answer different questions, so they have
different names.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, computed_field
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.context import AnalyticsContext
from app.analytics.queries import CATEGORY_TREE, Filters, fetch_all
from app.analytics.results import Caveat, money, share, units

NO_FILTERS = Filters()


class CatalogSort(StrEnum):
    """Sorting is never taken as text from the client.

    A name off this list picks a fragment we wrote; anything else is rejected
    before it reaches SQL.
    """

    NAME = "name"
    SKU = "sku"
    CATEGORY = "category"
    PRICE = "price"
    COST = "cost"
    MARGIN = "margin"
    ON_HAND = "on_hand"
    RETAIL_VALUE = "retail_value"
    LAST_SOLD = "last_sold"


SORT_SQL: dict[CatalogSort, str] = {
    CatalogSort.NAME: "p.name, coalesce(v.name, '')",
    CatalogSort.SKU: "v.sku",
    CatalogSort.CATEGORY: "c.name, p.name",
    CatalogSort.PRICE: "v.price",
    CatalogSort.COST: "v.cost",
    CatalogSort.MARGIN: (
        "case when v.price > 0 and v.cost is not null then (v.price - v.cost) / v.price end"
    ),
    CatalogSort.ON_HAND: "coalesce(sum(stock.on_hand), 0)",
    CatalogSort.RETAIL_VALUE: "coalesce(sum(stock.on_hand), 0) * coalesce(v.price, 0)",
    CatalogSort.LAST_SOLD: "max(last_sale.last_at)",
}


class StockAtLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_id: uuid.UUID
    location: str
    on_hand: Decimal


class CatalogRow(BaseModel):
    """One sellable item on the shelf."""

    model_config = ConfigDict(extra="forbid")

    variant_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    variant_name: str | None
    sku: str | None
    barcode: str | None
    category: str | None
    price: Decimal | None
    cost: Decimal | None
    # None when either side is missing — never 0, which reads as "makes nothing".
    unit_margin: Decimal | None
    units_on_hand: Decimal
    retail_value: Decimal
    stock: list[StockAtLocation]
    last_sold_at: datetime | None
    is_active: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def label(self) -> str:
        """What to print in one column.

        A single-variant product usually names its one variant after itself,
        and "Harborline — Harborline" in a table looks like a bug. Same rule as
        the breakdowns use, so an item reads the same everywhere.
        """
        if not self.variant_name or self.variant_name == self.product_name:
            return self.product_name
        return f"{self.product_name} — {self.variant_name}"


class CatalogPage(BaseModel):
    """A window onto the catalogue, plus how big the whole of it is.

    `total` counts every row matching the search rather than the page, because
    "50 of 1,240 items" is the only version of this a person can act on.
    """

    model_config = ConfigDict(extra="forbid")

    tenant: str
    currency: str
    timezone: str
    rows: list[CatalogRow]
    total: int
    limit: int
    offset: int
    caveats: list[Caveat] = []


def _search_clause(query: str | None, params: dict[str, Any]) -> str | None:
    """Match a name, a SKU, a barcode or a category, case-insensitively.

    One `ilike` over the fields a person would type, rather than the full-text
    search retrieval uses: someone looking at an inventory table is filtering
    it, and `websearch_to_tsquery` would not match half of "MNG-SS-0".
    """
    wanted = (query or "").strip()
    if not wanted:
        return None
    params["search"] = f"%{wanted}%"
    return (
        "(p.name ilike :search or v.name ilike :search or v.sku ilike :search "
        "or v.barcode ilike :search or c.name ilike :search)"
    )


async def catalog_page(
    session: AsyncSession,
    ctx: AnalyticsContext,
    *,
    query: str | None = None,
    sort: CatalogSort = CatalogSort.NAME,
    descending: bool = False,
    limit: int = 50,
    offset: int = 0,
    filters: Filters = NO_FILTERS,
    include_inactive: bool = False,
) -> CatalogPage:
    """Page through a shop's sellable items, with stock by location.

    A location filter narrows the stock counted, not the items listed: a shop
    asking what the con booth holds still wants to see that the other 900 items
    are all at the main store, showing zero.
    """
    params: dict[str, Any] = {"tenant": ctx.tenant_id, "limit": limit, "offset": offset}
    clauses = ["v.tenant_id = :tenant", "v.deleted_at is null", "p.deleted_at is null"]
    if not include_inactive:
        clauses.append("v.is_active")

    stock_clauses = ["i.tenant_id = :tenant", "i.deleted_at is null"]
    if filters.location_ids:
        params["location_ids"] = list(filters.location_ids)
        stock_clauses.append("i.location_id = any(:location_ids)")
    if filters.category_ids:
        params["category_ids"] = list(filters.category_ids)
        clauses.append("p.category_id in (select id from wanted_categories)")
    if filters.product_ids:
        params["product_ids"] = list(filters.product_ids)
        clauses.append("p.id = any(:product_ids)")
    if filters.variant_ids:
        params["variant_ids"] = list(filters.variant_ids)
        clauses.append("v.id = any(:variant_ids)")

    search = _search_clause(query, params)
    if search:
        clauses.append(search)

    caveats: list[Caveat] = []
    if filters.channels:
        caveats.append(
            Caveat(
                code="channel_filter_ignored_for_stock",
                message=(
                    "Stock is counted per location, not per channel, so the channel filter "
                    "does not apply to this list."
                ),
            )
        )

    # The category tree is itself a `with recursive`, so it opens the list when
    # it is there and the two CTEs below join it.
    opener = f"{CATEGORY_TREE.rstrip()},\n" if filters.category_ids else "with "

    statement = (
        opener
        + f"""
        stock as (
            select i.variant_id as variant_id,
                   i.location_id as location_id,
                   loc.name as location,
                   sum(i.on_hand) as on_hand
            from inventory_levels i
            join locations loc on loc.id = i.location_id
            where {" and ".join(stock_clauses)}
            group by 1, 2, 3
        ),
        last_sale as (
            select l.variant_id as variant_id, max(o.placed_at) as last_at
            from orders o
            join order_lines l on l.order_id = o.id and l.deleted_at is null
            where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
              and l.variant_id is not null
            group by 1
        )
        select v.id as variant_id,
               p.id as product_id,
               p.name as product_name,
               v.name as variant_name,
               v.sku as sku,
               v.barcode as barcode,
               c.name as category,
               v.price as price,
               v.cost as cost,
               v.is_active as is_active,
               coalesce(sum(stock.on_hand), 0) as on_hand,
               coalesce(
                   jsonb_agg(
                       jsonb_build_object(
                           'location_id', stock.location_id,
                           'location', stock.location,
                           'on_hand', stock.on_hand
                       )
                       order by stock.location
                   ) filter (where stock.location_id is not null),
                   '[]'::jsonb
               ) as by_location,
               max(last_sale.last_at) as last_sold_at,
               count(*) over () as total_rows
        from variants v
        join products p on p.id = v.product_id
        left join categories c on c.id = p.category_id
        left join stock on stock.variant_id = v.id
        left join last_sale on last_sale.variant_id = v.id
        where {" and ".join(clauses)}
        group by v.id, p.id, p.name, v.name, v.sku, v.barcode, c.name, v.price, v.cost, v.is_active
        order by {SORT_SQL[sort]} {"desc" if descending else "asc"} nulls last, p.name, v.id
        limit :limit offset :offset
        """
    )

    rows = await fetch_all(session, statement, params)

    out: list[CatalogRow] = []
    without_cost = 0
    for row in rows:
        on_hand = Decimal(row.on_hand)
        price = money(row.price) if row.price is not None else None
        cost = money(row.cost) if row.cost is not None else None
        if cost is None:
            without_cost += 1
        out.append(
            CatalogRow(
                variant_id=row.variant_id,
                product_id=row.product_id,
                product_name=row.product_name,
                variant_name=row.variant_name,
                sku=row.sku,
                barcode=row.barcode,
                category=row.category,
                price=price,
                cost=cost,
                unit_margin=share(price - cost, price) if price and cost is not None else None,
                units_on_hand=units(on_hand),
                retail_value=money(on_hand * (price or Decimal(0))),
                stock=[
                    StockAtLocation(
                        location_id=uuid.UUID(str(entry["location_id"])),
                        location=str(entry["location"]),
                        on_hand=units(Decimal(str(entry["on_hand"]))),
                    )
                    for entry in row.by_location
                ],
                last_sold_at=row.last_sold_at,
                is_active=row.is_active,
            )
        )

    if out and without_cost:
        caveats.append(
            Caveat(
                code="missing_cost",
                message=(
                    f"{without_cost} of the {len(out)} items on this page have no cost recorded, "
                    "so their margin is blank rather than zero."
                ),
            )
        )

    return CatalogPage(
        tenant=ctx.slug,
        currency=ctx.currency,
        timezone=ctx.timezone,
        rows=out,
        total=int(rows[0].total_rows) if rows else 0,
        limit=limit,
        offset=offset,
        caveats=caveats,
    )
