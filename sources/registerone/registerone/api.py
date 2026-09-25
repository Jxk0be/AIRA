"""The RegisterOne cloud API.

This is what our adapter is actually allowed to talk to. It behaves like a real
cloud POS API rather than like a database: bearer tokens, cursor pagination,
money as `{amount, currency}` objects, quantities as strings, rate limits, and
the occasional unexplained 503.

The adapter must never read registerone_db directly. If a test can only pass by
going around this API, the test is cheating.
"""

from __future__ import annotations

import base64
import json
import os
import random
import time
from collections import deque
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from registerone.db import dsn

TOKEN = os.environ.get("REGISTERONE_TOKEN", "ro_test_animanga_knox_2f8a41")
ADMIN_TOKEN = os.environ.get("REGISTERONE_ADMIN_TOKEN", "ro_admin_9c3e77")
RATE_LIMIT = int(os.environ.get("REGISTERONE_RATE_LIMIT", "10"))
FAULT_RATE = float(os.environ.get("REGISTERONE_FAULT_RATE", "0.01"))
MAX_PAGE_SIZE = int(os.environ.get("REGISTERONE_PAGE_SIZE", "100"))
CURRENCY = "USD"

pool: ConnectionPool | None = None
_recent_requests: deque[float] = deque()
_faults = random.Random(20260923)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global pool
    pool = ConnectionPool(dsn(), min_size=1, max_size=8, kwargs={"row_factory": dict_row})
    pool.wait(timeout=30)
    yield
    pool.close()


app = FastAPI(title="RegisterOne API", version="2.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# The things that make it feel like a cloud API
# ---------------------------------------------------------------------------


@app.middleware("http")
async def throttle_and_misbehave(request: Request, call_next: Any) -> Any:
    path = request.url.path
    exempt = path.startswith(("/health", "/_simulate", "/docs", "/openapi"))

    if not exempt:
        now = time.monotonic()
        while _recent_requests and now - _recent_requests[0] > 1.0:
            _recent_requests.popleft()
        if len(_recent_requests) >= RATE_LIMIT:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": "1"},
                content={
                    "errors": [
                        {
                            "category": "RATE_LIMIT_ERROR",
                            "code": "RATE_LIMITED",
                            "detail": f"More than {RATE_LIMIT} requests per second.",
                        }
                    ]
                },
            )
        _recent_requests.append(now)

        # The unexplained blip every integration has to survive.
        if FAULT_RATE > 0 and _faults.random() < FAULT_RATE:
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": "1"},
                content={
                    "errors": [
                        {
                            "category": "API_ERROR",
                            "code": "SERVICE_UNAVAILABLE",
                            "detail": "Temporarily unavailable. Retry.",
                        }
                    ]
                },
            )

    return await call_next(request)


def require_token(authorization: str = Header(default="")) -> str:
    if not authorization.startswith("Bearer ") or authorization[7:] != TOKEN:
        raise HTTPException(
            status_code=401,
            detail={
                "errors": [
                    {
                        "category": "AUTHENTICATION_ERROR",
                        "code": "UNAUTHORIZED",
                        "detail": "The Authorization header is missing or invalid.",
                    }
                ]
            },
        )
    return authorization[7:]


def require_admin(authorization: str = Header(default="")) -> str:
    if not authorization.startswith("Bearer ") or authorization[7:] != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Admin token required.")
    return authorization[7:]


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


def money(amount: int | None) -> dict[str, Any] | None:
    """Cents plus a currency, the way every real POS API does it."""
    return None if amount is None else {"amount": int(amount), "currency": CURRENCY}


def stamp(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def encode_cursor(sort_value: Any, row_id: str) -> str:
    payload = {"s": sort_value.isoformat() if isinstance(sort_value, datetime) else sort_value, "i": row_id}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[Any, str] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return payload["s"], payload["i"]
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "errors": [
                    {
                        "category": "INVALID_REQUEST_ERROR",
                        "code": "INVALID_CURSOR",
                        "detail": "Cursor is not readable.",
                    }
                ]
            },
        ) from exc


