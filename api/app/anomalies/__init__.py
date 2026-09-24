"""Rare, high-signal alerts.

Built on the insights framework like everything else, so an anomaly and a
reorder suggestion arrive in the same inbox and rank against each other by the
same rule. What is specific to this package is the statistics — a day is
compared with its own weekday's median rather than with an average day — and
the discipline about noise: a detector here would rather miss something than
cry wolf, because an owner only mutes a channel once.
"""

from app.anomalies.baselines import (
    Baseline,
    BaselineSet,
    DayObservation,
    build,
    event_days,
    mad,
    median,
)
from app.anomalies.detectors import (
    SENSITIVITY_Z,
    DetectorPrecision,
    ImpossibleValueDetector,
    RefundSpikeDetector,
    SalesAnomalyDetector,
    ShrinkDetector,
    StaleDataDetector,
    SyncHealth,
    precision_report,
    sensitivity_of,
    sync_health,
)

__all__ = [
    "SENSITIVITY_Z",
    "Baseline",
    "BaselineSet",
    "DayObservation",
    "DetectorPrecision",
    "ImpossibleValueDetector",
    "RefundSpikeDetector",
    "SalesAnomalyDetector",
    "ShrinkDetector",
    "StaleDataDetector",
    "SyncHealth",
    "build",
    "event_days",
    "mad",
    "median",
    "precision_report",
    "sensitivity_of",
    "sync_health",
]
