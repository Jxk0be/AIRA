"""What the dashboard screens read.

Thin HTTP over `app.analytics` and the canonical tables. Nothing here works a
number out for itself: if a figure appears on a screen, it came from the
semantic layer, so a number in a chart and the same number in the assistant's
sentence cannot drift apart.

Two modules, because they answer to different rules:

* `routes.py` reads the canonical schema only, like every other AI-facing
  layer, and never learns which platform a tenant runs.
* `operations.py` is the Data & sync screen, which is *about* the connection
  and therefore does know. It sits above `app.connectors` the way the sync CLI
  does, and nothing in the AI layer imports it (CLAUDE.md rule 1).
"""

from app.dashboard.operations import router as operations_router
from app.dashboard.routes import router as dashboard_router

__all__ = ["dashboard_router", "operations_router"]
