"""Checks that the fake POS behaves like a cloud API rather than a database.

Runs against the live container. These tests are allowed to open registerone_db
directly for ground truth — the *adapter* is not.

    python tasks.py sources && python tasks.py seed
    uv run --project sources/registerone pytest sources/registerone/tests
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import httpx
import psycopg
import pytest

BASE = "http://localhost:8100"
TOKEN = "ro_test_tsundoku_2f8a41"
ADMIN_TOKEN = "ro_admin_9c3e77"
DSN = "postgresql://registerone:registerone@127.0.0.1:5433/registerone"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@pytest.fixture(scope="session")
def client() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=BASE, timeout=30) as c:
        try:
            health = c.get("/health")
        except httpx.HTTPError as exc:
            pytest.skip(f"RegisterOne API not running ({exc}); run `python tasks.py sources`")
        if health.status_code != 200 or health.json().get("orders", 0) == 0:
            pytest.skip("RegisterOne has no data; run `python tasks.py seed`")
        yield c


@pytest.fixture(scope="session")
def db() -> Iterator[psycopg.Connection]:
    try:
        connection = psycopg.connect(DSN, connect_timeout=5)
    except psycopg.Error as exc:
        pytest.skip(f"registerone_db not reachable ({exc})")
    with connection:
        yield connection


def scalar(db: psycopg.Connection, sql: str, params: tuple = ()) -> Any:
    with db.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


def drain(client: httpx.Client, path: str, params: dict | None = None) -> list[dict]:
    """Follow the cursor to the end, retrying the faults the API injects."""
    objects: list[dict] = []
    cursor: str | None = None
    while True:
        query = dict(params or {})
        if cursor:
            query["cursor"] = cursor
        response = _get_with_retry(client, path, query)
        body = response.json()
        objects.extend(body["objects"])
        cursor = body.get("cursor")
        if not cursor:
            return objects


def _get_with_retry(client: httpx.Client, path: str, params: dict, attempts: int = 8) -> httpx.Response:
    for attempt in range(attempts):
        response = client.get(path, params=params, headers=AUTH)
        if response.status_code in (429, 503):
            time.sleep(float(response.headers.get("Retry-After", "1")) * (0.3 + attempt * 0.2))
            continue
        response.raise_for_status()
        return response
    raise AssertionError(f"{path} never succeeded in {attempts} attempts")


# ---------------------------------------------------------------------------


def test_requires_a_bearer_token(client: httpx.Client) -> None:
    assert client.get("/v2/locations").status_code == 401
    assert client.get("/v2/locations", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_locations(client: httpx.Client) -> None:
    body = _get_with_retry(client, "/v2/locations", {}).json()
    ids = {location["id"] for location in body["locations"]}
    assert ids == {"LOC_MAIN", "LOC_CON"}


def test_catalog_pagination_returns_every_variation(client: httpx.Client, db: psycopg.Connection) -> None:
    objects = drain(client, "/v2/catalog/list", {"types": "ITEM_VARIATION", "limit": 100})
    assert len(objects) == scalar(db, "select count(*) from item_variations")
    assert len({o["id"] for o in objects}) == len(objects), "a page boundary duplicated rows"


def test_pages_are_capped_at_one_hundred(client: httpx.Client) -> None:
    body = _get_with_retry(client, "/v2/catalog/list", {"types": "ITEM", "limit": 500}).json()
    assert len(body["objects"]) <= 100


def test_money_is_an_object_and_quantities_are_strings(client: httpx.Client) -> None:
    body = _get_with_retry(client, "/v2/inventory/counts", {"limit": 5}).json()
    count = body["objects"][0]
    assert isinstance(count["quantity"], str)

    order = _post_with_retry(client, "/v2/orders/search", {"limit": 5}).json()["objects"][0]
    assert order["total_money"] == {"amount": order["total_money"]["amount"], "currency": "USD"}
    assert isinstance(order["total_money"]["amount"], int)
    for line in order["line_items"]:
        assert isinstance(line["quantity"], str)
        assert set(line["base_price_money"]) == {"amount", "currency"}


def test_custom_amount_lines_have_no_catalog_object(client: httpx.Client) -> None:
    orders = _post_with_retry(client, "/v2/orders/search", {"limit": 100}).json()["objects"]
    lines = [line for order in orders for line in order["line_items"]]
    assert any(line["catalog_object_id"] is None for line in lines), (
        "no custom-amount line in the first 100 orders; the fixture lost a quirk"
    )


def test_orders_search_honours_the_updated_at_filter(client: httpx.Client, db: psycopg.Connection) -> None:
    cutoff = scalar(db, "select max(updated_at) - interval '30 days' from orders")
    expected = scalar(db, "select count(*) from orders where updated_at >= %s", (cutoff,))

    objects: list[dict] = []
    cursor: str | None = None
    while True:
        payload: dict[str, Any] = {
            "query": {"updated_at": {"start_at": cutoff.isoformat()}},
            "limit": 100,
        }
        if cursor:
            payload["cursor"] = cursor
        body = _post_with_retry(client, "/v2/orders/search", payload).json()
        objects.extend(body["objects"])
        cursor = body.get("cursor")
        if not cursor:
            break

    assert len(objects) == expected
    assert all(order["updated_at"] >= cutoff.isoformat().replace("+00:00", "Z") for order in objects)


def test_a_bad_cursor_is_rejected(client: httpx.Client) -> None:
    response = client.get("/v2/customers", params={"cursor": "not-a-cursor"}, headers=AUTH)
    assert response.status_code in (400, 429, 503)
    if response.status_code == 400:
        assert response.json()["detail"]["errors"][0]["code"] == "INVALID_CURSOR"


def test_rate_limiting_answers_429_with_retry_after(client: httpx.Client) -> None:
    statuses = []
    for _ in range(40):
        statuses.append(client.get("/v2/locations", headers=AUTH))
    limited = [r for r in statuses if r.status_code == 429]
    assert limited, "hammering the API never produced a 429"
    assert limited[0].headers.get("Retry-After")
    assert limited[0].json()["errors"][0]["code"] == "RATE_LIMITED"
    time.sleep(1.1)


def test_transient_503s_are_retryable(client: httpx.Client) -> None:
    """Turn the fault rate to certainty, confirm the shape, then put it back."""
    previous = client.post("/_simulate/fault-rate", json={"fault_rate": 1.0}, headers=ADMIN).json()[
        "previous"
    ]
    try:
        response = client.get("/v2/locations", headers=AUTH)
        assert response.status_code == 503
        assert response.headers.get("Retry-After")
        assert response.json()["errors"][0]["code"] == "SERVICE_UNAVAILABLE"
    finally:
        client.post("/_simulate/fault-rate", json={"fault_rate": previous}, headers=ADMIN)

    # And with faults off again, the same call succeeds.
    assert _get_with_retry(client, "/v2/locations", {}).status_code == 200


def test_simulate_day_adds_a_day_and_keeps_inventory_consistent(
    client: httpx.Client, db: psycopg.Connection
) -> None:
    before_orders = scalar(db, "select count(*) from orders")
    before_max = scalar(db, "select max(updated_at) from orders")

    response = client.post("/_simulate/day", json={"orders": 6}, headers=ADMIN)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["orders_created"] == 6

    db.rollback()  # see the API's committed writes
    assert scalar(db, "select count(*) from orders") == before_orders + 6
    assert scalar(db, "select max(updated_at) from orders") > before_max

    # The invariant the whole fixture rests on must survive the new day.
    drift = scalar(
        db,
        """
        select count(*) from (
            select c.variation_id, c.location_id, c.quantity::numeric as counted,
                   coalesce(sum(case when a.to_state = 'IN_STOCK' then a.quantity::numeric
                                     else -a.quantity::numeric end), 0) as summed
            from inventory_counts c
            left join inventory_adjustments a
              on a.variation_id = c.variation_id and a.location_id = c.location_id
            where c.state = 'IN_STOCK'
            group by 1, 2, 3
        ) t where counted <> summed
        """,
    )
    assert drift == 0

    # And an incremental pull with a cursor just before the simulated day sees
    # exactly the new orders, and nothing else.
    fresh = []
    cursor: str | None = None
    while True:
        payload: dict[str, Any] = {
            "query": {"updated_at": {"start_at": before_max.isoformat()}},
            "limit": 100,
        }
        if cursor:
            payload["cursor"] = cursor
        body = _post_with_retry(client, "/v2/orders/search", payload).json()
        fresh.extend(body["objects"])
        cursor = body.get("cursor")
        if not cursor:
            break
    new_ids = {order["id"] for order in fresh}
    assert set(result["order_ids"]) <= new_ids


def _post_with_retry(client: httpx.Client, path: str, payload: dict, attempts: int = 8) -> httpx.Response:
    for attempt in range(attempts):
        response = client.post(path, json=payload, headers=AUTH)
        if response.status_code in (429, 503):
            time.sleep(float(response.headers.get("Retry-After", "1")) * (0.3 + attempt * 0.2))
            continue
        response.raise_for_status()
        return response
    raise AssertionError(f"{path} never succeeded in {attempts} attempts")
