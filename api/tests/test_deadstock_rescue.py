"""Which rescue play, and at what price.

The choice is rule-based on purpose — a model is allowed to reword the reason
and nothing else — so the rules are testable here as rules, with no database
and no model.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.analytics.stale import CoPurchase, LocationStrength, StaleItem
from app.canonical.enums import StaleKind
from app.deadstock.rescue import (
    MARKDOWN_LADDER,
    MIN_BUNDLE_BASKETS,
    choose,
    markdown_ladder,
)


def item(**overrides: object) -> StaleItem:
    base: dict[str, object] = {
        "variant_id": uuid.uuid4(),
        "product_id": uuid.uuid4(),
        "label": "Figure Display Case, Small",
        "sku": "ACC-FDC-S",
        "category": "Accessories",
        "category_id": uuid.uuid4(),
        "kind": StaleKind.STALE,
        "on_hand": Decimal("6"),
        "price": Decimal("34.99"),
        "cost": Decimal("18.00"),
        "last_sold_at": datetime(2026, 6, 1, tzinfo=UTC),
        "days_since_last_sale": 115,
        "recent_daily": Decimal("0"),
        "trailing_daily": Decimal("0.02"),
        "never_sold": False,
    }
    base.update(overrides)
    return StaleItem(**base)  # type: ignore[arg-type]


def partner(baskets: int, **overrides: object) -> CoPurchase:
    base: dict[str, object] = {
        "variant_id": uuid.uuid4(),
        "label": "Matte Card Sleeves, 100ct",
        "baskets": baskets,
        "units_sold_recently": Decimal("14"),
        "price": Decimal("7.99"),
    }
    base.update(overrides)
    return CoPurchase(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Cash tied up
# ---------------------------------------------------------------------------


def test_cash_is_counted_at_cost_where_a_cost_exists() -> None:
    """Six cases bought at $18 is $108 of the shop's money, not $210 of price
    tags. The difference is the whole point of the figure."""
    stuck = item()
    assert stuck.cash_at_cost == Decimal("108.00")
    assert stuck.cash_tied_up == Decimal("108.00")


def test_retail_is_the_labelled_fallback_when_there_is_no_cost() -> None:
    stuck = item(cost=None)
    assert stuck.cash_at_cost is None
    assert stuck.cash_tied_up == stuck.cash_at_retail == Decimal("209.94")


# ---------------------------------------------------------------------------
# The markdown ladder
# ---------------------------------------------------------------------------


def test_the_ladder_is_gentlest_first() -> None:
    rungs = markdown_ladder(item())
    assert [rung.discount for rung in rungs] == list(MARKDOWN_LADDER)
    assert rungs[0].price > rungs[-1].price


def test_each_rung_says_whether_it_still_clears_cost() -> None:
    """The number an owner actually wants. A 50% cut on something bought at 60%
    of retail is not a discount, it is a loss."""
    thin = item(price=Decimal("30.00"), cost=Decimal("18.00"))
    rungs = markdown_ladder(thin)
    assert rungs[0].clears_cost  # 15% off → $25.50, still over $18
    assert not rungs[-1].clears_cost  # 50% off → $15.00, under cost
    assert rungs[-1].margin_per_unit == Decimal("-3.00")


def test_no_price_means_no_ladder_rather_than_a_guess() -> None:
    assert markdown_ladder(item(price=None)) == []


def test_the_chosen_markdown_is_the_gentlest_that_still_clears_cost() -> None:
    """Going straight to half price gives away margin nobody had to give."""
    rescue = choose(item(price=Decimal("30.00"), cost=Decimal("18.00")))
    assert rescue.play == "markdown"
    assert rescue.detail["discount"] == "0.15"


def test_break_even_is_reported_even_when_no_rung_clears_it() -> None:
    hopeless = item(price=Decimal("20.00"), cost=Decimal("19.00"))
    rescue = choose(hopeless)
    assert rescue.detail["break_even_price"] == "19.00"
    assert "below cost" in rescue.reason


# ---------------------------------------------------------------------------
# Choosing the play
# ---------------------------------------------------------------------------


def test_a_returnable_vendor_beats_a_markdown_for_dead_stock() -> None:
    """Getting the cost back beats getting a fraction of it."""
    rescue = choose(
        item(kind=StaleKind.DEAD),
        vendor={"id": uuid.uuid4(), "name": "Bluefin Brands", "takes_returns": True},
    )
    assert rescue.play == "return_to_vendor"
    assert rescue.detail["value_at_cost"] == "108.00"


def test_a_vendor_that_does_not_take_returns_is_not_offered() -> None:
    rescue = choose(
        item(kind=StaleKind.DEAD),
        vendor={"id": uuid.uuid4(), "name": "Riverbend", "takes_returns": False},
    )
    assert rescue.play != "return_to_vendor"


def test_a_return_is_not_offered_for_merely_stale_stock() -> None:
    """Two months quiet is a markdown problem, not a send-it-back problem."""
    rescue = choose(
        item(kind=StaleKind.STALE),
        vendor={"id": uuid.uuid4(), "name": "Bluefin Brands", "takes_returns": True},
    )
    assert rescue.play != "return_to_vendor"


def test_a_genuine_co_purchase_partner_becomes_a_bundle() -> None:
    rescue = choose(item(), partners=[partner(MIN_BUNDLE_BASKETS)])
    assert rescue.play == "bundle"
    assert "Matte Card Sleeves" in rescue.headline
    assert rescue.detail["baskets"] == MIN_BUNDLE_BASKETS


def test_one_coincidental_basket_is_not_a_bundle() -> None:
    """Pairing on a single shared basket is how a shop ends up with a shelf of
    bundles nobody wants."""
    rescue = choose(item(), partners=[partner(MIN_BUNDLE_BASKETS - 1)])
    assert rescue.play == "markdown"


def test_a_partner_that_has_stopped_selling_is_not_a_bundle() -> None:
    """Pairing dead stock with more dead stock moves nothing."""
    rescue = choose(item(), partners=[partner(10, units_sold_recently=Decimal("0"))])
    assert rescue.play == "markdown"


def test_moving_is_only_offered_to_a_multi_location_shop() -> None:
    elsewhere = LocationStrength(
        location_id=uuid.uuid4(),
        location_name="Con Booth",
        units=Decimal("40"),
        share=Decimal("0.8"),
    )
    single = choose(item(), locations=[elsewhere], multi_location=False)
    multi = choose(item(), locations=[elsewhere], multi_location=True)
    assert single.play == "markdown"
    assert multi.play == "move"
    assert "Con Booth" in multi.headline


def test_moving_is_not_offered_to_where_it_already_is() -> None:
    here = uuid.uuid4()
    rescue = choose(
        item(),
        locations=[
            LocationStrength(
                location_id=here,
                location_name="Market Square",
                units=Decimal("40"),
                share=Decimal("0.9"),
            )
        ],
        current_location_id=here,
        multi_location=True,
    )
    assert rescue.play == "markdown"


def test_every_item_gets_a_play_and_a_reason() -> None:
    """Even the awkward one with no price: the answer is "set this by hand",
    not silence."""
    for stuck in (item(), item(price=None), item(cost=None), item(never_sold=True)):
        rescue = choose(stuck)
        assert rescue.play
        assert rescue.headline
        assert rescue.reason
