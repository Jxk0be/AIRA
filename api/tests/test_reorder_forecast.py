"""The reorder arithmetic, as arithmetic.

`app.reorder.forecast` is pure functions over measurements, which is the whole
reason it is a separate module: every rule an owner might argue with can be
checked here against a hand-built row, with no database, no clock and no
fixture to drift.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.analytics import DateRange
from app.analytics.demand import HORIZON_DAYS, DemandInputs, DemandRow
from app.reorder.forecast import (
    DEFAULT_LEAD_TIME_DAYS,
    SEASONAL_MAX,
    SEASONAL_MIN,
    build,
    round_to_pack,
    seasonal_factor,
    velocity_of,
)

TODAY = datetime(2026, 9, 24, tzinfo=UTC).date()


def row(**overrides: object) -> DemandRow:
    """A plain, steady seller. Every test changes one thing about it."""
    base: dict[str, object] = {
        "variant_id": uuid.uuid4(),
        "location_id": None,
        "location_name": "Gay St",
        "product_name": "Crimson Ronin Vol. 7",
        "variant_name": None,
        "sku": "MNG-CR-07",
        "category": "Manga",
        "on_hand": Decimal("4"),
        "price": Decimal("12.99"),
        "cost": Decimal("7.14"),
        "is_active": True,
        # Most recent week first.
        "weekly_units": (Decimal("3"), Decimal("3"), Decimal("3"), Decimal("3")),
        "last_sold_at": datetime(2026, 9, 23, tzinfo=UTC),
        "first_sold_at": datetime(2024, 1, 1, tzinfo=UTC),
        "lead_time_days": 14,
        "pack_size": Decimal("1"),
        "min_order_qty": Decimal("0"),
        "vendor_id": uuid.uuid4(),
        "vendor_name": "Paper Lantern Books",
        "unit_cost": Decimal("7.14"),
    }
    base.update(overrides)
    return DemandRow(**base)  # type: ignore[arg-type]


def inputs(*rows: DemandRow, **overrides: object) -> DemandInputs:
    window = DateRange(TODAY - timedelta(days=HORIZON_DAYS - 1), TODAY)
    out = DemandInputs(as_of=TODAY, window=window, rows=list(rows))
    for key, value in overrides.items():
        setattr(out, key, value)
    return out


# ---------------------------------------------------------------------------
# Velocity
# ---------------------------------------------------------------------------


def test_velocity_weights_recent_weeks_more() -> None:
    """Four of last week beats four of a month ago.

    A new volume taking off has to show up before the month is out, or the
    suggestion arrives after the customers have.
    """
    rising = velocity_of(
        row(weekly_units=(Decimal("8"), Decimal("4"), Decimal("2"), Decimal("0"))), 28
    )
    falling = velocity_of(
        row(weekly_units=(Decimal("0"), Decimal("2"), Decimal("4"), Decimal("8"))), 28
    )
    assert rising > falling


def test_velocity_ignores_days_the_shelf_was_empty() -> None:
    """Ten sold in six days is not 0.36 a day.

    The single most valuable correction in the whole forecast: an item that
    sold out is the one most worth reordering, and the naive rate makes it look
    like the one least worth reordering.
    """
    sold_out = row(weekly_units=(Decimal("10"), Decimal("0"), Decimal("0"), Decimal("0")))
    naive = velocity_of(sold_out, 28)

    sold_out.stockout_days = 22
    corrected = velocity_of(sold_out, 28)

    assert corrected > naive * 4


def test_velocity_is_zero_when_nothing_sold() -> None:
    assert velocity_of(row(weekly_units=(Decimal("0"),) * 4), 28) == Decimal("0")


# ---------------------------------------------------------------------------
# Seasonality
# ---------------------------------------------------------------------------


def test_no_seasonal_factor_without_a_year_of_history() -> None:
    """A shop three months old has no season, and inventing one from a single
    data point is how a December order gets placed in March."""
    assert seasonal_factor(
        row(last_year_units=Decimal("40"), trailing_year_span_units=Decimal("10")),
        available=False,
    ) == Decimal("1")


def test_seasonal_factor_is_clamped_both_ways() -> None:
    """One convention weekend last year is not a season."""
    wild = seasonal_factor(
        row(last_year_units=Decimal("400"), trailing_year_span_units=Decimal("10")),
        available=True,
    )
    dead = seasonal_factor(
        row(last_year_units=Decimal("1"), trailing_year_span_units=Decimal("100")),
        available=True,
    )
    assert wild == SEASONAL_MAX
    assert dead == SEASONAL_MIN


def test_seasonal_factor_scales_the_order() -> None:
    quiet = build(inputs(row(), seasonality_unavailable=False)).suggestions[0]
    busy = build(
        inputs(
            row(last_year_units=Decimal("24"), trailing_year_span_units=Decimal("12")),
            seasonality_unavailable=False,
        )
    ).suggestions[0]
    assert busy.seasonal_factor == Decimal("2")
    assert busy.suggested_qty > quiet.suggested_qty


# ---------------------------------------------------------------------------
# Rounding to what a vendor will accept
# ---------------------------------------------------------------------------


def test_pack_size_rounds_up() -> None:
    assert round_to_pack(Decimal("7"), Decimal("6"), Decimal("0")) == Decimal("12")
    assert round_to_pack(Decimal("12"), Decimal("6"), Decimal("0")) == Decimal("12")


def test_minimum_order_is_respected_and_still_lands_on_a_pack() -> None:
    assert round_to_pack(Decimal("2"), Decimal("6"), Decimal("24")) == Decimal("24")


def test_nothing_wanted_orders_nothing() -> None:
    assert round_to_pack(Decimal("0"), Decimal("6"), Decimal("24")) == Decimal("0")


def test_pack_rounding_reaches_the_suggestion() -> None:
    suggestion = build(inputs(row(pack_size=Decimal("6")))).suggestions[0]
    assert suggestion.suggested_qty % Decimal("6") == 0
    assert "packs of 6" in suggestion.explanation


# ---------------------------------------------------------------------------
# What not to reorder
# ---------------------------------------------------------------------------


def test_discontinued_items_are_skipped() -> None:
    forecast = build(inputs(row(is_active=False)))
    assert not forecast.suggestions
    assert forecast.skipped["discontinued"] == 1


def test_one_off_inventory_is_skipped() -> None:
    """Singles are bought one at a time by whoever traded them in; suggesting a
    reorder of one is suggesting the shop buy a card back from itself."""
    forecast = build(inputs(row(category="TCG Singles")))
    assert not forecast.suggestions
    assert forecast.skipped["one-off inventory"] == 1


def test_what_is_already_on_order_is_subtracted() -> None:
    """Reviewing the reorder twice in a week must not order everything twice."""
    item = row()
    alone = build(inputs(item)).suggestions[0]
    with_order = build(inputs(item), on_order={item.variant_id: Decimal("50")})
    assert alone.suggested_qty > 0
    assert not with_order.suggestions


# ---------------------------------------------------------------------------
# Honesty about what is missing
# ---------------------------------------------------------------------------


def test_missing_cost_still_gets_a_suggestion_with_a_caveat() -> None:
    """A shop with no costs still needs to know what to buy. It just cannot be
    told what the order is worth."""
    suggestion = build(inputs(row(cost=None, unit_cost=None))).suggestions[0]
    assert suggestion.suggested_qty > 0
    assert suggestion.line_cost is None
    assert any("no cost recorded" in caveat for caveat in suggestion.caveats)


def test_missing_lead_time_is_assumed_and_said_so() -> None:
    suggestion = build(inputs(row(lead_time_days=None))).suggestions[0]
    assert suggestion.lead_time_days == DEFAULT_LEAD_TIME_DAYS
    assert suggestion.lead_time_assumed
    assert "assumed" in suggestion.explanation


def test_no_stock_history_is_reported_as_a_caveat_not_hidden() -> None:
    forecast = build(inputs(row(), stockouts_unknown=True))
    assert any("sold out" in caveat for caveat in forecast.caveats)


def test_a_variant_with_no_vendor_says_so() -> None:
    suggestion = build(inputs(row(vendor_id=None, vendor_name=None))).suggestions[0]
    assert any("no vendor" in caveat for caveat in suggestion.caveats)


# ---------------------------------------------------------------------------
# The explanation
# ---------------------------------------------------------------------------


def test_every_suggestion_can_explain_itself() -> None:
    """The rule the whole feature rests on: a number an owner cannot argue with
    is a number they will not act on."""
    forecast = build(
        inputs(
            row(),
            row(pack_size=Decimal("6"), on_hand=Decimal("1")),
            row(cost=None, unit_cost=None, lead_time_days=None),
        )
    )
    assert len(forecast.suggestions) == 3
    for suggestion in forecast.suggestions:
        assert "sells" in suggestion.explanation
        assert "on hand" in suggestion.explanation
        assert "lead time" in suggestion.explanation


def test_urgent_lines_sort_first() -> None:
    """What will be gone before the van arrives goes at the top."""
    comfortable = row(on_hand=Decimal("40"))
    desperate = row(on_hand=Decimal("1"))
    forecast = build(inputs(comfortable, desperate))
    assert forecast.suggestions[0].variant_id == desperate.variant_id
    assert forecast.suggestions[0].stocks_out_before_delivery
