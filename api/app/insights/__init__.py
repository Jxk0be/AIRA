"""Insights: the one shape every proactive feature produces.

A detector finds something worth doing and returns `InsightDraft`s. The service
stores them, deduplicating so the same problem is not announced twice, and the
inbox, the weekly digest, the alerts and the value ledger are all views over
what it stored.

This package reads the canonical schema through `app.analytics` and nothing
else. It does not know which POS a tenant runs (CLAUDE.md rule 1), and no
detector writes SQL of its own (rule 4).

    ctx = await load_context(session, "tsundoku")
    runs = await run_detectors(session, ctx)
    actions = await top_open(session, ctx, limit=3)
"""

from app.insights.models import (
    Detector,
    DetectorRun,
    InsightDraft,
    StoredInsight,
    ValueLedger,
)
from app.insights.registry import all_detectors, detectors_for, register
from app.insights.service import (
    METRIC_CASH_RECOVERED,
    OPEN_STATUSES,
    acted_insights,
    add_outcome,
    counts_by_status,
    expire_stale,
    list_insights,
    mark_notified,
    outcome_exists,
    record,
    record_feedback,
    run_detectors,
    set_status,
    top_open,
    value_ledger,
)
from app.insights.tables import Insight, InsightOutcome

__all__ = [
    "METRIC_CASH_RECOVERED",
    "OPEN_STATUSES",
    "Detector",
    "DetectorRun",
    "Insight",
    "InsightDraft",
    "InsightOutcome",
    "StoredInsight",
    "ValueLedger",
    "acted_insights",
    "add_outcome",
    "all_detectors",
    "counts_by_status",
    "detectors_for",
    "expire_stale",
    "list_insights",
    "mark_notified",
    "outcome_exists",
    "record",
    "record_feedback",
    "register",
    "run_detectors",
    "set_status",
    "top_open",
    "value_ledger",
]
