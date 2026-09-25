"""The worker: what happens without anybody opening the app.

A minute-long loop asks, for each shop and each job, "what is the most recent
instant this was due, in that shop's own timezone, and has anybody claimed it?"
Claiming is an insert on `(tenant_id, job, due_at)`, which is what makes the
whole thing idempotent and what lets two workers run at once without talking.

    python -m app.jobs.worker
    python -m app.jobs.worker --once --tenant animanga_knox --job detectors
"""

from app.jobs.runner import (
    JobOutcome,
    JobSkipped,
    last_successful,
    recent_runs,
    run_once,
    sweep_stale,
)
from app.jobs.schedule import DETECTORS_DAILY, DIGEST, GRACE, MONTH_END, OUTCOMES, SYNC, Schedule
from app.jobs.tables import JobRun
from app.jobs.worker import ROUNDS, tick

__all__ = [
    "DETECTORS_DAILY",
    "DIGEST",
    "GRACE",
    "MONTH_END",
    "OUTCOMES",
    "ROUNDS",
    "SYNC",
    "JobOutcome",
    "JobRun",
    "JobSkipped",
    "Schedule",
    "last_successful",
    "recent_runs",
    "run_once",
    "sweep_stale",
    "tick",
]
