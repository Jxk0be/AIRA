"""Print the digest a shop would get on Monday, without sending it.

    python -m app.digest.cli --tenant tsundoku
    python -m app.digest.cli --tenant tsundoku --as-of 2026-09-23 --html out.html
    python -m app.digest.cli --tenant tsundoku --send

`--send` goes through the real pipeline — prefs, quiet hours, the daily
ceiling, the log — and still sends nothing anywhere unless `NOTIFY_TRANSPORT`
is `live`. That is deliberate: the interesting failures are in the deciding,
not in the SMTP.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date
from pathlib import Path

from app.analytics import TenantNotFound, load_context
from app.db import dispose_engine, get_sessionmaker
from app.digest import DigestSkipped, prepare
from app.digest import send as send_digest


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True, help="tenant slug, e.g. tsundoku")
    parser.add_argument(
        "--as-of", type=date.fromisoformat, default=None, help="pretend today is this date"
    )
    parser.add_argument("--html", type=Path, help="also write the HTML version here")
    parser.add_argument("--payload", action="store_true", help="print the facts it was built from")
    parser.add_argument(
        "--send",
        action="store_true",
        help="run the real send (console transport unless NOTIFY_TRANSPORT=live)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            try:
                ctx = await load_context(session, args.tenant)
            except TenantNotFound as exc:
                print(f"  {exc}")
                return 2

            prepared = await prepare(session, ctx, as_of=args.as_of)
            print()
            print(f"  Subject: {prepared.subject}")
            print(f"  Wording: {prepared.rendered.copy_source}")
            print("  " + "-" * 68)
            for line in prepared.rendered.text.splitlines():
                print(f"  {line}")
            print()

            if args.payload:
                print(json.dumps(prepared.digest.payload(), indent=2, default=str))
                print()

            if args.html:
                args.html.write_text(prepared.rendered.html, encoding="utf-8")
                print(f"  HTML written to {args.html}\n")

            if args.send:
                try:
                    results = await send_digest(session, ctx, as_of=args.as_of)
                except DigestSkipped as skipped:
                    print(f"  Not sent: {skipped.reason}\n")
                    return 0
                if not results:
                    print("  Nobody at this shop has asked for the digest.\n")
                    return 0
                for result in results:
                    mark = "sent" if result.sent else str(result.status)
                    print(f"    {result.recipient:34} {mark:10} {result.detail or ''}")
                print()
            return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
