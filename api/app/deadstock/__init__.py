"""Dead stock rescue: a plan per item, and a check on whether it worked.

Three parts, in the order they run:

* `app.analytics.stale` grades the shelf — slowing, stale, dead — and prices
  what is stuck at cost where a cost exists.
* `rescue` chooses the play by rule (return, bundle, move, mark down) and lets
  the cheap model phrase it. The model never picks the play or the price.
* `outcomes` measures the thirty days either side of whatever the owner did,
  and counts cash recovered at cost rather than at the sticker.

    rescue_plan = await plan(session, ctx)
    await log_action(session, ctx, variant_id=..., kind="markdown", price_after=...)
"""

from app.analytics.stale import StaleItem, StaleReport, stale_inventory
from app.deadstock.outcomes import measure
from app.deadstock.rescue import MARKDOWN_LADDER, Rescue, Rung, choose, markdown_ladder
from app.deadstock.service import (
    RescuePlan,
    log_action,
    plan,
    recent_actions,
    vendor_terms,
)
from app.deadstock.tables import RescueAction

__all__ = [
    "MARKDOWN_LADDER",
    "Rescue",
    "RescueAction",
    "RescuePlan",
    "Rung",
    "StaleItem",
    "StaleReport",
    "choose",
    "log_action",
    "markdown_ladder",
    "measure",
    "plan",
    "recent_actions",
    "stale_inventory",
    "vendor_terms",
]
