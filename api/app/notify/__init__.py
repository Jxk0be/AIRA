"""Getting a message to a shop owner, on the terms they agreed to.

One interface over email (Resend), SMS (Twilio) and a console notifier that
prints instead of sending. Every feature that reaches a human goes through
`deliver` or `broadcast`, so the rules about quiet hours, channel consent and
how many messages a day is too many are enforced once rather than per feature.

Nothing leaves the machine unless `NOTIFY_TRANSPORT=live`.
"""

from app.notify.base import Delivery, Message, Notifier, NotifierError
from app.notify.providers import ConsoleNotifier, EmailNotifier, SmsNotifier, notifier_for
from app.notify.service import (
    Recipient,
    SendResult,
    app_link,
    broadcast,
    deliver,
    in_quiet_hours,
    recent_messages,
    recipients,
    unsubscribe,
    unsubscribe_url,
    upsert_recipient,
)
from app.notify.tables import NotificationPref, OutboundMessage

__all__ = [
    "ConsoleNotifier",
    "Delivery",
    "EmailNotifier",
    "Message",
    "NotificationPref",
    "Notifier",
    "NotifierError",
    "OutboundMessage",
    "Recipient",
    "SendResult",
    "SmsNotifier",
    "app_link",
    "broadcast",
    "deliver",
    "in_quiet_hours",
    "notifier_for",
    "recent_messages",
    "recipients",
    "unsubscribe",
    "unsubscribe_url",
    "upsert_recipient",
]