def page(
    sql: str,
    params: Sequence[Any],
    *,
    sort_expr: str,
    id_expr: str,
    cursor: str | None,
    limit: int,
) -> tuple[list[dict], str | None]:
    """Keyset pagination on (sort_expr, id_expr).

    Keyset rather than OFFSET so that a page boundary stays put while
    `/_simulate/day` is inserting rows underneath a long backfill.
    """
    assert pool is not None
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    args = list(params)
    where = ""
    after = decode_cursor(cursor)
    if after is not None:
        where = f" and ({sort_expr}, {id_expr}) > (%s, %s)"
        args.extend([after[0], after[1]])

    statement = f"{sql}{where} order by {sort_expr}, {id_expr} limit %s"
    args.append(limit + 1)

    with pool.connection() as connection, connection.cursor() as db:
        db.execute(statement, args)
        rows = db.fetchall()

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor(last["_sort"], last["_id"])
    for row in rows:
        row.pop("_sort", None)
        row.pop("_id", None)
    return rows, next_cursor


def envelope(objects: Iterable[dict], cursor: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"objects": list(objects)}
    if cursor:
        body["cursor"] = cursor
    return body


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    assert pool is not None
    with pool.connection() as connection, connection.cursor() as db:
        db.execute("select count(*) as orders from orders")
        orders = db.fetchone()["orders"]
    return {"status": "ok", "orders": orders, "rate_limit_per_second": RATE_LIMIT, "fault_rate": FAULT_RATE}


@app.get("/v2/locations")
def list_locations(_: str = Depends(require_token)) -> dict[str, Any]:
    assert pool is not None
    with pool.connection() as connection, connection.cursor() as db:
        db.execute("select id, name, timezone, status from locations order by id")
        rows = db.fetchall()
    return {
        "locations": [
            {
                "id": r["id"],
                "name": r["name"],
                "timezone": r["timezone"],
                "status": r["status"],
                "currency": CURRENCY,
            }
            for r in rows
        ]
    }


@app.get("/v2/catalog/list")
def list_catalog(
    types: str = Query(default="ITEM,ITEM_VARIATION,CATEGORY"),
    cursor: str | None = None,
    limit: int = MAX_PAGE_SIZE,
    begin_time: datetime | None = Query(default=None, description="only objects updated at or after this"),
    _: str = Depends(require_token),
) -> dict[str, Any]:
    wanted = {t.strip().upper() for t in types.split(",") if t.strip()}
    unknown = wanted - {"ITEM", "ITEM_VARIATION", "CATEGORY"}
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown object types: {sorted(unknown)}")
    if len(wanted) != 1:
        raise HTTPException(
            status_code=400,
            detail="Request one type at a time; each has its own cursor.",
        )
    kind = next(iter(wanted))

    time_clause = " and updated_at >= %s" if begin_time else ""
    params: list[Any] = [begin_time] if begin_time else []

    if kind == "CATEGORY":
        sql = (
            "select id, name, parent_id, updated_at, updated_at as _sort, id as _id "
            f"from categories where true{time_clause}"
        )
        rows, next_cursor = page(
            sql, params, sort_expr="updated_at", id_expr="id", cursor=cursor, limit=limit
        )
        objects = [
            {
                "type": "CATEGORY",
                "id": r["id"],
                "updated_at": stamp(r["updated_at"]),
                "category_data": {"name": r["name"], "parent_id": r["parent_id"]},
            }
            for r in rows
        ]
    elif kind == "ITEM":
        sql = (
            "select id, name, description, category_id, product_type, is_deleted, "
            "custom_attributes, created_at, updated_at, updated_at as _sort, id as _id "
            f"from catalog_items where true{time_clause}"
        )
        rows, next_cursor = page(
            sql, params, sort_expr="updated_at", id_expr="id", cursor=cursor, limit=limit
        )
        objects = [
            {
                "type": "ITEM",
                "id": r["id"],
                "updated_at": stamp(r["updated_at"]),
                "is_deleted": r["is_deleted"],
                "item_data": {
                    "name": r["name"],
                    "description": r["description"],
                    "category_id": r["category_id"],
                    "product_type": r["product_type"],
                    "custom_attributes": r["custom_attributes"],
                    "created_at": stamp(r["created_at"]),
                },
            }
            for r in rows
        ]
    else:
        sql = (
            "select v.id, v.item_id, v.name, v.sku, v.upc, v.price_amount, v.currency, "
            "v.pricing_type, v.track_inventory, v.is_deleted, v.updated_at, "
            "vi.vendor_id, vi.unit_cost_amount, v.updated_at as _sort, v.id as _id "
            "from item_variations v "
            "left join variation_vendor_info vi on vi.variation_id = v.id "
            f"where true{' and v.updated_at >= %s' if begin_time else ''}"
        )
        rows, next_cursor = page(
            sql, params, sort_expr="v.updated_at", id_expr="v.id", cursor=cursor, limit=limit
        )
        objects = [
            {
                "type": "ITEM_VARIATION",
                "id": r["id"],
                "updated_at": stamp(r["updated_at"]),
                "is_deleted": r["is_deleted"],
                "item_variation_data": {
                    "item_id": r["item_id"],
                    "name": r["name"],
                    "sku": r["sku"],
                    "upc": r["upc"],
                    "pricing_type": r["pricing_type"],
                    "price_money": money(r["price_amount"]),
                    "track_inventory": r["track_inventory"],
                    # Cost hangs off to the side here, exactly as it does upstream,
                    # and is null more often than anyone would like.
                    "vendor_info": (
                        None
                        if r["vendor_id"] is None
                        else {"vendor_id": r["vendor_id"], "unit_cost_money": money(r["unit_cost_amount"])}
                    ),
                },
            }
            for r in rows
        ]

    return envelope(objects, next_cursor)


