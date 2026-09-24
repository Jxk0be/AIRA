"""Re-embed everything for a tenant.

    python -m app.rag.reembed --tenant tsundoku

For when the model or the dimension changes. Ordinary ingest skips chunks whose
content and model are unchanged, which is what keeps it free to run after every
sync; this is the escape hatch for the case where the text is the same but the
vectors are no longer worth anything.

Nothing is deleted first. Each chunk is overwritten in place, so search keeps
working against the old vectors right up until the new one lands.
"""

from __future__ import annotations

import asyncio
import sys

from app.rag.cli import main as ingest_main


def run(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if "--force" not in args:
        args.append("--force")
    return asyncio.run(ingest_main(args))


if __name__ == "__main__":
    raise SystemExit(run())
