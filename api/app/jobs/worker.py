"""The worker: a loop, a clock, and one round per tick.

    python -m app.jobs.worker
    python -m app.jobs.worker --once --tenant tsundoku

Deliberately not a scheduling framework. Every schedule in this product is per
tenant and cut in that tenant's own timezone, so what a framework would give us
— a registry of jobs on a global clock — is the part that does not fit. What is
left is a minute-long sleep and a question: for each shop, for each job, what
is the most recent instant it was due, and has anybody claimed it?

That design has three properties worth the twenty lines it costs. A worker that
was asleep across a due time still does the work, once, late. Two workers can
run at the same time without coordinating, because claiming is an insert. And a
shop that moves timezone is correct on the next tick with nothing to rebuild.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import partial
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext, TenantNotFound, load_context
from app.canonical.enums import JobStatus
from app.db import dispose_engine, get_sessionmaker
from app.jobs import schedule as schedules
from app.jobs import tasks
from app.jobs.runner import JobOutcome, run_once, sweep_stale
from app.jobs.schedule import Schedule

log = logging.getLogger("app.jobs.worker")

TICK_SECONDS = 60

Job = Callable[[AsyncSession, AnalyticsContext], Awaitable[dict[str, Any]]]

# Name, schedule, body. The order matters within a tick: detectors run after a
# sync so they see this hour's sales, and the digest runs after the detectors
# so its top three are current.
ROUNDS: tuple[tuple[str, Schedule, Job], ...] = (
    ("sync", schedules.SYNC, tasks.sync),
    ("detectors", schedules.DETECTORS_DAILY, tasks.detectors),
    ("digest", schedules.DIGEST, tasks.digest),
    ("outcomes", schedules.OUTCOMES, tasks.outcomes),
    ("month_end", schedules.MONTH_END, tasks.month_end),
)


async def tick(
    session: AsyncSession,
    *,
    only: str | None = None,
    jobs: tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> list[JobOutcome]:
    """One round: every shop, every job that is due and unclaimed."""
    moment = now or datetime.now(tz=UTC)
    slugs = [only] if only else await tasks.active_tenants(session)

    outcomes: list[JobOutcome] = []
    for slug in slugs:
        try:
            ctx = await load_context(session, slug)
        except TenantNotFound:
            log.warning("tenant %s has gone away", slug)
            continue

        for name, plan, body in ROUNDS:
            if jobs and name not in jobs:
                continue
            due_at = plan.last_due(ctx.tz, moment)
            grace = schedules.GRACE.get(name)
            if grace is not None and moment - due_at > grace:
                # Too late to be worth doing. A worker booted on Friday should
                # not send Monday's digest.
                continue

            # partial, not a lambda: it binds this round's `body` and `ctx`
            # the same way, and mypy can see through it to `run_once`'s
            # Callable[[], Awaitable[...]].
            outcome = await run_once(session, ctx, name, due_at, partial(body, session, ctx))
            outcomes.append(outcome)

            if outcome.status is JobStatus.FAILED and name == "sync":
                # Everything after a failed sync would be reasoning about data
                # we know is stale. The stale-data detector is the one thing
                # with something true to say, and tomorrow's round will run it.
                log.warning("%s: sync failed, skipping the rest of this round", slug)
                break

    return outcomes


async def run_forever(*, only: str | None = None) -> None:
    stopping = asyncio.Event()

    def stop(*_: object) -> None:
        log.info("stopping after this round")
        stopping.set()

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), stop)
            except ValueError:  # pragma: no cover — not on the main thread
                pass

    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            await sweep_stale(session)
        while not stopping.is_set():
            async with sessionmaker() as session:
                try:
                    outcomes = await tick(session, only=only)
                except Exception:
                    log.exception("a round failed; carrying on")
                    outcomes = []
            for outcome in outcomes:
                if outcome.status is not JobStatus.SKIPPED:
                    log.info("%s", outcome.line())
            try:
                await asyncio.wait_for(stopping.wait(), timeout=TICK_SECONDS)
            except TimeoutError:
                pass
    finally:
        await dispose_engine()


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", help="only this shop, by slug")
    parser.add_argument(
        "--once", action="store_true", help="do one round and stop, for cron or a test"
    )
    parser.add_argument(
        "--job",
        action="append",
        choices=[name for name, _, _ in ROUNDS],
        help="only these jobs (repeatable)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if not args.once:
        await run_forever(only=args.tenant)
        return 0

    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            await sweep_stale(session)
            outcomes = await tick(
                session, only=args.tenant, jobs=tuple(args.job) if args.job else None
            )
        print()
        for outcome in outcomes:
            print(f"  {outcome.line()}")
        print()
        return 1 if any(o.status is JobStatus.FAILED for o in outcomes) else 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
