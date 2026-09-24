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


class Tender(StrEnum):
    """How a payment was taken. Every provider's own list collapses to these."""

    CARD = "card"
    CASH = "cash"
    OTHER = "other"


class InsightSeverity(StrEnum):
    """How loudly an insight asks to be dealt with.

    `urgent` is the only one that may interrupt a shop owner's evening, so the
    bar for it is "money is leaving the building right now".
    """

    INFO = "info"
    WARN = "warn"
    URGENT = "urgent"


class InsightStatus(StrEnum):
    NEW = "new"
    SEEN = "seen"
    ACTED = "acted"
    DISMISSED = "dismissed"
    EXPIRED = "expired"
    SNOOZED = "snoozed"


class JobStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class NotifyChannel(StrEnum):
    EMAIL = "email"
    SMS = "sms"
    CONSOLE = "console"


class MessageStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class PurchaseOrderStatus(StrEnum):
    DRAFT = "draft"
    SENT = "sent"
    RECEIVED = "received"
    CANCELED = "canceled"


class StaleKind(StrEnum):
    """Dead stock, graded. Each grade gets a different rescue plan."""

    SLOWING = "slowing"
    STALE = "stale"
    DEAD = "dead"
