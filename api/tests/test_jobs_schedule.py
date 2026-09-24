"""When a job was last due, in a shop's own timezone.

The worker's whole correctness argument rests on this one function: it asks
each schedule for the most recent instant it was due, and that instant is what
a run claims. Get it wrong and a digest goes out twice, or never.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.jobs.schedule import (
    DETECTORS_DAILY,
    DIGEST,
    GRACE,
    MONTH_END,
    SYNC,
    Schedule,
)

KNOXVILLE = ZoneInfo("America/New_York")
PORTLAND = ZoneInfo("America/Los_Angeles")


def utc(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# ---------------------------------------------------------------------------
# The shapes
# ---------------------------------------------------------------------------


def test_hourly_looks_back_to_the_last_turn_of_the_hour() -> None:
    due = SYNC.last_due(KNOXVILLE, utc(2026, 9, 24, 14, 30))
    assert due == utc(2026, 9, 24, 14, 5)


def test_hourly_before_the_minute_falls_back_an_hour() -> None:
    due = SYNC.last_due(KNOXVILLE, utc(2026, 9, 24, 14, 2))
    assert due == utc(2026, 9, 24, 13, 5)


def test_daily_is_cut_in_shop_time() -> None:
    """02:15 local, which is a different UTC instant in each shop."""
    knoxville = Schedule("daily", hour=2, minute=15).last_due(KNOXVILLE, utc(2026, 9, 24))
    portland = Schedule("daily", hour=2, minute=15).last_due(PORTLAND, utc(2026, 9, 24))
    assert knoxville == utc(2026, 9, 24, 6, 15)
    assert portland == utc(2026, 9, 24, 9, 15)
    assert knoxville != portland


def test_weekly_finds_the_monday_just_gone() -> None:
    """Thursday's tick is still working for Monday 7am."""
    due = DIGEST.last_due(KNOXVILLE, utc(2026, 9, 24))  # a Thursday
    assert due == utc(2026, 9, 21, 11)  # Monday 07:00 EDT
    assert due.astimezone(KNOXVILLE).weekday() == 0


def test_weekly_on_the_day_but_before_the_hour_goes_back_a_week() -> None:
    """Monday at 06:00 local is not yet Monday's digest."""
    monday_early = datetime(2026, 9, 21, 6, 0, tzinfo=KNOXVILLE).astimezone(UTC)
    due = DIGEST.last_due(KNOXVILLE, monday_early)
    assert due.astimezone(KNOXVILLE).date().isoformat() == "2026-09-14"


def test_monthly_on_the_first_before_the_hour_is_last_month() -> None:
    first_early = datetime(2026, 9, 1, 3, 0, tzinfo=KNOXVILLE).astimezone(UTC)
    due = MONTH_END.last_due(KNOXVILLE, first_early)
    assert due.astimezone(KNOXVILLE).date().isoformat() == "2026-08-01"


def test_monthly_after_the_hour_is_this_month() -> None:
    later = datetime(2026, 9, 14, 9, 0, tzinfo=KNOXVILLE).astimezone(UTC)
    assert MONTH_END.last_due(KNOXVILLE, later).astimezone(KNOXVILLE).day == 1


# ---------------------------------------------------------------------------
# The awkward days
# ---------------------------------------------------------------------------


def test_a_due_time_never_moves_forward() -> None:
    """Whatever the schedule, "last due" is in the past. A due time in the
    future would be claimed early and then never run for real."""
    now = utc(2026, 3, 8, 9)  # the US spring-forward Sunday
    for schedule in (SYNC, DETECTORS_DAILY, DIGEST, MONTH_END):
        assert schedule.last_due(KNOXVILLE, now) <= now


def test_ticking_twice_inside_one_period_claims_the_same_instant() -> None:
    """This is what makes the whole worker idempotent: two ticks nine minutes
    apart produce the same `due_at`, and the second insert loses."""
    first = DETECTORS_DAILY.last_due(KNOXVILLE, utc(2026, 9, 24, 14, 0))
    second = DETECTORS_DAILY.last_due(KNOXVILLE, utc(2026, 9, 24, 14, 9))
    assert first == second


def test_consecutive_periods_claim_different_instants() -> None:
    today = DETECTORS_DAILY.last_due(KNOXVILLE, utc(2026, 9, 24, 14))
    tomorrow = DETECTORS_DAILY.last_due(KNOXVILLE, utc(2026, 9, 25, 14))
    assert tomorrow - today == timedelta(days=1)


# ---------------------------------------------------------------------------
# Validation and grace
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [{"minute": 60}, {"hour": 24}, {"weekday": 7}, {"day": 0}, {"day": 31}],
)
def test_nonsense_schedules_are_refused_at_construction(kwargs: dict[str, int]) -> None:
    """A day-31 monthly schedule simply would not fire in February, so it is
    rejected rather than silently skipping a month."""
    with pytest.raises(ValueError):
        Schedule("daily", **kwargs)


def test_every_job_has_a_grace_window() -> None:
    """A worker booted on Friday should not send Monday's digest, and the only
    thing stopping it is this table being complete."""
    assert set(GRACE) == {"sync", "detectors", "digest", "outcomes", "month_end"}
    assert GRACE["digest"] < GRACE["month_end"]


def test_a_stale_due_time_falls_outside_its_grace() -> None:
    friday = utc(2026, 9, 25, 14)
    assert not DIGEST.is_due_within(KNOXVILLE, GRACE["digest"], friday)
    monday_morning = datetime(2026, 9, 21, 9, 0, tzinfo=KNOXVILLE).astimezone(UTC)
    assert DIGEST.is_due_within(KNOXVILLE, GRACE["digest"], monday_morning)
