"""The chart validator.

A chart is the most trusted thing on the screen and the easiest thing to
fabricate, so the rule is absolute: every value in one has to be a value some
tool returned during the same turn. These tests are the teeth of that rule.

No database and no model — the validator is pure, which is exactly why it can be
trusted with the job.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.agent.charts import ChartRejected, ChartSpec, ChartType, Observed, validate

SUMMARY = {
    "period_start": "2025-12-01",
    "period_end": "2025-12-31",
    "gross_sales": "18745.81",
    "discounts": "172.82",
    "net_sales": "18534.57",
    "order_count": 348,
    "caveats": [{"code": "custom_amount_sales", "message": "2.6% of these sales"}],
}


def observed_from(*results: object) -> Observed:
    seen = Observed()
    for result in results:
        seen.record(result)
    return seen


def chart(**overrides: object) -> ChartSpec:
    spec: dict[str, object] = {
        "type": ChartType.BAR,
        "title": "December",
        "x": "period_end",
        "y": ["net_sales"],
        "data": [{"period_end": "2025-12-31", "net_sales": "18534.57"}],
    }
    spec.update(overrides)
    return ChartSpec(**spec)  # type: ignore[arg-type]


def test_a_chart_built_from_a_tool_result_is_accepted() -> None:
    validate(chart(), observed_from(SUMMARY))


def test_an_invented_number_is_rejected() -> None:
    """The failure this whole mechanism exists for.

    18,600 is a plausible December for this shop, it is not what the tool said,
    and nobody reading the chart would ever know.
    """
    with pytest.raises(ChartRejected, match="did not come from any tool"):
        validate(
            chart(data=[{"period_end": "2025-12-31", "net_sales": "18600.00"}]),
            observed_from(SUMMARY),
        )


def test_a_rounded_number_is_still_an_invented_one() -> None:
    """Rounding is the polite version of making it up, and it still moves the
    number the owner will quote to their accountant."""
    with pytest.raises(ChartRejected):
        validate(
            chart(data=[{"period_end": "2025-12-31", "net_sales": "18535"}]),
            observed_from(SUMMARY),
        )


def test_the_same_number_in_any_shape_is_the_same_number() -> None:
    """Analytics returns Decimals, JSON makes them strings, the model writes
    floats. All three have to compare equal or the validator would reject
    honest charts and teach everyone to switch it off.
    """
    seen = observed_from({"period_end": "2025-12-31", "net_sales": Decimal("18534.5700")})
    for written in ("18534.57", 18534.57, Decimal("18534.570"), "$18,534.57"):
        validate(chart(data=[{"period_end": "2025-12-31", "net_sales": written}]), seen)


def test_zero_and_one_do_not_have_to_be_earned() -> None:
    """A week with no sales is a real row, and requiring 0 to have appeared in a
    tool result would only make the model fight the validator."""
    seen = observed_from({"bucket": "2026-01-05", "net_sales": "412.10"})
    validate(
        chart(
            x="bucket",
            data=[
                {"bucket": "2026-01-05", "net_sales": "412.10"},
                {"bucket": "2026-01-05", "net_sales": 0},
            ],
        ),
        seen,
    )


def test_an_invented_label_is_rejected_too() -> None:
    """A bar labelled with a product the shop does not sell is the same lie in
    a different column."""
    with pytest.raises(ChartRejected, match="did not come from any tool"):
        validate(
            chart(
                x="product",
                y=["net_sales"],
                data=[{"product": "Definitely Real Vol. 1", "net_sales": "18534.57"}],
            ),
            observed_from(SUMMARY),
        )


def test_series_names_must_be_field_names_a_tool_used() -> None:
    with pytest.raises(ChartRejected, match="appear in no tool result"):
        validate(chart(y=["revenue"]), observed_from(SUMMARY))


def test_a_row_missing_the_x_value_is_rejected() -> None:
    with pytest.raises(ChartRejected, match="x axis"):
        validate(chart(data=[{"net_sales": "18534.57"}]), observed_from(SUMMARY))


def test_charting_before_calling_any_tool_is_rejected() -> None:
    """The model has to look something up before it can draw it."""
    with pytest.raises(ChartRejected, match="nothing to chart"):
        validate(chart(), Observed())


def test_values_are_found_however_deeply_nested() -> None:
    """Tool results are nested — rows inside a breakdown, caveats inside a
    summary — and a value is legitimate wherever it appeared."""
    breakdown = {
        "dimension": "category",
        "rows": [
            {"label": "Manga", "net_sales": "11083.34"},
            {"label": "Singles", "net_sales": "16714.00"},
        ],
    }
    validate(
        chart(
            x="label",
            data=[
                {"label": "Manga", "net_sales": "11083.34"},
                {"label": "Singles", "net_sales": "16714.00"},
            ],
        ),
        observed_from(breakdown),
    )
