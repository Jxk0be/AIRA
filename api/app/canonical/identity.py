"""Turning a contact detail into something two rows can be matched on.

Lives in the canonical layer rather than with the deduplication code that first
needed it, because more than one thing now depends on agreeing about it: the
sync engine writes `email_normalized` and `phone_normalized` with these, the
dedupe pass matches on them, and anything that later has to decide whether two
rows are the same person has to use the same rule.

If they ever disagreed, somebody would be merged into a stranger.
"""

from __future__ import annotations

import re

_NON_DIGITS = re.compile(r"\D")


def normalise_email(email: str | None) -> str | None:
    if not email:
        return None
    cleaned = email.strip().lower()
    return cleaned or None


def normalise_phone(phone: str | None) -> str | None:
    """Digits only, with a leading US country code dropped.

    Deliberately naive about international numbers: guessing wrong would merge
    two different people, which is far worse than leaving a duplicate.
    """
    if not phone:
        return None
    digits = _NON_DIGITS.sub("", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) >= 7 else None


__all__ = ["normalise_email", "normalise_phone"]
