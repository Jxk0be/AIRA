"""The notifiers that actually talk to something.

Three of them, and the one that matters most in day-to-day work is the console:
a dev machine has no Resend key and no Twilio number, and a feature that can
only be exercised by sending real mail is a feature nobody exercises.

Both real providers are called over plain HTTP rather than through a vendor
SDK. Each is one endpoint with three fields, and an SDK here would be a pinned
dependency and a mock to write for the sake of a POST.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.canonical.enums import NotifyChannel
from app.config import Settings, get_settings
from app.notify.base import Delivery, Message, Notifier

log = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"
TWILIO_ENDPOINT = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
TIMEOUT = 20.0


class ConsoleNotifier(Notifier):
    """Prints instead of sending, and keeps what it printed.

    `sent` is what tests assert against: a digest test should be able to check
    the subject line and the numbers in the body without a fake HTTP server.
    """

    channel = NotifyChannel.CONSOLE

    def __init__(self, echo: bool = True) -> None:
        self.echo = echo
        self.sent: list[Message] = []

    async def send(self, message: Message) -> Delivery:
        self.sent.append(message)
        if self.echo:
            log.info(
                "console notifier → %s [%s] %s\n%s",
                message.to,
                message.kind,
                message.subject,
                message.text,
            )
        return Delivery(ok=True, provider_message_id=f"console-{len(self.sent)}")


class EmailNotifier(Notifier):
    """Email through Resend."""

    channel = NotifyChannel.EMAIL

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self.settings.resend_api_key and self.settings.notify_from_email)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=TIMEOUT)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def send(self, message: Message) -> Delivery:
        if not self.configured:
            return Delivery(
                ok=False,
                detail="No RESEND_API_KEY / NOTIFY_FROM_EMAIL is configured, so nothing was sent.",
            )

        sender = self.settings.notify_from_email
        if self.settings.notify_from_name:
            sender = f"{self.settings.notify_from_name} <{sender}>"

        payload: dict[str, Any] = {
            "from": sender,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
        }
        if message.html:
            payload["html"] = message.html
        if message.reply_to:
            payload["reply_to"] = message.reply_to
        if message.headers:
            payload["headers"] = message.headers

        try:
            response = await self._http().post(
                RESEND_ENDPOINT,
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.resend_api_key}"},
            )
        except httpx.HTTPError as exc:
            return Delivery(ok=False, detail=f"Could not reach Resend: {exc}")

        if response.status_code >= 400:
            return Delivery(ok=False, detail=f"Resend said {response.status_code}: {response.text}")
        body = response.json()
        return Delivery(ok=True, provider_message_id=str(body.get("id") or ""))


class SmsNotifier(Notifier):
    """SMS through Twilio.

    Used for urgent alerts to the shop's own staff, who asked for them on the
    notifications screen — not for marketing to the shop's customers, which
    this product deliberately does not do.

    This class sends what it is given. Whether to send at all is decided by
    `app.notify.service`, which checks the recipient's channels, quiet hours
    and daily ceiling first. Keep it that way: a consent check inside a
    provider is a consent check that gets bypassed the first time somebody
    adds a second provider.
    """

    channel = NotifyChannel.SMS

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.twilio_account_sid
            and self.settings.twilio_auth_token
            and self.settings.twilio_from_number
        )

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=TIMEOUT)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def send(self, message: Message) -> Delivery:
        if not self.configured:
            return Delivery(
                ok=False, detail="No Twilio credentials are configured, so nothing was sent."
            )
        url = TWILIO_ENDPOINT.format(sid=self.settings.twilio_account_sid)
        try:
            response = await self._http().post(
                url,
                data={
                    "To": message.to,
                    "From": self.settings.twilio_from_number,
                    "Body": message.text,
                },
                auth=(self.settings.twilio_account_sid, self.settings.twilio_auth_token),
            )
        except httpx.HTTPError as exc:
            return Delivery(ok=False, detail=f"Could not reach Twilio: {exc}")

        if response.status_code >= 400:
            return Delivery(ok=False, detail=f"Twilio said {response.status_code}: {response.text}")
        body = response.json()
        return Delivery(ok=True, provider_message_id=str(body.get("sid") or ""))


def notifier_for(channel: NotifyChannel, settings: Settings | None = None) -> Notifier:
    """The notifier a channel resolves to, honouring the dev override.

    `NOTIFY_TRANSPORT=console` turns every channel into the console one, which
    is what a laptop and the test suite both want: the whole pipeline runs,
    prefs and quiet hours and logging included, and nothing leaves the machine.
    """
    settings = settings or get_settings()
    if settings.notify_transport == "console":
        return ConsoleNotifier()
    if channel is NotifyChannel.SMS:
        return SmsNotifier(settings)
    if channel is NotifyChannel.EMAIL:
        return EmailNotifier(settings)
    return ConsoleNotifier()
