"""The shape of an outbound message, and what a provider owes us.

One interface so the digest, the alerts and the next-volume sends all speak the
same way, and so a test never needs a network. A notifier's only job is to hand
the bytes to a provider and say what happened; deciding *whether* to send is
`app.notify.service`'s job and happens before a notifier is ever reached.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.canonical.enums import NotifyChannel


@dataclass(frozen=True, slots=True)
class Message:
    """One message, already rendered.

    Both bodies are built for every email: `text` is not a fallback nobody
    reads, it is what a phone's notification preview shows and what lands when
    images are blocked.
    """

    to: str
    subject: str
    text: str
    html: str | None = None
    # "digest", "alert", "series_release" — matches the log's `kind`.
    kind: str = "alert"
    # Reply-to for a shop-branded send, so a customer replying reaches the shop
    # rather than us.
    reply_to: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Delivery:
    """What the provider said. A failure is a value, not an exception.

    A digest that could not be emailed must still leave a row saying so, and a
    raised exception halfway through a loop over ten shops would lose the other
    nine.
    """

    ok: bool
    provider_message_id: str | None = None
    detail: str | None = None


class Notifier(ABC):
    """One channel, one provider."""

    channel: NotifyChannel

    @abstractmethod
    async def send(self, message: Message) -> Delivery: ...

    async def aclose(self) -> None:  # noqa: B027 — not every provider holds a connection
        """Release connections."""


class NotifierError(RuntimeError):
    """The provider could not be reached at all. Carries a human message."""
