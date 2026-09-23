"""Vocabulary shared by the adapter contract and the canonical tables.

Adapters translate a platform's own words into these. Nothing downstream of the
adapter layer should ever see a platform-specific status string.
"""

from __future__ import annotations

from enum import StrEnum


class Channel(StrEnum):
    """Where a sale happened, normalised across platforms."""

    IN_STORE = "in_store"
    ONLINE = "online"
    EVENT = "event"  # pop-ups, conventions, markets
    OTHER = "other"


class OrderStatus(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"
    CANCELED = "canceled"


class MovementKind(StrEnum):
    """Why stock moved. `sold` is derivable from orders, but some systems
    report it directly, so it has a name here too."""

    RECEIVED = "received"
    SOLD = "sold"
    RETURNED = "returned"
    ADJUSTED = "adjusted"
    DAMAGED = "damaged"
    TRANSFERRED = "transferred"
    COUNTED = "counted"


class ChunkSource(StrEnum):
    PRODUCT = "product"
    DOCUMENT = "document"


class SyncMode(StrEnum):
    BACKFILL = "backfill"
    INCREMENTAL = "incremental"


class SyncStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
