"""Charts the model may draw, and the numbers it may put in them.

A chart is the most trusted thing on the screen. Nobody re-reads the sentence
above it, and nobody checks the axis against a report. So the model is not
allowed to write numbers into one: every value in a chart has to be a value some
tool actually returned during this turn, and `validate` is what enforces that.

This is not a politeness check. An assistant that can invent a plausible bar is
an assistant that will, eventually, on a Friday, in front of a customer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Values this small are structural (zero, a count of one) and appear in any
# dataset, so requiring them to have come from a tool result adds nothing and
# would only make the model fight the validator.
TRIVIAL = {"0", "1", "-1"}


class ChartType(StrEnum):
    LINE = "line"
    BAR = "bar"
    PIE = "pie"
    TABLE = "table"


class ChartSpec(BaseModel):
    """Our chart format, shared by the assistant and the dashboard.

    One shape for both so a chart pinned from a conversation renders with the
    same component as a dashboard widget.
    """

    model_config = ConfigDict(extra="forbid")

    type: ChartType
    title: str = Field(max_length=200)
    # The key in each row that labels the point: a date, a product, a category.
    x: str
    # One key per series. A bar chart with two series is two bars per label.
    y: list[str] = Field(min_length=1, max_length=6)
    data: list[dict[str, Any]] = Field(min_length=1, max_length=400)
    # Optional prose the UI shows under the chart, e.g. a coverage caveat.
    note: str | None = Field(default=None, max_length=400)


class ChartRejected(ValueError):
    """The chart contains something no tool returned. Message is for the model."""


def _normalise(value: Any) -> str | None:
    """A number's canonical form, or None if it is not a number.

    `Decimal("18534.5700")`, `18534.57` and `"18534.57"` all have to compare
    equal: analytics returns Decimals, JSON turns them into strings, and the
    model writes them back as floats.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float | Decimal):
        number = Decimal(str(value))
    elif isinstance(value, str):
        try:
            number = Decimal(value.replace(",", "").replace("$", "").strip())
        except (InvalidOperation, ValueError):
            return None
    else:
        return None
    normalised = number.normalize()
    # normalize() gives 1E+3 for 1000; 'f' formatting keeps it human.
    return format(normalised, "f")


@dataclass
class Observed:
    """Everything the tools said this turn, flattened for lookup."""

    numbers: set[str] = field(default_factory=set)
    labels: set[str] = field(default_factory=set)

    def record(self, value: Any) -> None:
        """Walk a tool result and remember every scalar in it."""
        if isinstance(value, Mapping):
            for key, item in value.items():
                self.labels.add(str(key))
                self.record(item)
        elif isinstance(value, str):
            self.labels.add(value)
            number = _normalise(value)
            if number is not None:
                self.numbers.add(number)
        elif isinstance(value, Iterable) and not isinstance(value, bytes):
            for item in value:
                self.record(item)
        else:
            number = _normalise(value)
            if number is not None:
                self.numbers.add(number)
            elif value is not None:
                self.labels.add(str(value))

    def has_number(self, value: Any) -> bool:
        number = _normalise(value)
        return number is not None and (number in TRIVIAL or number in self.numbers)

    def has_label(self, value: str) -> bool:
        return value in self.labels


def validate(spec: ChartSpec, observed: Observed) -> None:
    """Refuse a chart built from anything a tool did not return.

    Raises `ChartRejected` with a message written for the model, because the
    model is the one that has to fix it: it goes straight back as the tool's
    error result.
    """
    if not observed.numbers and not observed.labels:
        raise ChartRejected(
            "No tool has returned any data in this turn, so there is nothing to chart. "
            "Call a data tool first, then chart its output."
        )

    missing_series = [key for key in spec.y if not observed.has_label(key)]
    if missing_series:
        raise ChartRejected(
            f"These series names appear in no tool result this turn: {missing_series}. "
            "Use the field names exactly as the tool returned them."
        )

    for index, row in enumerate(spec.data):
        if spec.x not in row:
            raise ChartRejected(f"Row {index} has no '{spec.x}' value, which is the x axis.")
        for key, value in row.items():
            if value is None:
                continue
            if _normalise(value) is not None:
                if not observed.has_number(value):
                    raise ChartRejected(
                        f"The value {value!r} in row {index} ('{key}') did not come from any tool "
                        "result this turn. Charts may only contain numbers a tool returned — "
                        "do not round, rescale or estimate them."
                    )
            elif not observed.has_label(str(value)):
                raise ChartRejected(
                    f"The label {value!r} in row {index} ('{key}') did not come from any tool "
                    "result this turn."
                )
