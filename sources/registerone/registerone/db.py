"""Connection plumbing for the fake POS.

Plain psycopg, no ORM: this is a foreign system we are pretending not to own,
and the less it looks like our own stack the better.
"""

from __future__ import annotations

import os

DEFAULT_DSN = "postgresql://registerone:registerone@127.0.0.1:5433/registerone"


def dsn() -> str:
    return os.environ.get("REGISTERONE_DSN", DEFAULT_DSN)