@app.get("/v2/inventory/counts")
def list_inventory_counts(
    location_ids: str | None = None,
    cursor: str | None = None,
    limit: int = MAX_PAGE_SIZE,
    _: str = Depends(require_token),
) -> dict[str, Any]:
    clause = ""
    params: list[Any] = []
    if location_ids:
        clause = " and location_id = any(%s)"
        params.append([x.strip() for x in location_ids.split(",") if x.strip()])

    sql = (
        "select variation_id, location_id, state, quantity, calculated_at, "
        "variation_id || '|' || location_id || '|' || state as _sort, "
        "variation_id || '|' || location_id || '|' || state as _id "
        f"from inventory_counts where true{clause}"
    )
    rows, next_cursor = page(
        sql,
        params,
        sort_expr="variation_id || '|' || location_id || '|' || state",
        id_expr="variation_id || '|' || location_id || '|' || state",
        cursor=cursor,
        limit=limit,
    )
    return envelope(
        [
            {
                "catalog_object_id": r["variation_id"],
                "catalog_object_type": "ITEM_VARIATION",
                "location_id": r["location_id"],
                "state": r["state"],
                # A string, like the real thing. Parsing this as an int is a bug
                # waiting for the first fractional quantity.
                "quantity": r["quantity"],
                "calculated_at": stamp(r["calculated_at"]),
            }
            for r in rows
        ],
        next_cursor,
    )


@app.get("/v2/inventory/changes")
def list_inventory_changes(
    location_ids: str | None = None,
    begin_time: datetime | None = None,
    end_time: datetime | None = None,
    cursor: str | None = None,
    limit: int = MAX_PAGE_SIZE,
    _: str = Depends(require_token),
) -> dict[str, Any]:
    clause = ""
    params: list[Any] = []
    if location_ids:
        clause += " and location_id = any(%s)"
        params.append([x.strip() for x in location_ids.split(",") if x.strip()])
    if begin_time:
        clause += " and occurred_at >= %s"
        params.append(begin_time)
    if end_time:
        clause += " and occurred_at < %s"
        params.append(end_time)

    sql = (
        "select id, variation_id, location_id, from_state, to_state, quantity, "
        "occurred_at, reason, occurred_at as _sort, id as _id "
        f"from inventory_adjustments where true{clause}"
    )
    rows, next_cursor = page(sql, params, sort_expr="occurred_at", id_expr="id", cursor=cursor, limit=limit)
    return envelope(
        [
            {
                "type": "ADJUSTMENT",
                "id": r["id"],
                "adjustment": {
                    "catalog_object_id": r["variation_id"],
                    "catalog_object_type": "ITEM_VARIATION",
                    "location_id": r["location_id"],
                    "from_state": r["from_state"],
                    "to_state": r["to_state"],
                    "quantity": r["quantity"],
                    "occurred_at": stamp(r["occurred_at"]),
                    "reason": r["reason"],
                },
            }
            for r in rows
        ],
        next_cursor,
    )


