"""Run the detectors for a shop and print what they found.

    python -m app.insights.cli --tenant animanga_knox
    python -m app.insights.cli --tenant animanga_knox --as-of 2026-09-23 --kind reorder

The fast way to see whether a detector works against real fixture data without
waiting for the worker's next round, and the thing to reach for when a finding
looks wrong: `--evidence` prints the exact numbers it was made from.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date

from app.analytics import TenantNotFound, load_context
from app.db import dispose_engine, get_sessionmaker
from app.insights import expire_stale, list_insights, run_detectors, value_ledger
from app.insights.registry import known, load_builtin_detectors


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True, help="tenant slug, e.g. animanga_knox")
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="pretend today is this date, YYYY-MM-DD",
    )
    parser.add_argument(
        "--kind",
        action="append",
        help="only these detectors (repeatable). Omit for all.",
    )
    parser.add_argument("--evidence", action="store_true", help="print each finding's numbers")
    parser.add_argument("--list", action="store_true", help="list the detectors and stop")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if args.list:
        load_builtin_detectors()
        print("\n  " + "\n  ".join(known()) + "\n")
        return 0

    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            try:
                ctx = await load_context(session, args.tenant)
            except TenantNotFound as exc:
                print(f"  {exc}")
                return 2

            expired = await expire_stale(session, ctx)
            await session.commit()

            runs = await run_detectors(
                session, ctx, args.as_of, kinds=tuple(args.kind) if args.kind else None
            )

            print(f"\n  {ctx.name} — detectors as of {args.as_of or ctx.today()}\n")
            for run in runs:
                if run.error:
                    outcome = f"FAILED  {run.error}"
                elif run.skipped_reason:
                    outcome = f"skipped ({run.skipped_reason})"
                else:
                    outcome = f"{run.created} new, {run.updated} updated"
                print(f"    {run.kind:20} {outcome}")
            if expired:
                print(f"\n    {expired} older findings expired")

            open_now = await list_insights(session, ctx, status="open", limit=25)
            print(f"\n  Open findings ({len(open_now)})\n")
            for insight in open_now:
                money = (
                    f"${insight.dollar_impact:,.0f}"
                    if insight.dollar_impact is not None
                    else "  —   "
                )
                print(f"    [{insight.severity!s:6}] {money:>10}  {insight.title}")
                print(f"                            {insight.summary[:150]}")
                if args.evidence:
                    print(
                        "                            "
                        + json.dumps(insight.evidence, indent=2, default=str).replace(
                            "\n", "\n                            "
                        )[:2000]
                    )
                print()

            today = args.as_of or ctx.today()
            ledger = await value_ledger(session, ctx, today.replace(day=1), today)
            print(
                f"  This month: {ledger.insights_created} found, {ledger.insights_acted} acted on, "
                f"${ledger.flagged_impact:,.0f} flagged, "
                f"${ledger.attributed_revenue:,.2f} attributed\n"
            )
            return 1 if any(run.error for run in runs) else 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
