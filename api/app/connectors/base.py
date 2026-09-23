"""The adapter contract.

An adapter's whole job is to turn one customer system into the canonical
Pydantic models in `app.canonical.models`. It does not touch our database, does
not know what a tenant id is, and never writes to the customer's system.

Two things every adapter owes the sync engine:

* **The untouched payload** alongside every record, so it lands in `raw_records`
  before anything maps it. That is what makes "re-run the mapping without
  re-fetching" and "where did this number come from?" answerable.
* **An honest `describe()`.** A capability claimed here turns a tool on for the
  tenant. Claiming `has_costs` on a system with no costs produces confident
  wrong answers, which is worse than no answer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.canonical.models import (
    CanonicalCategory,
    CanonicalCustomer,
    CanonicalInventoryLevel,
    CanonicalInventoryMovement,
    CanonicalLocation,
    CanonicalOrder,
    CanonicalProduct,
    CanonicalRefund,
    CanonicalVariant,
    Capabilities,
)

# The order matters: each entity may reference the ones before it by
# external_id, and the sync engine resolves those into foreign keys as it goes.
ENTITIES: tuple[str, ...] = (
    "locations",
    "categories",
    "products",
    "variants",
    "customers",
    "orders",
    "refunds",
    "inventory_levels",
    "inventory_movements",
)


@dataclass(frozen=True, slots=True)
class Fetched[T]:
    """One record, plus the bytes it was built from.

    `cursor` is the source's own pagination token *after* the page this record
    came from. The sync engine persists it so an interrupted backfill can pick
    up where it stopped instead of re-walking a rate-limited API.
    """

    record: T
    raw: dict[str, Any]
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class AdapterInfo:
    name: str
    display_name: str
    capabilities: Capabilities
    # Plain-language caveats worth showing a shop owner, e.g. "this system
    # keeps no cost history, so margin uses today's cost".
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HealthStatus:
    ok: bool
    detail: str


class AdapterError(RuntimeError):
    """The source system could not be read. Carries a message fit for a human."""


class SourceAdapter(ABC):
    """Base class for every adapter.

    Entities a source cannot provide are simply left unimplemented: the
    defaults yield nothing, and the matching capability tells the rest of the
    system why the numbers are missing.
    """

    @abstractmethod
    def describe(self) -> AdapterInfo: ...

    @abstractmethod
    async def healthcheck(self) -> HealthStatus: ...

    async def aclose(self) -> None:  # noqa: B027 — optional hook, not every adapter holds a connection
        """Release connections. Always called, even when a sync fails."""

    async def iter_locations(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalLocation]]:
        return
        yield  # pragma: no cover — makes this an async generator

    async def iter_categories(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalCategory]]:
        return
        yield  # pragma: no cover

    async def iter_products(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalProduct]]:
        return
        yield  # pragma: no cover

    async def iter_variants(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalVariant]]:
        return
        yield  # pragma: no cover

    async def iter_customers(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalCustomer]]:
        return
        yield  # pragma: no cover

    async def iter_orders(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalOrder]]:
        return
        yield  # pragma: no cover

    async def iter_refunds(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalRefund]]:
        return
        yield  # pragma: no cover

    async def iter_inventory_levels(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalInventoryLevel]]:
        return
        yield  # pragma: no cover

    async def iter_inventory_movements(
        self, since: datetime | None = None, resume_cursor: str | None = None
    ) -> AsyncIterator[Fetched[CanonicalInventoryMovement]]:
        return
        yield  # pragma: no cover

    def iterator_for(self, entity: str) -> Any:
        """The iterator that handles `entity`, by name."""
        try:
            return getattr(self, f"iter_{entity}")
        except AttributeError as exc:  # pragma: no cover — ENTITIES is fixed
            raise AdapterError(f"{type(self).__name__} has no iterator for {entity!r}") from exc
