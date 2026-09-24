"""Staffing hints: a heatmap and a couple of plain observations.

Eight weeks of the shop's own tills, by weekday and hour in its own timezone,
with event days pulled out so a convention weekend does not decide what a
normal Saturday looks like. Where the owner has entered a rota, the two are
compared; where they have entered an hourly wage as well, a quiet stretch gets
a cost attached.

Everything is phrased as an observation. This feature does not tell anybody to
cut a shift.
"""

from app.staffing.service import (
    WEEKDAY_NAMES,
    Cell,
    Heatmap,
    Observation,
    Shift,
    busiest_hours,
    delete_shift,
    heatmap,
    observe,
    set_shift,
    shifts,
)

__all__ = [
    "WEEKDAY_NAMES",
    "Cell",
    "Heatmap",
    "Observation",
    "Shift",
    "busiest_hours",
    "delete_shift",
    "heatmap",
    "observe",
    "set_shift",
    "shifts",
]