@app.get("/v2/customers")
def list_customers(
    begin_time: datetime | None = None,
    cursor: str | None = None,
    limit: int = MAX_PAGE_SIZE,
    _: str = Depends(require_token),
) -> dict[str, Any]:
    clause = " and updated_at >= %s" if begin_time else ""
    params: list[Any] = [begin_time] if begin_time else []
    sql = (
        "select id, given_name, family_name, email, phone, reference_id, created_at, "
        "updated_at, updated_at as _sort, id as _id "
        f"from customers where true{clause}"
    )
    rows, next_cursor = page(sql, params, sort_expr="updated_at", id_expr="id", cursor=cursor, limit=limit)
    return envelope(
        [
            {
                "id": r["id"],
                "given_name": r["given_name"],
                "family_name": r["family_name"],
                "email_address": r["email"],
                "phone_number": r["phone"],
                "reference_id": r["reference_id"],
                "created_at": stamp(r["created_at"]),
                "updated_at": stamp(r["updated_at"]),
            }
            for r in rows
        ],
        next_cursor,
    )


class DateTimeRange(BaseModel):
    start_at: datetime | None = None
    end_at: datetime | None = None


class OrderSearchQuery(BaseModel):
    updated_at: DateTimeRange | None = None


class OrderSearchRequest(BaseModel):
    location_ids: list[str] | None = None
    query: OrderSearchQuery | None = None
    cursor: str | None = None
    limit: int = MAX_PAGE_SIZE
    # Orders that were never completed are excluded unless asked for, which is
    # the sort of default that quietly changes a revenue number.
    include_canceled: bool = True


@app.post("/v2/orders/search")
def search_orders(
    request: OrderSearchRequest,
    _: str = Depends(require_token),
) -> dict[str, Any]:
    clause = ""
    params: list[Any] = []
    if request.location_ids:
        clause += " and o.location_id = any(%s)"
        params.append(request.location_ids)
    if request.query and request.query.updated_at:
        if request.query.updated_at.start_at:
            clause += " and o.updated_at >= %s"
            params.append(request.query.updated_at.start_at)
        if request.query.updated_at.end_at:
            clause += " and o.updated_at < %s"
            params.append(request.query.updated_at.end_at)
    if not request.include_canceled:
        clause += " and o.state <> 'CANCELED'"

    sql = (
        "select o.id, o.location_id, o.customer_id, o.state, o.source, o.total_money, "
        "o.total_tax_money, o.total_discount_money, o.total_tip_money, o.created_at, "
        "o.updated_at, o.closed_at, o.version, o.updated_at as _sort, o.id as _id "
        f"from orders o where true{clause}"
    )
    rows, next_cursor = page(
        sql, params, sort_expr="o.updated_at", id_expr="o.id", cursor=request.cursor, limit=request.limit
    )

    order_ids = [r["id"] for r in rows]
    lines: dict[str, list[dict]] = {oid: [] for oid in order_ids}
    if order_ids:
        assert pool is not None
        with pool.connection() as connection, connection.cursor() as db:
            db.execute(
                "select uid, order_id, catalog_object_id, name, variation_name, quantity, "
                "base_price_money, total_discount_money, gross_sales_money, total_money, note "
                "from order_line_items where order_id = any(%s) order by uid",
                [order_ids],
            )
            for line in db.fetchall():
                lines[line["order_id"]].append(
                    {
                        "uid": line["uid"],
                        # Null means it was rung up as a bare amount with no
                        # catalog object behind it.
                        "catalog_object_id": line["catalog_object_id"],
                        "name": line["name"],
                        "variation_name": line["variation_name"],
                        "quantity": line["quantity"],
                        "base_price_money": money(line["base_price_money"]),
                        "total_discount_money": money(line["total_discount_money"]),
                        "gross_sales_money": money(line["gross_sales_money"]),
                        "total_money": money(line["total_money"]),
                        "note": line["note"],
                    }
                )

    return envelope(
        [
            {
                "id": r["id"],
                "location_id": r["location_id"],
                "customer_id": r["customer_id"],
                "state": r["state"],
                "source": {"name": r["source"]},
                "version": r["version"],
                "total_money": money(r["total_money"]),
                "total_tax_money": money(r["total_tax_money"]),
                "total_discount_money": money(r["total_discount_money"]),
                "total_tip_money": money(r["total_tip_money"]),
                "created_at": stamp(r["created_at"]),
                "updated_at": stamp(r["updated_at"]),
                "closed_at": stamp(r["closed_at"]),
                "line_items": lines[r["id"]],
            }
            for r in rows
        ],
        next_cursor,
    )


