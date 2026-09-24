"""Which detectors exist, and when each one is worth running.

The same idea as the adapter registry: one place turns a name into code, and
nothing downstream imports a detector module directly. A feature adds itself
here and the worker, the digest and the manual "run detectors now" button all
pick it up without being edited.
"""

from __future__ import annotations

from app.insights.models import Detector

# Schedules, in the vocabulary the worker understands. Kept as names rather
# than cron strings so a detector says what it means ("after every sync") and
# the worker decides what that costs.
SCHEDULES = frozenset({"after_sync", "daily", "weekly", "monthly"})

_DETECTORS: dict[str, Detector] = {}


def register(detector: Detector) -> Detector:
    """Add a detector. Usable as a decorator on the class's instance factory."""
    if detector.schedule not in SCHEDULES:
        raise ValueError(
            f"detector {detector.kind!r} has schedule {detector.schedule!r}; "
            f"known: {', '.join(sorted(SCHEDULES))}"
        )
    if detector.kind in _DETECTORS:
        raise ValueError(f"detector {detector.kind!r} is already registered")
    _DETECTORS[detector.kind] = detector
    return detector


def all_detectors() -> tuple[Detector, ...]:
    return tuple(_DETECTORS[kind] for kind in sorted(_DETECTORS))


def detectors_for(schedule: str) -> tuple[Detector, ...]:
    return tuple(d for d in all_detectors() if d.schedule == schedule)


def get(kind: str) -> Detector:
    try:
        return _DETECTORS[kind]
    except KeyError:
        known = ", ".join(sorted(_DETECTORS)) or "none registered"
        raise KeyError(f"unknown detector {kind!r}. Registered: {known}") from None


def known() -> list[str]:
    return sorted(_DETECTORS)


def load_builtin_detectors() -> None:
    """Import the feature packages so their detectors register themselves.

    Called by the worker and by the API's insights routes. Importing
    `app.insights` alone deliberately does not drag in every feature.
    """
    from app.anomalies import detectors as anomaly_detectors  # noqa: F401
    from app.deadstock import detector as deadstock_detector  # noqa: F401
    from app.reorder import detector as reorder_detector  # noqa: F401
    from app.staffing import detector as staffing_detector  # noqa: F401
