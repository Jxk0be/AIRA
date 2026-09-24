"""Asking a model for words, never for numbers.

Three features here put a model in front of a shop owner — the digest's
connecting sentences, a dead-stock suggestion's phrasing, and the series
parser's second pass. All three share one rule, and this module is where it is
enforced: **the model writes prose, the semantic layer writes figures.**

So every call here:

* gets a JSON payload of already-computed facts, and must answer in JSON
* is parsed and validated before anything reaches a person
* has a plain template to fall back to, and falls back silently rather than
  sending something unchecked

A model that is unreachable, slow or creative therefore costs a shop a slightly
duller email, not a wrong number.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any, TypeVar

from anthropic import APIError, AsyncAnthropic

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

T = TypeVar("T")

# A model asked for JSON sometimes wraps it in a fenced block anyway. Stripping
# that is worth four lines; re-prompting for it is not.
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class Phraser:
    """A thin wrapper over the cheap model, for text only.

    Holds no state beyond the client, so a test can hand in a scripted stand-in
    and get the same code path the real thing takes.
    """

    def __init__(
        self, settings: Settings | None = None, client: AsyncAnthropic | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client

    @property
    def available(self) -> bool:
        return bool(self._client or self.settings.anthropic_api_key)

    @property
    def client(self) -> AsyncAnthropic:
        if self._client is None:
            self._client = AsyncAnthropic(api_key=self.settings.anthropic_api_key)
        return self._client

    async def json(
        self,
        *,
        system: str,
        payload: dict[str, Any] | list[Any],
        instruction: str,
        parse: Callable[[Any], T],
        fallback: T,
        model: str | None = None,
        max_tokens: int = 1024,
    ) -> T:
        """Ask for JSON, validate it, and fall back when anything is off.

        `parse` is the caller's validator: it receives whatever came back and
        either returns the typed value or raises. Any exception at all — no
        key, no API key, a refusal, a timeout, a number that was not in the
        payload — produces `fallback`. Nothing unvalidated escapes.
        """
        if not self.available:
            return fallback

        prompt = (
            f"{instruction}\n\n"
            "Here are the facts. Do not introduce any number that is not in them.\n\n"
            f"{json.dumps(payload, default=str, indent=2)}\n\n"
            "Reply with JSON only."
        )
        try:
            response = await self.client.messages.create(
                model=model or self.settings.utility_model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except APIError as exc:
            log.warning("phrasing call failed, using the plain wording: %s", exc)
            return fallback
        except Exception as exc:  # a transport error must not cost a digest
            log.warning("phrasing call failed unexpectedly: %s", exc)
            return fallback

        text = "".join(block.text for block in response.content if block.type == "text").strip()
        try:
            return parse(json.loads(_FENCE.sub("", text).strip()))
        except Exception as exc:
            log.warning("model output rejected, using the plain wording: %s", exc)
            return fallback


# --------------------------------------------------------------------------
# The number check
# --------------------------------------------------------------------------

# Anything that looks like a figure: 1,234.50, $12, 7%, 3.1. Matched loosely on
# purpose — a validator that misses a number is worse than one that flags a
# harmless one, because the cost of a flag is a duller sentence.
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[str]:
    """Every figure in a string, normalised so 1,200 and 1200.00 agree."""
    return {_normalise(match.group()) for match in _NUMBER.finditer(text)}


def _normalise(raw: str) -> str:
    value = raw.replace(",", "")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value or "0"


def facts_from(payload: Any) -> set[str]:
    """Every figure anywhere in a payload, however deeply nested."""
    found: set[str] = set()
    stack: list[Any] = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list | tuple):
            stack.extend(item)
        elif isinstance(item, bool) or item is None:
            continue
        elif isinstance(item, int | float):
            found.add(_normalise(str(item)))
        elif isinstance(item, str):
            found.update(numbers_in(item))
        else:
            found.update(numbers_in(str(item)))
    return found


class InventedNumber(ValueError):
    """The model wrote a figure that is not in the facts it was given.

    Raised by a validator and caught by `Phraser.json`, which then sends the
    plain template. The one failure mode this whole module exists to prevent is
    an invented figure reaching a shop owner's inbox looking authoritative.
    """

    def __init__(self, offenders: set[str], text: str) -> None:
        super().__init__(
            f"the wording contains {sorted(offenders)}, which is not in the figures it was given"
        )
        self.offenders = offenders
        self.text = text


def check_numbers(text: str, facts: set[str], *, allow: set[str] | None = None) -> None:
    """Raise unless every figure in `text` came from the facts.

    Small integers are allowed through: "top 3 actions", "5 items", "last 7
    days" are counting words a writer needs, they are not claims about the
    shop's money, and a validator that rejects them rejects every readable
    sentence.
    """
    permitted = facts | (allow or set()) | {str(n) for n in range(0, 13)}
    offenders = numbers_in(text) - permitted
    if offenders:
        raise InventedNumber(offenders, text)