@app.get("/v2/vendors")
def list_vendors(
    cursor: str | None = None,
    limit: int = MAX_PAGE_SIZE,
    _: str = Depends(require_token),
) -> dict[str, Any]:
    """The suppliers the shop buys from.

    No `begin_time`: the table has no `updated_at`, which is exactly how a lot
    of real supplier lists are — small, rarely edited, and only ever fully
    re-read. An adapter has to cope with that rather than assume every entity
    supports an incremental pull.
    """
    sql = (
        "select id, name, account_number, email, phone, notes, "
        "id as _sort, id as _id from vendors where true"
    )
    rows, next_cursor = page(sql, [], sort_expr="id", id_expr="id", cursor=cursor, limit=limit)
    return envelope(
        [
            {
                "id": r["id"],
                "name": r["name"],
                "account_number": r["account_number"],
                "email_address": r["email"],
                "phone_number": r["phone"],
                "note": r["notes"],
            }
            for r in rows
        ],
        next_cursor,
    )


@app.get("/v2/payments")
def list_payments(
    begin_time: datetime | None = None,
    end_time: datetime | None = None,
    cursor: str | None = None,
    limit: int = MAX_PAGE_SIZE,
    _: str = Depends(require_token),
) -> dict[str, Any]:
    clause = ""
    params: list[Any] = []
    if begin_time:
        clause += " and created_at >= %s"
        params.append(begin_time)
    if end_time:
        clause += " and created_at < %s"
        params.append(end_time)

    sql = (
        "select id, order_id, amount_money, tip_money, source_type, card_brand, status, "
        "created_at, created_at as _sort, id as _id "
        f"from payments where true{clause}"
    )
    rows, next_cursor = page(sql, params, sort_expr="created_at", id_expr="id", cursor=cursor, limit=limit)
    return envelope(
        [
            {
                "id": r["id"],
                "order_id": r["order_id"],
                # amount_money excludes the tip; the two together are what the
                # customer handed over. Getting this wrong double-counts tips.
                "amount_money": money(r["amount_money"]),
                "tip_money": money(r["tip_money"]),
                "source_type": r["source_type"],
                "card_details": None if not r["card_brand"] else {"card": {"card_brand": r["card_brand"]}},
                "status": r["status"],
                "created_at": stamp(r["created_at"]),
            }
            for r in rows
        ],
        next_cursor,
    )


@app.get("/v2/refunds")
def list_refunds(
    begin_time: datetime | None = None,
    end_time: datetime | None = None,
    cursor: str | None = None,
    limit: int = MAX_PAGE_SIZE,
    _: str = Depends(require_token),
) -> dict[str, Any]:
    clause = ""
    params: list[Any] = []
    if begin_time:
        clause += " and created_at >= %s"
        params.append(begin_time)
    if end_time:
        clause += " and created_at < %s"
        params.append(end_time)

    sql = (
        "select id, payment_id, order_id, amount_money, reason, status, created_at, "
        "created_at as _sort, id as _id "
        f"from refunds where true{clause}"
    )
    rows, next_cursor = page(sql, params, sort_expr="created_at", id_expr="id", cursor=cursor, limit=limit)
    return envelope(
        [
            {
                "id": r["id"],
                "payment_id": r["payment_id"],
                "order_id": r["order_id"],
                "amount_money": money(r["amount_money"]),
                "reason": r["reason"],
                "status": r["status"],
                "created_at": stamp(r["created_at"]),
            }
            for r in rows
        ],
        next_cursor,
    )


