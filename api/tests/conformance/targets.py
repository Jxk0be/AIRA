"""What the conformance suite runs against.

Every adapter gets an entry here. Adding one is the whole cost of putting a new
platform under the same checks: the suite is written once, against the canonical
model, and knows nothing about any particular source.

The suite runs against a tenant that has **already been synced** — the same
thing phase 11's `onboard conformance` does against a real customer's data.
Nothing here re-seeds a fixture; if the tenant has no data, the checks skip and
say so.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConformanceTarget:
    id: str
    tenant_slug: str
    # A one-line reminder of how to get this target into a runnable state.
    setup_hint: str


TARGETS: tuple[ConformanceTarget, ...] = (
    ConformanceTarget(
        id="registerone",
        tenant_slug="animanga_knox",
        setup_hint="python tasks.py sources && python tasks.py seed && python tasks.py backfill",
    ),
    # A source with nothing in common with the one above: a file, no ids, no
    # customers, no history, no incremental. Same checks, no edits to them.
    ConformanceTarget(
        id="mapping-spreadsheet",
        tenant_slug="panel_and_pawn",
        setup_hint="python tasks.py export && python tasks.py backfill panel_and_pawn",
    ),
)
