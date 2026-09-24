"""How many to order, and why.

Deliberately arithmetic, not machine learning. Three things decide almost every
small-shop reorder — how fast it is selling, how long the vendor takes, and
what the same weeks looked like last year — and a model that beat this by a few
percent would cost the one thing that actually matters here: an owner has to be
able to read the suggestion and disagree with it.

Every number below is reachable from the sentence stored on the suggestion.
"sold 3.1 a week, 4 on hand, 14-day lead time, holiday factor 1.6" is the whole
calculation, in the order it happened.

Pure functions over `app.analytics.demand`'s measurements: no session, no
clock, no I/O. That is what makes the table of worked examples in the tests a
real test rather than a fixture.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from app.analytics.demand import HORIZON_DAYS, WEEK_DAYS, DemandInputs, DemandRow

# Recent weeks count for more. Four-to-one across a month is enough to notice a
# new release taking off without letting one good Saturday set the order.
WEEK_WEIGHTS: tuple[int, ...] = (4, 3, 2, 1)

# How long after an order arrives before the next one could. A shop orders
# weekly, so a delivery has to cover the lead time plus one more ordering
# cycle or it runs out while waiting for the next van.
REVIEW_PERIOD_DAYS = 7

# What we assume when nobody has told us a lead time. Two weeks is the honest
# middle for a distributor and is stated in the explanation rather than hidden.
DEFAULT_LEAD_TIME_DAYS = 14

# A seasonal factor outside this band is almost always an artefact — one
# convention weekend last year, or an item that did not exist yet — rather than
# a season. Clamped rather than discarded so a real Christmas still shows up.
SEASONAL_MIN = Decimal("0.5")
SEASONAL_MAX = Decimal("2.5")

# Above this many units a week an item is a steady seller and gets a full week
# of safety stock; below it, a third of that. Carrying a week of cover for
# something that sells one a fortnight is how a stockroom fills up.
STEADY_WEEKLY_UNITS = Decimal("3")
SLOW_SAFETY_WEEKS = Decimal("0.33")

# Items nobody should be told to reorder. Matched against the category name,
# case-insensitively; a tenant can add its own in settings.
DEFAULT_SKIP_CATEGORIES = ("singles", "single", "consignment", "trade-in", "used")

UNITS = Decimal("0.001")


@dataclass(slots=True)
class Suggestion:
    """One line of a reorder, with the whole calculation attached."""

    variant_id: uuid.UUID
    location_id: uuid.UUID | None
    location_name: str | None
    label: str
    sku: str | None
    category: str | None
    on_hand: Decimal
    # Units a day, after weighting recent weeks and excluding days at zero.
    velocity: Decimal
    seasonal_factor: Decimal
    horizon_days: int
    demand: Decimal
    safety_stock: Decimal
    suggested_qty: Decimal
    # Before pack-size rounding and the vendor's minimum, so the explanation
    # can say "rounded 7 up to 12".
    raw_qty: Decimal
    pack_size: Decimal
    min_order_qty: Decimal
    lead_time_days: int
    lead_time_assumed: bool
    unit_cost: Decimal | None
    # What it sells for. Needed to price a stockout in lost sales; None for a
    # variable-priced item rung up at the register.
    unit_price: Decimal | None
    vendor_id: uuid.UUID | None
    vendor_name: str | None
    explanation: str
    caveats: tuple[str, ...] = ()
    # Days until the shelf is empty at the current rate, or None if it is
    # already empty or not moving.
    days_of_cover: Decimal | None = None

    @property
    def line_cost(self) -> Decimal | None:
        if self.unit_cost is None:
            return None
        return (self.suggested_qty * self.unit_cost).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    @property
    def stocks_out_before_delivery(self) -> bool:
        """Will this run out before a van could get here?

        The test for whether a suggestion is worth interrupting someone about,
        as opposed to something to put on the weekly order.
        """
        return self.days_of_cover is not None and self.days_of_cover < self.lead_time_days


@dataclass(slots=True)
class Forecast:
    """Every suggestion from one run, plus what it could not account for."""

    suggestions: list[Suggestion] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)

    @property
    def total_at_cost(self) -> Decimal:
        return sum(
            (s.line_cost for s in self.suggestions if s.line_cost is not None), Decimal("0")
        )

    @property
    def cost_coverage(self) -> Decimal | None:
        """Share of suggested lines we can actually price."""
        if not self.suggestions:
            return None
        priced = sum(1 for s in self.suggestions if s.unit_cost is not None)
        return (Decimal(priced) / Decimal(len(self.suggestions))).quantize(Decimal("0.01"))


def velocity_of(row: DemandRow, window_days: int) -> Decimal:
    """Units a day, weighting recent weeks and ignoring days at zero stock.

    The stockout exclusion matters more than the weighting. An item that sold
    ten in the six days it was on the shelf and nothing in the other
    twenty-two did not sell 0.36 a day; it sold 1.67 a day and was out of stock
    for three weeks, which is exactly the item most worth reordering.
    """
    weights = WEEK_WEIGHTS[: len(row.weekly_units)]
    if not weights:
        return Decimal("0")
    weighted = sum(
        (units * weight for units, weight in zip(row.weekly_units, weights, strict=False)),
        Decimal("0"),
    )
    per_week = weighted / Decimal(sum(weights))
    per_day = per_week / Decimal(WEEK_DAYS)

    sellable_days = max(window_days - row.stockout_days, 1)
    if sellable_days < window_days:
        per_day *= Decimal(window_days) / Decimal(sellable_days)
    return per_day.quantize(UNITS)


def seasonal_factor(row: DemandRow, *, available: bool) -> Decimal:
    """How much busier this time of year is than an average month.

    Only computed where a full year of history sits behind it. A shop three
    months old has no season, and inventing one from a single data point is
    how a December order gets placed in March.
    """
    if not available or row.last_year_units is None or not row.trailing_year_span_units:
        return Decimal("1")
    factor = Decimal(row.last_year_units) / Decimal(row.trailing_year_span_units)
    return max(SEASONAL_MIN, min(SEASONAL_MAX, factor)).quantize(Decimal("0.01"))


def round_to_pack(quantity: Decimal, pack_size: Decimal, min_order_qty: Decimal) -> Decimal:
    """Turn a wanted quantity into one the vendor will actually accept."""
    if quantity <= 0:
        return Decimal("0")
    pack = pack_size if pack_size and pack_size > 0 else Decimal("1")
    packs = (quantity / pack).quantize(Decimal("1"), rounding=ROUND_CEILING)
    rounded = packs * pack
    if min_order_qty and rounded < min_order_qty:
        packs = (min_order_qty / pack).quantize(Decimal("1"), rounding=ROUND_CEILING)
        rounded = packs * pack
    return rounded


def skip_reason(row: DemandRow, skip_categories: tuple[str, ...]) -> str | None:
    """Why this item should not be reordered at all, if it should not."""
    if not row.is_active:
        return "discontinued"
    category = (row.category or "").strip().lower()
    if category and any(word in category for word in skip_categories):
        return "one-off inventory"
    if row.units_in_window <= 0:
        return "no sales in the window"
    return None


def build(
    inputs: DemandInputs,
    *,
    skip_categories: tuple[str, ...] = DEFAULT_SKIP_CATEGORIES,
    on_order: dict[uuid.UUID, Decimal] | None = None,
) -> Forecast:
    """Turn measurements into suggestions.

    `on_order` is what is already on a purchase order and has not arrived. It
    is subtracted before anything is suggested, so reviewing the same reorder
    twice in a week does not order everything twice.
    """
    forecast = Forecast()
    already_ordered = on_order or {}
    window_days = inputs.window.days

    if inputs.stockouts_unknown:
        forecast.caveats.append(
            "This shop's system keeps no stock history, so days when something was "
            "sold out could not be left out of the rate of sale. Anything that ran "
            "out will look slower than it is."
        )
    if inputs.seasonality_unavailable:
        forecast.caveats.append(
            "There is less than a year of sales here, so no seasonal adjustment was "
            "applied — these are this month's rates carried forward."
        )

    for row in inputs.rows:
        reason = skip_reason(row, skip_categories)
        if reason:
            forecast.skipped[reason] = forecast.skipped.get(reason, 0) + 1
            continue

        velocity = velocity_of(row, window_days)
        if velocity <= 0:
            forecast.skipped["not moving"] = forecast.skipped.get("not moving", 0) + 1
            continue

        factor = seasonal_factor(row, available=not inputs.seasonality_unavailable)
        lead_time = row.lead_time_days if row.lead_time_days is not None else None
        assumed = lead_time is None
        lead_time = lead_time if lead_time is not None else DEFAULT_LEAD_TIME_DAYS
        horizon = lead_time + REVIEW_PERIOD_DAYS

        demand = (velocity * factor * Decimal(horizon)).quantize(UNITS)
        weekly = velocity * Decimal(WEEK_DAYS)
        safety_weeks = Decimal("1") if weekly >= STEADY_WEEKLY_UNITS else SLOW_SAFETY_WEEKS
        safety = (velocity * factor * Decimal(WEEK_DAYS) * safety_weeks).quantize(UNITS)

        pipeline = row.on_hand + already_ordered.get(row.variant_id, Decimal("0"))
        raw = demand + safety - pipeline
        quantity = round_to_pack(max(raw, Decimal("0")), row.pack_size, row.min_order_qty)
        if quantity <= 0:
            continue

        cover = (row.on_hand / velocity).quantize(Decimal("0.1")) if row.on_hand > 0 else None

        caveats: list[str] = []
        if row.best_cost is None:
            caveats.append("no cost recorded, so this line has no value at cost")
        if assumed:
            caveats.append(f"no lead time on file — assumed {DEFAULT_LEAD_TIME_DAYS} days")
        if row.stockout_days_approximate:
            caveats.append("a stock count in the window makes the sold-out days approximate")
        if row.vendor_id is None:
            caveats.append("no vendor on file, so this cannot be grouped onto an order yet")

        forecast.suggestions.append(
            Suggestion(
                variant_id=row.variant_id,
                location_id=row.location_id,
                location_name=row.location_name,
                label=row.label,
                sku=row.sku,
                category=row.category,
                on_hand=row.on_hand,
                velocity=velocity,
                seasonal_factor=factor,
                horizon_days=horizon,
                demand=demand,
                safety_stock=safety,
                suggested_qty=quantity,
                raw_qty=raw.quantize(UNITS),
                pack_size=row.pack_size,
                min_order_qty=row.min_order_qty,
                lead_time_days=lead_time,
                lead_time_assumed=assumed,
                unit_cost=row.best_cost,
                unit_price=row.price,
                vendor_id=row.vendor_id,
                vendor_name=row.vendor_name,
                explanation=explain(row, velocity, factor, lead_time, quantity, raw, assumed),
                caveats=tuple(caveats),
                days_of_cover=cover,
            )
        )

    forecast.suggestions.sort(
        key=lambda s: (
            not s.stocks_out_before_delivery,
            s.days_of_cover if s.days_of_cover is not None else Decimal("-1"),
        )
    )
    return forecast


def explain(
    row: DemandRow,
    velocity: Decimal,
    factor: Decimal,
    lead_time: int,
    quantity: Decimal,
    raw: Decimal,
    lead_time_assumed: bool,
) -> str:
    """The sentence that goes on the suggestion.

    Written in the order the calculation happened, because the first question
    an owner asks a suggestion they disagree with is "where did that come
    from", and the answer has to be readable without a key.
    """
    weekly = (velocity * Decimal(WEEK_DAYS)).quantize(Decimal("0.1"))
    parts = [
        f"sells {weekly}/week",
        f"{_plain(row.on_hand)} on hand",
        f"{lead_time}-day lead time{' (assumed)' if lead_time_assumed else ''}",
    ]
    if row.stockout_days:
        parts.append(f"sold out {row.stockout_days} of the last {HORIZON_DAYS} days")
    if factor != 1:
        parts.append(f"seasonal factor {factor}")
    if quantity != raw.quantize(Decimal("1"), rounding=ROUND_CEILING) and row.pack_size > 1:
        parts.append(f"rounded up to packs of {_plain(row.pack_size)}")
    return ", ".join(parts)


def _plain(value: Decimal) -> str:
    """A quantity a human would write: 4, not 4.0000."""
    return format(value.normalize(), "f")
