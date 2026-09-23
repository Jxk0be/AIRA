"""Resolving a `secret_ref` into an actual credential.

We store a pointer, never the secret. In development the pointer is
`env:NAME`; a hosted deploy swaps this for a real secret manager without any
adapter noticing.
"""

from __future__ import annotations

import os


class SecretNotFound(RuntimeError):
    pass


def resolve(secret_ref: str | None) -> str | None:
    if not secret_ref:
        return None
    scheme, _, rest = secret_ref.partition(":")
    if scheme == "env":
        value = os.environ.get(rest)
        if value is None:
            raise SecretNotFound(
                f"{secret_ref} points at the environment variable {rest}, which is not set"
            )
        return value
    if scheme == "literal":
        # Dev convenience only. Never use this for a real customer.
        return rest
    raise SecretNotFound(f"unsupported secret reference scheme {scheme!r} in {secret_ref!r}")
