"""When a job is next due, in the shop's own timezone.

Deliberately ours rather than a scheduling library's. Every schedule in this
product is per tenant and cut in that tenant's timezone — "Monday 7am" means
7am in Knoxville for one shop and 7am in Portland for another — and a library
that owns the clock wants one job per tenant per schedule registered up front,
which is a job table that grows with the customer list and has to be rebuilt
whenever a shop moves timezone.

The model here is the other way round: the worker ticks, and each tick asks
every schedule "what is the most recent instant you were due?". That instant is
what the run claims in `job_runs`, so a worker that was asleep across a due
time still does the work once, late, rather than never or twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

Kind = Literal["hourly", "daily", "weekly", "monthly"]


@dataclass(frozen=True, slots=True)
class Schedule:
    """A recurring shop-local instant.

    `weekday` follows Python: 0 is Monday. `day` is a day of the month, and a
    schedule for the 31st simply does not fire in February rather than
    quietly sliding to the 28th, because "the 1st of the month" is the only
    monthly schedule this product actually uses.
    """

    kind: Kind
    minute: int = 0
    hour: int = 0
    weekday: int = 0
    day: int = 1

    def __post_init__(self) -> None:
        if not 0 <= self.minute < 60:
            raise ValueError(f"minute out of range: {self.minute}")
        if not 0 <= self.hour < 24:
            raise ValueError(f"hour out of range: {self.hour}")
        if not 0 <= self.weekday < 7:
            raise ValueError(f"weekday out of range: {self.weekday}")
        if not 1 <= self.day <= 28:
            raise ValueError(f"day must be 1-28 so every month has one: {self.day}")

    def last_due(self, tz: ZoneInfo, now: datetime | None = None) -> datetime:
        """The most recent instant this was due, at or before `now`, in UTC."""
        local = (now or datetime.now(tz=UTC)).astimezone(tz)

        if self.kind == "hourly":
            candidate = local.replace(minute=self.minute, second=0, microsecond=0)
            if candidate > local:
                candidate -= timedelta(hours=1)
            return candidate.astimezone(UTC)

        candidate = local.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)

        if self.kind == "daily":
            if candidate > local:
                candidate -= timedelta(days=1)
            return candidate.astimezone(UTC)

        if self.kind == "weekly":
            # Walk back to the right weekday, then back one more week if that
            # lands in the future (today is the right weekday but too early).
            back = (candidate.weekday() - self.weekday) % 7
            candidate -= timedelta(days=back)
            if candidate > local:
                candidate -= timedelta(days=7)
            return candidate.astimezone(UTC)

        candidate = candidate.replace(day=self.day)
        if candidate > local:
            # The 1st of this month has not arrived yet, so it is last month's.
            first_of_this = candidate.replace(day=1)
            previous = first_of_this - timedelta(days=1)
            candidate = previous.replace(
                day=self.day, hour=self.hour, minute=self.minute, second=0, microsecond=0
            )
        return candidate.astimezone(UTC)

    def is_due_within(self, tz: ZoneInfo, grace: timedelta, now: datetime | None = None) -> bool:
        """Whether the last due instant is recent enough to still act on.

        The guard against a cold start doing a month of catching up: a worker
        booted on Friday should not send Monday's digest.
        """
        moment = now or datetime.now(tz=UTC)
        return moment - self.last_due(tz, moment) <= grace


# The product's schedules, named once so the worker, the docs and the Data &
# sync screen all agree on what "the weekly digest" means.
SYNC = Schedule("hourly", minute=5)
DETECTORS_DAILY = Schedule("daily", hour=6, minute=30)
DIGEST = Schedule("weekly", weekday=0, hour=7)
OUTCOMES = Schedule("daily", hour=2, minute=15)
MONTH_END = Schedule("monthly", day=1, hour=6)

# How late a run may be and still be worth doing. An hourly sync missed by six
# hours is worth catching up; a digest missed by four days is not.
GRACE: dict[str, timedelta] = {
    "sync": timedelta(hours=6),
    "detectors": timedelta(hours=12),
    "digest": timedelta(days=1),
    "outcomes": timedelta(hours=12),
    "month_end": timedelta(days=3),
}
