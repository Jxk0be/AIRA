"""Every table we own, in one import.

Alembic's autogenerate only compares what is registered on `Base.metadata`, and
a table whose module nobody imported is a table Alembic offers to drop. The
canonical schema used to be the whole story; now that features carry tables of
their own, this module is the single place that has to stay complete.

Import it for the metadata, not for the names: each module is still the place
its own tables are defined and documented.
"""

from __future__ import annotations

import app.canonical.tables
import app.deadstock.tables
import app.insights.tables
import app.jobs.tables
import app.monthend.tables
import app.notify.tables
import app.reorder.tables  # noqa: F401
from app.db import Base

metadata = Base.metadata

__all__ = ["metadata"]