# ---------------------------------------------------------------------------
# Admin: move the shop forward a day so incremental sync has something to find
# ---------------------------------------------------------------------------


class FaultRateRequest(BaseModel):
    fault_rate: float


@app.post("/_simulate/fault-rate")
def set_fault_rate(request: FaultRateRequest, _: str = Depends(require_admin)) -> dict[str, Any]:
    """Turn the random 503s up or down.

    Test infrastructure, not POS behaviour: retry logic needs a way to be
    exercised on purpose instead of once every hundred calls.
    """
    global FAULT_RATE
    if not 0.0 <= request.fault_rate <= 1.0:
        raise HTTPException(status_code=400, detail="fault_rate must be between 0 and 1")
    previous, FAULT_RATE = FAULT_RATE, request.fault_rate
    return {"fault_rate": FAULT_RATE, "previous": previous}


class SimulateDayRequest(BaseModel):
    day: date | None = None
    orders: int | None = None


@app.post("/_simulate/day")
def simulate_day(
    request: SimulateDayRequest,
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    assert pool is not None
    with pool.connection() as connection, connection.cursor() as db:
        db.execute("select max(created_at) as last from orders")
        last = db.fetchone()["last"]
        day = request.day or ((last.date() + timedelta(days=1)) if last else datetime.now(UTC).date())

        db.execute("select coalesce(max(substring(id from 5)::int), 0) as n from orders")
        order_n = db.fetchone()["n"]
        db.execute("select coalesce(max(substring(id from 5)::int), 0) as n from payments")
        payment_n = db.fetchone()["n"]
        db.execute("select coalesce(max(substring(id from 5)::int), 0) as n from inventory_adjustments")
        adjustment_n = db.fetchone()["n"]

        db.execute(
            """
            select v.id, v.price_amount, v.pricing_type, i.name as item_name, v.name as variation_name
            from item_variations v
            join catalog_items i on i.id = v.item_id
            join inventory_counts c
              on c.variation_id = v.id and c.location_id = 'LOC_MAIN' and c.state = 'IN_STOCK'
            where not v.is_deleted and not i.is_deleted
              and v.price_amount is not null and c.quantity::numeric > 1
              -- Only things this shop has ever actually sold. A variation with
              -- no sales behind it is either the never-sold quirk or the
              -- planted dead stock, and selling one of those on a simulated
              -- day quietly dismantles the scenario a test is about to look
              -- for. Since every call to this endpoint moves the fixture a day
              -- further from its seed, that erosion is permanent.
              and exists (
                select 1 from order_line_items l where l.catalog_object_id = v.id
              )
            order by v.id
            """
        )
        sellable = db.fetchall()
        if not sellable:
            raise HTTPException(status_code=409, detail="Nothing in stock to sell; seed first.")

        db.execute("select id from customers order by id")
        customer_ids = [r["id"] for r in db.fetchall()]

        rng = random.Random(f"{day.isoformat()}:{order_n}")
        count = request.orders if request.orders is not None else rng.randint(4, 12)

        created_orders: list[str] = []
        touched: set[tuple[str, str]] = set()
        line_count = 0
        payment_count = 0
        adjustment_count = 0

        for _i in range(count):
            order_n += 1
            order_id = f"ORD_{order_n:06d}"
            placed = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(
                hours=rng.randint(15, 23), minutes=rng.randint(0, 59)
            )

            picks = rng.sample(sellable, k=min(len(sellable), rng.choices([1, 2, 3], [5, 3, 2])[0]))
            subtotal = 0
            discount_total = 0
            lines: list[tuple] = []
            for index, variation in enumerate(picks, start=1):
                quantity = 1
                unit = variation["price_amount"]
                gross = unit * quantity
                discount = int(gross * 0.1) if rng.random() < 0.08 else 0
                subtotal += gross - discount
                discount_total += discount
                lines.append(
                    (
                        f"{order_id}:{index}",
                        order_id,
                        variation["id"],
                        variation["item_name"],
                        variation["variation_name"],
                        str(quantity),
                        unit,
                        discount,
                        gross,
                        gross - discount,
                        None,
                    )
                )

            tax = round(subtotal * 0.0925)
            total = subtotal + tax
            customer_id = rng.choice(customer_ids) if (customer_ids and rng.random() > 0.6) else None
            closed = placed + timedelta(minutes=rng.randint(1, 9))

            db.execute(
                "insert into orders (id, location_id, customer_id, state, source, total_money, "
                "total_tax_money, total_discount_money, total_tip_money, created_at, updated_at, "
                "closed_at, version) values (%s, 'LOC_MAIN', %s, 'COMPLETED', 'POS', %s, %s, %s, 0, "
                "%s, %s, %s, 1)",
                (order_id, customer_id, total, tax, discount_total, placed, closed, closed),
            )
            db.executemany(
                "insert into order_line_items (uid, order_id, catalog_object_id, name, "
                "variation_name, quantity, base_price_money, total_discount_money, "
                "gross_sales_money, total_money, note) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                lines,
            )
            line_count += len(lines)

            payment_n += 1
            db.execute(
                "insert into payments (id, order_id, amount_money, tip_money, source_type, "
                "card_brand, status, created_at) values (%s, %s, %s, 0, %s, %s, 'COMPLETED', %s)",
                (
                    f"PAY_{payment_n:06d}",
                    order_id,
                    total,
                    "CARD" if rng.random() < 0.7 else "CASH",
                    "VISA" if rng.random() < 0.7 else None,
                    closed,
                ),
            )
            payment_count += 1

            for line in lines:
                adjustment_n += 1
                db.execute(
                    "insert into inventory_adjustments (id, variation_id, location_id, from_state, "
                    "to_state, quantity, occurred_at, reason) "
                    "values (%s, %s, 'LOC_MAIN', 'IN_STOCK', 'SOLD', %s, %s, 'Sale')",
                    (f"ADJ_{adjustment_n:06d}", line[2], line[5], placed),
                )
                adjustment_count += 1
                touched.add((line[2], "LOC_MAIN"))

            created_orders.append(order_id)

        # A shipment lands too, so incremental sync sees stock go up as well as down.
        for variation in rng.sample(sellable, k=min(6, len(sellable))):
            adjustment_n += 1
            received = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=14)
            quantity = rng.randint(2, 8)
            db.execute(
                "insert into inventory_adjustments (id, variation_id, location_id, from_state, "
                "to_state, quantity, occurred_at, reason) "
                "values (%s, %s, 'LOC_MAIN', null, 'IN_STOCK', %s, %s, 'Shipment received')",
                (f"ADJ_{adjustment_n:06d}", variation["id"], str(quantity), received),
            )
            adjustment_count += 1
            touched.add((variation["id"], "LOC_MAIN"))

        # Recompute the affected counts from history, so the fixture's central
        # invariant survives every simulated day.
        recalculated = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=23)
        for variation_id, location_id in sorted(touched):
            db.execute(
                """
                insert into inventory_counts (variation_id, location_id, state, quantity, calculated_at)
                select %s, %s, s.state, s.quantity::text, %s from (
                    select 'IN_STOCK' as state, coalesce(sum(
                        case when to_state = 'IN_STOCK' then quantity::numeric
                             else -quantity::numeric end), 0) as quantity
                    from inventory_adjustments where variation_id = %s and location_id = %s
                    union all
                    select 'SOLD', coalesce(sum(quantity::numeric), 0)
                    from inventory_adjustments
                    where variation_id = %s and location_id = %s and to_state = 'SOLD'
                    union all
                    select 'WASTE', coalesce(sum(quantity::numeric), 0)
                    from inventory_adjustments
                    where variation_id = %s and location_id = %s and to_state = 'WASTE'
                ) s
                on conflict (variation_id, location_id, state)
                do update set quantity = excluded.quantity, calculated_at = excluded.calculated_at
                """,
                (
                    variation_id,
                    location_id,
                    recalculated,
                    variation_id,
                    location_id,
                    variation_id,
                    location_id,
                    variation_id,
                    location_id,
                ),
            )

        connection.commit()

    return {
        "day": day.isoformat(),
        "orders_created": len(created_orders),
        "order_ids": created_orders,
        "line_items": line_count,
        "payments": payment_count,
        "inventory_adjustments": adjustment_count,
        "variations_recounted": len(touched),
    }
