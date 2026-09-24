"""The number check: the one thing standing between a model and an inbox.

The digest and the dead-stock wording both let a model write sentences from
figures we computed. This is the validator that decides whether what it wrote
is allowed out, and it is the difference between a feature and a liability.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.llm import InventedNumber, check_numbers, facts_from, numbers_in


# ---------------------------------------------------------------------------
# Finding figures in prose
# ---------------------------------------------------------------------------


def test_thousands_separators_and_trailing_zeros_normalise_to_one_figure() -> None:
    """$1,200 in a sentence and 1200.00 in the payload are the same number, and
    a validator that disagreed would reject every correct email."""
    assert numbers_in("we took $1,200 last week") == {"1200"}
    assert numbers_in("1200.00") == {"1200"}
    assert numbers_in("1,200.50") == {"1200.5"}


def test_percentages_and_negatives_are_figures_too() -> None:
    assert "19" in numbers_in("up 19% on the week")
    assert "-40" in numbers_in("down -40 dollars")


# ---------------------------------------------------------------------------
# Pulling facts out of a payload
# ---------------------------------------------------------------------------


def test_facts_are_gathered_from_anywhere_in_the_payload() -> None:
    """Nested lists and dicts included: an action's dollar figure is three
    levels down and is exactly the sort of number a sentence will quote."""
    payload = {
        "net_sales": "1483.17",
        "orders": 29,
        "actions": [{"title": "11 items", "dollar_impact": "123.11"}],
        "nested": {"deep": {"value": Decimal("7.50")}},
    }
    facts = facts_from(payload)
    assert {"1483.17", "29", "11", "123.11", "7.5"} <= facts


def test_booleans_and_nulls_are_not_figures() -> None:
    """`True` is 1 in Python, and a validator that learned "1" from a boolean
    would wave through a sentence claiming one of something."""
    assert facts_from({"ok": True, "missing": None}) == set()


# ---------------------------------------------------------------------------
# The check itself
# ---------------------------------------------------------------------------


def test_wording_built_from_the_facts_passes() -> None:
    facts = facts_from({"net_sales": "1483.17", "previous": "1245.02"})
    check_numbers("You took $1,483.17, up from $1,245.02.", facts)


def test_an_invented_figure_is_rejected() -> None:
    """The failure this whole module exists to prevent: a plausible,
    authoritative-looking number that came from nowhere."""
    facts = facts_from({"net_sales": "1483.17"})
    with pytest.raises(InventedNumber) as raised:
        check_numbers("You took $1,483.17, up $183.75 on last week.", facts)
    assert "183.75" in raised.value.offenders


def test_small_counting_words_are_allowed() -> None:
    """"Top 3 actions" and "the last 7 days" are how a sentence is written, not
    claims about the shop's money. A validator that rejected them would reject
    every readable email and the feature would never ship."""
    check_numbers("The top 3 actions from the last 7 days.", facts_from({}))


def test_a_number_above_the_counting_range_still_has_to_be_real() -> None:
    with pytest.raises(InventedNumber):
        check_numbers("There are 48 items to deal with.", facts_from({}))


def test_explicitly_allowed_figures_pass() -> None:
    """Dates reach prose as their parts — the day, the month, the year — and
    those come from the period, not from the payload's money."""
    check_numbers("Week of September 21, 2026.", facts_from({}), allow={"21", "2026"})


def test_the_offending_figure_is_named_so_it_can_be_logged() -> None:
    """When this fires the digest falls back silently, so the only way anyone
    finds out which number was invented is the exception."""
    with pytest.raises(InventedNumber) as raised:
        check_numbers("$999.99 of margin", facts_from({"margin": "100.00"}))
    assert raised.value.offenders == {"999.99"}
    assert "999.99" in str(raised.value)
