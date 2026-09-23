"""The RegisterOne adapter.

This is the worked example: a code adapter against a cursor-paginated,
rate-limited cloud API. A new platform means copying this file's shape, not
touching anything else in AIRA.

It talks to the RegisterOne REST API and never to `registerone_db`. Reading the
customer's database directly would be both cheating and, for every real
platform, impossible.

Three conversions it owes the canonical model:

* integer cents -> `Decimal` dollars
* decimal *strings* -> `Decimal`
* platform vocabulary -> canonical vocabulary (`POS` -> `in_store`, the con
  booth's location -> `event`, `IN_STOCK -> SOLD` -> a `sold` movement)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

import httpx

from app.canonical.enums import Channel, MovementKind, OrderStatus
from app.canonical.models import (
    CanonicalCategory,
    CanonicalCustomer,
    CanonicalInventoryLevel,
    CanonicalInventoryMovement,
    CanonicalLocation,
    CanonicalOrder,
    CanonicalOrderLine,
    CanonicalProduct,
    CanonicalRefund,
    CanonicalVariant,
    Capabilities,
)
from app.connectors.base import AdapterError, AdapterInfo, Fetched, HealthStatus, SourceAdapter
from app.connectors.registry import register

log = logging.getLogger(__name__)

ADAPTER_NAME = "registerone"
DEFAULT_BASE_URL = "http://localhost:8100"
PAGE_SIZE = 100
MAX_ATTEMPTS = 8
# The API answers 429 above ten requests a second, so stay under it rather than
# discovering the limit over and over.
MIN_INTERVAL_SECONDS = 0.12

CAPABILITIES = Capabilities(
    has_costs=True,
    has_customers=True,
    has_inventory_history=True,
    multi_location=True,
    has_online_channel=True,
    supports_incremental=True,
)

NOTES = (
    "RegisterOne keeps no cost history, so the cost on a past sale is the item's "
    "cost as it stands today.",
    "About 40% of sales are cash walk-ins with no customer attached, so "
    "customer figures describe the shop's regulars rather than everyone.",
)

STATUS = {
    "OPEN": OrderStatus.OPEN,
    "COMPLETED": OrderStatus.COMPLETED,
    "CANCELED": OrderStatus.CANCELED,
}

MOVEMENTS = {
    (None, "IN_STOCK"): MovementKind.RECEIVED,
    ("IN_STOCK", "SOLD"): MovementKind.SOLD,
    ("IN_STOCK", "WASTE"): MovementKind.DAMAGED,
    ("SOLD", "IN_STOCK"): MovementKind.RETURNED,
}


def cents(money: dict[str, Any] | None) -> Decimal | None:
    """`{"amount": 1299, "currency": "USD"}` -> `Decimal("12.99")`."""
    if not money or money.get("amount") is None:
        return None
    return (Decimal(int(money["amount"])) / 100).quantize(Decimal("0.0001"))


def quantity(value: str | None) -> Decimal:
    """Quantities arrive as strings. Parsing them as ints breaks the first time
    someone sells half a pound of anything."""
    return Decimal(value) if value not in (None, "") else Decimal("0")


def when(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class RegisterOneClient:
    """Bearer auth, cursor pagination, and retries that honour Retry-After."""

    def __init__(self, base_url: str, token: str, timeout: float = 30.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        self._next_allowed = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _pace(self) -> None:
        loop = asyncio.get_running_loop()
        wait = self._next_allowed - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)
        self._next_allowed = loop.time() + MIN_INTERVAL_SECONDS

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last_error = ""
        for attempt in range(MAX_ATTEMPTS):
            await self._pace()
            try:
                response = await self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                await self._backoff(attempt, None)
                continue

            if response.status_code in (429, 503):
                last_error = f"HTTP {response.status_code}"
                await self._backoff(attempt, response.headers.get("Retry-After"))
                continue
            if response.status_code == 401:
                raise AdapterError(
                    "RegisterOne rejected our credentials. Check the integration's secret_ref."
                )
            if response.status_code >= 400:
                raise AdapterError(f"RegisterOne {method} {path} failed: {response.text[:300]}")
            return cast(dict[str, Any], response.json())

        raise AdapterError(
            f"RegisterOne {method} {path} still failing after {MAX_ATTEMPTS} attempts "
            f"({last_error})"
        )

    @staticmethod
    async def _backoff(attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = 1.0
        else:
            delay = min(8.0, 0.25 * (2**attempt))
        # Jitter, so a backfill that hits the limit does not resume in lockstep.
        await asyncio.sleep(delay * (0.75 + random.random() * 0.5))

    async def paginate(
        self, path: str, *, params: dict[str, Any] | None = None, cursor: str | None = None
    ) -> AsyncIterator[tuple[dict[str, Any], str | None]]:
        """Yield `(object, cursor_after_this_page)` until the cursor runs out."""
        query = dict(params or {})
        query["limit"] = PAGE_SIZE
        while True:
            if cursor:
                query["cursor"] = cursor
            body = await self.request("GET", path, params=query)
            cursor = body.get("cursor")
            for obj in body["objects"]:
                yield obj, cursor
            if not cursor:
                return

    async def paginate_post(
        self, path: str, payload: dict[str, Any], cursor: str | None = None
    ) -> AsyncIterator[tuple[dict[str, Any], str | None]]:
        while True:
            body_in = dict(payload, limit=PAGE_SIZE)
            if cursor:
                body_in["cursor"] = cursor
            body = await self.request("POST", path, json=body_in)
            cursor = body.get("cursor")
            for obj in body["objects"]:
                yield obj, cursor
            if not cursor:
                return


class RegisterOneAdapter(SourceAdapter):
    def __init__(self, config: dict[str, Any], secret: str | None = None) -> None:
        base_url = config.get("base_url") or os.environ.get(
            "REGISTERONE_BASE_URL", DEFAULT_BASE_URL
        )
        token = secret or os.environ.get("REGISTERONE_TOKEN")
        if not token:
            raise AdapterError(
                "No RegisterOne token. Set the integration's secret_ref, or REGISTERONE_TOKEN."
            )
        self.config = config
        self.client = RegisterOneClient(base_url, token)
        # Cost at the time of a sale is not something RegisterOne records, so we
        # snapshot the variation's current cost. NOTES says so out loud.
        self._costs: dict[str, Decimal | None] = {}
        self._costs_complete = False
        # Which location is the convention booth, so its sales become
        # channel=event rather than another in-store register.
        self._event_locations: set[str] = set(config.get("event_location_ids", ["LOC_CON"]))

    def describe(self) -> AdapterInfo:
        return AdapterInfo(
            name=ADAPTER_NAME,
            display_name="RegisterOne",
            capabilities=CAPABILITIES,
            notes=NOTES,
        )

    async def healthcheck(self) -> HealthStatus:
        try:
            body = await self.client.request("GET", "/v2/locations")
        except AdapterError as exc:
            return HealthStatus(ok=False, detail=str(exc))
        count = len(body.get("locations", []))
        return HealthStatus(ok=count > 0, detail=f"{count} locations visible")

    async def aclose(self) -> None:
        await self.client.aclose()

    # -- catalog ------------------------------------------------------------

    async def iter_locations(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalLocation]]:
        body = await self.client.request("GET", "/v2/locations")
        for raw in body["locations"]:
            yield Fetched(
                record=CanonicalLocation(
                    external_id=raw["id"],
                    name=raw["name"],
                    timezone=raw.get("timezone"),
                    is_active=raw.get("status") == "ACTIVE",
                ),
                raw=raw,
            )

    async def iter_categories(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalCategory]]:
        async for raw, cursor in self._catalog("CATEGORY", since, resume_cursor):
            data = raw["category_data"]
            yield Fetched(
                record=CanonicalCategory(
                    external_id=raw["id"],
                    name=data["name"],
                    parent_external_id=data.get("parent_id"),
                    source_updated_at=when(raw.get("updated_at")),
                ),
                raw=raw,
                cursor=cursor,
            )

    async def iter_products(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalProduct]]:
        async for raw, cursor in self._catalog("ITEM", since, resume_cursor):
            data = raw["item_data"]
            updated = when(raw.get("updated_at"))
            deleted = bool(raw.get("is_deleted"))
            yield Fetched(
                record=CanonicalProduct(
                    external_id=raw["id"],
                    name=data["name"],
                    description=data.get("description"),
                    category_external_id=data.get("category_id"),
                    product_type=data.get("product_type"),
                    attributes=data.get("custom_attributes") or {},
                    is_active=not deleted,
                    # The source only tells us *that* it was deleted, so the
                    # best deletion time we have is when it last changed.
                    deleted_at=updated if deleted else None,
                    source_updated_at=updated,
                ),
                raw=raw,
                cursor=cursor,
            )

    async def iter_variants(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalVariant]]:
        async for raw, cursor in self._catalog("ITEM_VARIATION", since, resume_cursor):
            data = raw["item_variation_data"]
            vendor = data.get("vendor_info") or {}
            cost = cents(vendor.get("unit_cost_money"))
            self._costs[raw["id"]] = cost
            updated = when(raw.get("updated_at"))
            deleted = bool(raw.get("is_deleted"))
            yield Fetched(
                record=CanonicalVariant(
                    external_id=raw["id"],
                    product_external_id=data["item_id"],
                    name=data.get("name"),
                    sku=data.get("sku"),
                    barcode=data.get("upc"),
                    # Null for VARIABLE pricing: the register decides, and the
                    # catalog genuinely does not know.
                    price=cents(data.get("price_money")),
                    cost=cost,
                    tracks_inventory=bool(data.get("track_inventory", True)),
                    is_active=not deleted,
                    deleted_at=updated if deleted else None,
                    source_updated_at=updated,
                ),
                raw=raw,
                cursor=cursor,
            )
        if since is None:
            self._costs_complete = True

    async def _catalog(
        self, kind: str, since: datetime | None, resume_cursor: str | None
    ) -> AsyncIterator[tuple[dict[str, Any], str | None]]:
        params: dict[str, Any] = {"types": kind}
        if since is not None:
            params["begin_time"] = since.isoformat()
        async for raw, cursor in self.client.paginate(
            "/v2/catalog/list", params=params, cursor=resume_cursor
        ):
            yield raw, cursor

    # -- people -------------------------------------------------------------

    async def iter_customers(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalCustomer]]:
        params: dict[str, Any] = {}
        if since is not None:
            params["begin_time"] = since.isoformat()
        async for raw, cursor in self.client.paginate(
            "/v2/customers", params=params, cursor=resume_cursor
        ):
            yield Fetched(
                record=CanonicalCustomer(
                    external_id=raw["id"],
                    first_name=raw.get("given_name"),
                    last_name=raw.get("family_name"),
                    email=raw.get("email_address"),
                    phone=raw.get("phone_number"),
                    source_created_at=when(raw.get("created_at")),
                    source_updated_at=when(raw.get("updated_at")),
                ),
                raw=raw,
                cursor=cursor,
            )

    # -- sales --------------------------------------------------------------

    async def iter_orders(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalOrder]]:
        await self._ensure_costs()
        payload: dict[str, Any] = {}
        if since is not None:
            payload["query"] = {"updated_at": {"start_at": since.isoformat()}}

        async for raw, cursor in self.client.paginate_post(
            "/v2/orders/search", payload, cursor=resume_cursor
        ):
            yield Fetched(record=self._order(raw), raw=raw, cursor=cursor)

    def _order(self, raw: dict[str, Any]) -> CanonicalOrder:
        total = cents(raw["total_money"]) or Decimal("0")
        tax = cents(raw["total_tax_money"]) or Decimal("0")
        tip = cents(raw["total_tip_money"]) or Decimal("0")
        discount = cents(raw["total_discount_money"]) or Decimal("0")
        # Canonical subtotal is gross, before discounts:
        #   total = subtotal - discount + tax + tip
        subtotal = total - tax - tip + discount

        location_id = raw.get("location_id")
        source_name = (raw.get("source") or {}).get("name")
        if location_id in self._event_locations:
            channel = Channel.EVENT
        elif source_name == "ONLINE":
            channel = Channel.ONLINE
        elif source_name == "POS":
            channel = Channel.IN_STORE
        else:
            channel = Channel.OTHER

        placed = when(raw["created_at"])
        assert placed is not None  # created_at is never null in this API

        return CanonicalOrder(
            external_id=raw["id"],
            location_external_id=location_id,
            customer_external_id=raw.get("customer_id"),
            status=STATUS.get(raw["state"], OrderStatus.OPEN),
            channel=channel,
            subtotal=subtotal,
            discount_total=discount,
            tax_total=tax,
            tip_total=tip,
            total=total,
            placed_at=placed,
            closed_at=when(raw.get("closed_at")),
            source_updated_at=when(raw.get("updated_at")),
            lines=[self._line(line) for line in raw.get("line_items", [])],
        )

    def _line(self, raw: dict[str, Any]) -> CanonicalOrderLine:
        variant_id = raw.get("catalog_object_id")
        name = raw["name"]
        if raw.get("variation_name"):
            name = f"{name} ({raw['variation_name']})"
        return CanonicalOrderLine(
            external_id=raw["uid"],
            # None for a custom amount rung up at the register. Not an error,
            # and not something to invent a product for.
            variant_external_id=variant_id,
            name_snapshot=name,
            quantity=quantity(raw["quantity"]),
            unit_price=cents(raw["base_price_money"]) or Decimal("0"),
            discount=cents(raw.get("total_discount_money")) or Decimal("0"),
            unit_cost_snapshot=self._costs.get(variant_id) if variant_id else None,
            note=raw.get("note"),
        )

    async def iter_refunds(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalRefund]]:
        params: dict[str, Any] = {}
        if since is not None:
            params["begin_time"] = since.isoformat()
        async for raw, cursor in self.client.paginate(
            "/v2/refunds", params=params, cursor=resume_cursor
        ):
            occurred = when(raw["created_at"])
            assert occurred is not None
            yield Fetched(
                record=CanonicalRefund(
                    external_id=raw["id"],
                    order_external_id=raw["order_id"],
                    amount=cents(raw["amount_money"]) or Decimal("0"),
                    reason=raw.get("reason"),
                    occurred_at=occurred,
                    source_updated_at=occurred,
                ),
                raw=raw,
                cursor=cursor,
            )

    # -- inventory ----------------------------------------------------------

    async def iter_inventory_levels(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalInventoryLevel]]:
        # Counts are current state, not history, so `since` does not apply:
        # a level that did not change still has to be the level we hold.
        async for raw, cursor in self.client.paginate("/v2/inventory/counts", cursor=resume_cursor):
            if raw.get("state") != "IN_STOCK":
                continue
            as_of = when(raw.get("calculated_at"))
            assert as_of is not None
            yield Fetched(
                record=CanonicalInventoryLevel(
                    external_id=f"{raw['catalog_object_id']}:{raw['location_id']}",
                    variant_external_id=raw["catalog_object_id"],
                    location_external_id=raw["location_id"],
                    on_hand=quantity(raw["quantity"]),
                    as_of=as_of,
                    source_updated_at=as_of,
                ),
                raw=raw,
                cursor=cursor,
            )

    async def iter_inventory_movements(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalInventoryMovement]]:
        params: dict[str, Any] = {}
        if since is not None:
            params["begin_time"] = since.isoformat()
        async for raw, cursor in self.client.paginate(
            "/v2/inventory/changes", params=params, cursor=resume_cursor
        ):
            adjustment = raw["adjustment"]
            occurred = when(adjustment["occurred_at"])
            assert occurred is not None
            states = (adjustment.get("from_state"), adjustment.get("to_state"))
            yield Fetched(
                record=CanonicalInventoryMovement(
                    external_id=raw["id"],
                    variant_external_id=adjustment["catalog_object_id"],
                    location_external_id=adjustment.get("location_id"),
                    kind=MOVEMENTS.get(states, MovementKind.ADJUSTED),
                    quantity=quantity(adjustment["quantity"]),
                    occurred_at=occurred,
                    reason=adjustment.get("reason"),
                    source_updated_at=occurred,
                ),
                raw=raw,
                cursor=cursor,
            )

    # -- costs --------------------------------------------------------------

    async def _ensure_costs(self) -> None:
        """A full catalog pass, once, so every sold line can carry a cost.

        On an incremental run `iter_variants` only saw what changed, which is
        not enough to price the lines in this run's orders.
        """
        if self._costs_complete:
            return
        log.info("pulling the full variation list for cost snapshots")
        async for raw, _ in self._catalog("ITEM_VARIATION", None, None):
            data = raw["item_variation_data"]
            vendor = data.get("vendor_info") or {}
            self._costs[raw["id"]] = cents(vendor.get("unit_cost_money"))
        self._costs_complete = True


def _build(config: dict[str, Any], secret: str | None) -> SourceAdapter:
    return RegisterOneAdapter(config, secret)


def _describe() -> AdapterInfo:
    return AdapterInfo(
        name=ADAPTER_NAME,
        display_name="RegisterOne",
        capabilities=CAPABILITIES,
        notes=NOTES,
    )


register(ADAPTER_NAME, _build, _describe)
