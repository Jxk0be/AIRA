"""Which adapter a tenant's integration row means.

The registry is the only place a platform name turns into code. Everything
downstream reads the integration row and the capabilities on it, never this.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.connectors.base import AdapterInfo, SourceAdapter

AdapterFactory = Callable[[dict[str, Any], str | None], SourceAdapter]

_ADAPTERS: dict[str, AdapterFactory] = {}
_DESCRIBERS: dict[str, Callable[[], AdapterInfo]] = {}


def register(name: str, factory: AdapterFactory, describe: Callable[[], AdapterInfo]) -> None:
    if name in _ADAPTERS:
        raise ValueError(f"adapter {name!r} is already registered")
    _ADAPTERS[name] = factory
    _DESCRIBERS[name] = describe


def build(name: str, config: dict[str, Any], secret: str | None = None) -> SourceAdapter:
    """Instantiate the adapter an integration row names.

    `config` is the integration's non-secret settings; `secret` is the
    credential a secret_ref resolved to, and is never persisted by us.
    """
    try:
        factory = _ADAPTERS[name]
    except KeyError:
        known = ", ".join(sorted(_ADAPTERS)) or "none registered"
        raise KeyError(f"unknown adapter {name!r}. Registered: {known}") from None
    return factory(config, secret)


def describe(name: str) -> AdapterInfo:
    """What an adapter can do, without connecting to anything."""
    return _DESCRIBERS[name]()


def available() -> list[str]:
    return sorted(_ADAPTERS)


def load_builtin_adapters() -> None:
    """Import the adapters that ship with AIRA so they register themselves.

    Called by the sync CLI and the API's startup. Importing `app.connectors`
    alone deliberately does not drag in every vendor SDK.
    """
    from app.connectors.adapters import mapping, registerone  # noqa: F401
