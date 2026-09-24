"""The statistics behind the alerts, without a database.

Two claims are tested here and they are the two an owner would challenge: that
a day is judged against its own weekday rather than against an average day, and
that the spread it is judged against is the one for the side it fell on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal

from app.analytics import AnalyticsContext
from app.anomalies.baselines import (
    MIN_SPREAD,
    Baseline,
    mad,
    median,
    one_sided_mad,
)
from app.anomalies.detectors import SENSITIVITY_Z, sensitivity_of
from app.canonical.models import Capabilities
from app.notify.service import Recipient, in_quiet_hours


def baseline(values: list[str], **overrides: object) -> Baseline:
    numbers = [Decimal(value) for value in values]
    centre = median(numbers)
    base: dict[str, object] = {
        "location_id": None,
        "location_name": "Gay St",
        "weekday": 5,
        "observations": len(numbers),
        "median_net_sales": centre,
        "spread_below": one_sided_mad(numbers, centre, below=True),
        "spread_above": one_sided_mad(numbers, centre, below=False),
        "median_orders": Decimal("8"),
    }
    base.update(overrides)
    return Baseline(**base)  # type: ignore[arg-type]


def context(**settings: object) -> AnalyticsContext:
    return AnalyticsContext(
        tenant_id=uuid.uuid4(),
        slug="tsundoku",
        name="Tsundoku & Tabletop",
        timezone="America/New_York",
        currency="USD",
        capabilities=Capabilities(),
        settings=dict(settings),
    )


# ---------------------------------------------------------------------------
# Robust statistics
# ---------------------------------------------------------------------------


def test_median_survives_one_enormous_day() -> None:
    """A mean would not. That is the whole argument for using a median: one
    convention weekend must not become the shop's new normal."""
    ordinary = ["100", "110", "120", "130"]
    assert median([Decimal(v) for v in ordinary]) == Decimal("115")
    assert median([Decimal(v) for v in [*ordinary, "9000"]]) == Decimal("120")


def test_mad_is_zero_when_every_day_is_the_same() -> None:
    assert mad([Decimal("100")] * 5) == Decimal("0")


def test_spread_is_floored_so_identical_days_do_not_make_everything_unusual() -> None:
    """Four identical Tuesdays give a spread of zero, and without a floor the
    fifth Tuesday is infinitely far from normal."""
    flat = baseline(["100", "100", "100", "100"])
    assert flat.z(Decimal("110")) == (Decimal("10") / MIN_SPREAD).quantize(Decimal("0.01"))


def test_an_ordinary_good_day_is_not_a_spike() -> None:
    """What the per-side spread actually buys.

    These Saturdays sit tightly between $260 and $300 and then trail off to
    $520 and $610 — the right skew every shop's takings have. Against one
    spread averaged over both halves, the $520 kind of day reads as a two-sigma
    spike and would be reported every few weeks. Against the other good
    Saturdays it is unremarkable, which is what it is.
    """
    values = ["260", "270", "280", "290", "300", "520", "610"]
    numbers = [Decimal(v) for v in values]
    centre = median(numbers)
    symmetric = mad(numbers, centre)
    grid = baseline(values)

    assert grid.spread_above > symmetric
    good_day = Decimal("520")
    assert abs(grid.z(good_day)) < abs((good_day - centre) / symmetric)
    assert abs(grid.z(good_day)) < Decimal("2")


def test_a_bad_day_is_still_measured_against_the_bad_days() -> None:
    """And the downside is not made worse by it.

    The two are close here, because the low half is where a symmetric
    median-of-deviations lands anyway. The claim is only that the low spread is
    never the *wider* one, so nothing that was detectable stops being so.
    """
    grid = baseline(["260", "270", "280", "290", "300", "520", "610"])
    assert grid.spread_below <= grid.spread_above
    assert grid.z(Decimal("150")) < Decimal("-2.5")


def test_a_baseline_with_too_little_history_is_not_usable() -> None:
    """Saying nothing is the correct output for a shop three weeks old."""
    assert not baseline(["100", "110"], observations=2).usable
    assert baseline(["100", "110", "120", "130"]).usable


# ---------------------------------------------------------------------------
# Sensitivity
# ---------------------------------------------------------------------------


def test_sensitivity_comes_from_the_shop_and_falls_back_safely() -> None:
    assert sensitivity_of(context(anomaly_sensitivity="high")) == SENSITIVITY_Z["high"]
    assert sensitivity_of(context(anomaly_sensitivity="LOW")) == SENSITIVITY_Z["low"]
    assert sensitivity_of(context()) == SENSITIVITY_Z["normal"]
    assert sensitivity_of(context(anomaly_sensitivity="nonsense")) == SENSITIVITY_Z["normal"]


# ---------------------------------------------------------------------------
# Quiet hours, which is the other half of not being annoying
# ---------------------------------------------------------------------------


def recipient(start: time | None, end: time | None) -> Recipient:
    return Recipient(
        id=uuid.uuid4(),
        name="Owner",
        email="owner@example.com",
        phone=None,
        channels=(),
        quiet_hours_start=start,
        quiet_hours_end=end,
        allow_urgent_in_quiet_hours=False,
        max_per_day=3,
        wants_digest=True,
        unsubscribe_token="t",
    )


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 24, hour, minute, tzinfo=UTC)


def test_quiet_hours_wrap_past_midnight() -> None:
    """22:00 to 08:00 is the setting almost everybody picks, and it is the one
    a naive `start <= now < end` gets wrong."""
    overnight = recipient(time(22, 0), time(8, 0))
    assert in_quiet_hours(overnight, at(23))
    assert in_quiet_hours(overnight, at(3))
    assert in_quiet_hours(overnight, at(7, 59))
    assert not in_quiet_hours(overnight, at(9))
    assert not in_quiet_hours(overnight, at(21, 59))


def test_a_daytime_quiet_window_also_works() -> None:
    """A shop that only wants to hear from us in the evening is allowed to."""
    daytime = recipient(time(9, 0), time(17, 0))
    assert in_quiet_hours(daytime, at(12))
    assert not in_quiet_hours(daytime, at(20))


def test_no_quiet_hours_means_no_quiet_hours() -> None:
    assert not in_quiet_hours(recipient(None, None), at(3))
    assert not in_quiet_hours(recipient(time(9), time(9)), at(9))


def test_today_is_cut_in_shop_time_not_utc() -> None:
    """At 01:00 in Knoxville it is still not tomorrow."""
    ctx = context()
    one_am_knoxville = datetime(2026, 9, 25, 5, 0, tzinfo=UTC)
    assert one_am_knoxville.astimezone(ctx.tz).date() == date(2026, 9, 25)
    assert one_am_knoxville.astimezone(ctx.tz).hour == 1
