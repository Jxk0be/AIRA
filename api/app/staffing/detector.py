"""At most one staffing observation a month, and only the clearest one.

Monthly because a rota does not change weekly, and one because a list of six
things to think about is a list nobody acts on. There is no dollar figure
unless the owner has entered an hourly labour cost in settings — without one,
the honest output is "these hours are quiet", not an invented saving.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext
from app.canonical.enums import InsightSeverity
from app.insights import register
from app.insights.models import InsightDraft
from app.staffing.service import heatmap, observe, shifts

SHELF_LIFE_DAYS = 35


class StaffingDetector:
    kind = "staffing"
    schedule = "monthly"
    requires: tuple[str, ...] = ()

    async def run(
        self, session: AsyncSession, ctx: AnalyticsContext, as_of: date
    ) -> list[InsightDraft]:
        rota = await shifts(session, ctx)
        if not rota:
            # No rota on file means nothing to compare the tills against. The
            # heatmap is still worth looking at, and the Staffing page says so;
            # an insight about it would be a nag about data entry.
            return []

        grids = await heatmap(session, ctx, as_of=as_of)
        if not grids:
            return []

        hourly = ctx.setting("hourly_labour_cost", None)
        cost = Decimal(str(hourly)) if hourly else None

        observations = observe(grids[0], rota, hourly_labour_cost=cost)
        if not observations:
            return []

        clearest = observations[0]
        severity = InsightSeverity.INFO
        return [
            InsightDraft(
                kind=self.kind,
                severity=severity,
                title=f"{clearest.weekday_name} looks {_word(clearest.kind)}",
                summary=(
                    f"{clearest.sentence} Worth a look next time the rota comes up — "
                    "this is eight weeks of your own tills, not a recommendation."
                ),
                dedupe_key=f"staffing:{as_of:%Y-%m}",
                dollar_impact=clearest.labour_cost,
                evidence={
                    "location": grids[0].location_name,
                    "weeks": grids[0].weeks,
                    "event_days_excluded": grids[0].event_days_excluded,
                    "hourly_labour_cost": str(cost) if cost is not None else None,
                    "observations": [
                        {
                            "kind": o.kind,
                            "weekday": o.weekday_name,
                            "hours": list(o.hours),
                            "orders_per_hour": str(o.orders_per_hour),
                            "staff_count": o.staff_count,
                            "sentence": o.sentence,
                            "labour_cost": (
                                str(o.labour_cost) if o.labour_cost is not None else None
                            ),
                        }
                        for o in observations[:6]
                    ],
                },
                suggested_action={
                    "type": "review_staffing",
                    "label": "See the heatmap",
                    "route": "staffing",
                },
                expires_at=datetime.now(tz=UTC) + timedelta(days=SHELF_LIFE_DAYS),
            )
        ]


def _word(kind: str) -> str:
    return {
        "quiet": "quieter than it is staffed for",
        "busy": "busier than it is staffed for",
        "dead_open": "slow at opening",
        "dead_close": "slow at closing",
    }.get(kind, "worth a look")


register(StaffingDetector())
