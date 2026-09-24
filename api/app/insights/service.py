"""Storing insights, running detectors, and adding up what it was worth.

Everything that writes to `insights` goes through here, for one reason: the
dedupe rule has to hold in exactly one place. A detector re-run must update the
finding it made last time rather than shout again, and it must not undo what
the owner already did about it.

The ordering rule lives here too. "Most important" means severity first and
dollar figure second, and it is used by the inbox, by the digest's top three
and by the agent's tool, so those three can never disagree about which problem
matters most.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext
from app.canonical.enums import InsightSeverity, InsightStatus
from app.db import table_of
from app.insights import registry
from app.insights.models import DetectorRun, InsightDraft, StoredInsight, ValueLedger
from app.insights.tables import Insight, InsightOutcome

log = logging.getLogger(__name__)

# The outcome metric that means "stock turned back into money, at cost".
# Named once because the value ledger and the dead-stock job both use it.
METRIC_CASH_RECOVERED = "cash_recovered"

# Statuses that still want the owner's attention.
OPEN_STATUSES = (InsightStatus.NEW, InsightStatus.SEEN, InsightStatus.SNOOZED)

# Bigger number = louder. Used for ordering only; the enum stays the truth.
_SEVERITY_RANK = case(
    (Insight.severity == InsightSeverity.URGENT, 3),
    (Insight.severity == InsightSeverity.WARN, 2),
    else_=1,
)


def _ranked(query: Select[Any]) -> Select[Any]:
    """Most important first: severity, then money, then newest."""
    return query.order_by(
        _SEVERITY_RANK.desc(),
        func.coalesce(Insight.dollar_impact, Decimal("0")).desc(),
        Insight.created_at.desc(),
    )


def _row_to_insight(row: Insight) -> StoredInsight:
    return StoredInsight(
        id=row.id,
        kind=row.kind,
        severity=row.severity,
        status=str(row.status),
        title=row.title,
        summary=row.summary,
        dollar_impact=row.dollar_impact,
        evidence=dict(row.evidence or {}),
        suggested_action=dict(row.suggested_action or {}),
        as_of=row.as_of,
        created_at=row.created_at,
        expires_at=row.expires_at,
        snoozed_until=row.snoozed_until,
        notified_at=row.notified_at,
        was_useful=row.was_useful,
    )


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


async def record(
    session: AsyncSession,
    ctx: AnalyticsContext,
    drafts: list[InsightDraft],
    as_of: date,
) -> tuple[int, int]:
    """Store a detector's drafts. Returns (created, updated).

    The upsert deliberately leaves `status`, `acted_at`, `dismissed_at`,
    `notified_at` and the feedback columns alone. Re-running a detector
    refreshes the numbers on a finding the owner has already dealt with; it
    does not put it back in their inbox. Detectors that want a finding to come
    back next week put the week in their dedupe key.
    """
    if not drafts:
        return 0, 0

    table = table_of(Insight)
    now = datetime.now(tz=UTC)

    # Which of these we already hold, so the caller can report created vs
    # updated honestly rather than guessing from the row count.
    keys = [d.dedupe_key for d in drafts]
    existing = set(
        (
            await session.execute(
                select(Insight.dedupe_key).where(
                    Insight.tenant_id == ctx.tenant_id, Insight.dedupe_key.in_(keys)
                )
            )
        )
        .scalars()
        .all()
    )

    rows = [
        {
            "id": uuid.uuid4(),
            "tenant_id": ctx.tenant_id,
            "kind": d.kind,
            "severity": d.severity,
            "title": d.title,
            "summary": d.summary,
            "dollar_impact": d.dollar_impact,
            "evidence": d.evidence,
            "suggested_action": d.suggested_action,
            "status": InsightStatus.NEW,
            "dedupe_key": d.dedupe_key,
            "as_of": as_of,
            "expires_at": d.expires_at,
        }
        for d in drafts
    ]

    statement = insert(table).values(rows)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["tenant_id", "dedupe_key"],
            set_={
                "severity": statement.excluded.severity,
                "title": statement.excluded.title,
                "summary": statement.excluded.summary,
                "dollar_impact": statement.excluded.dollar_impact,
                "evidence": statement.excluded.evidence,
                "suggested_action": statement.excluded.suggested_action,
                "as_of": statement.excluded.as_of,
                "expires_at": statement.excluded.expires_at,
                "updated_at": now,
            },
        )
    )
    created = sum(1 for d in drafts if d.dedupe_key not in existing)
    return created, len(drafts) - created


async def run_detectors(
    session: AsyncSession,
    ctx: AnalyticsContext,
    as_of: date | None = None,
    *,
    schedule: str | None = None,
    kinds: tuple[str, ...] | None = None,
) -> list[DetectorRun]:
    """Run the detectors this shop can support, and store what they find.

    One failing detector never costs the others their findings: each is
    caught, recorded on its own `DetectorRun`, and the rest carry on. A
    detector the tenant's system cannot feed is skipped with the reason, which
    is what the Data & sync screen shows instead of an empty inbox.
    """
    registry.load_builtin_detectors()
    day = as_of or ctx.today()
    wanted = (
        registry.detectors_for(schedule)
        if schedule
        else tuple(registry.get(k) for k in kinds)
        if kinds
        else registry.all_detectors()
    )

    runs: list[DetectorRun] = []
    for detector in wanted:
        missing = [c for c in detector.requires if not ctx.has(c)]
        if missing:
            runs.append(
                DetectorRun(
                    kind=detector.kind,
                    drafts=0,
                    created=0,
                    updated=0,
                    skipped_reason=f"needs {', '.join(missing)}",
                )
            )
            continue
        try:
            drafts = await detector.run(session, ctx, day)
            created, updated = await record(session, ctx, drafts, day)
            await session.commit()
            runs.append(
                DetectorRun(
                    kind=detector.kind, drafts=len(drafts), created=created, updated=updated
                )
            )
        except Exception as exc:  # one detector must not lose the others
            await session.rollback()
            log.exception("detector %s failed for %s", detector.kind, ctx.slug)
            runs.append(
                DetectorRun(
                    kind=detector.kind,
                    drafts=0,
                    created=0,
                    updated=0,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return runs


async def expire_stale(session: AsyncSession, ctx: AnalyticsContext) -> int:
    """Retire findings that have passed their own expiry.

    Advice has a shelf life. A reorder suggestion built on three-week-old
    velocity is not advice any more, and leaving it in the inbox teaches the
    owner to stop reading the inbox.
    """
    now = datetime.now(tz=UTC)
    table = table_of(Insight)
    result = await session.execute(
        update(table)
        .where(
            table.c.tenant_id == ctx.tenant_id,
            table.c.expires_at.is_not(None),
            table.c.expires_at < now,
            table.c.status.in_([s.value for s in OPEN_STATUSES]),
        )
        .values(status=InsightStatus.EXPIRED, updated_at=now)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def set_status(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    insight_id: uuid.UUID,
    status: InsightStatus,
    *,
    snooze_days: int | None = None,
) -> StoredInsight | None:
    """Act on, dismiss, snooze or simply mark an insight as seen.

    `tenant_id` is a filter, not a convenience: it is what makes it impossible
    for one shop's id to reach another shop's row (CLAUDE.md rule 3).
    """
    now = datetime.now(tz=UTC)
    values: dict[str, Any] = {"status": status, "updated_at": now}
    if status is InsightStatus.ACTED:
        values["acted_at"] = now
    elif status is InsightStatus.DISMISSED:
        values["dismissed_at"] = now
    elif status is InsightStatus.SNOOZED:
        values["snoozed_until"] = now + timedelta(days=snooze_days or 7)

    table = table_of(Insight)
    await session.execute(
        update(table).where(table.c.id == insight_id, table.c.tenant_id == tenant_id).values(values)
    )
    await session.commit()
    row = (
        await session.execute(
            select(Insight).where(Insight.id == insight_id, Insight.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    return _row_to_insight(row) if row else None


async def record_feedback(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    insight_id: uuid.UUID,
    useful: bool,
    note: str | None = None,
) -> None:
    """Store "this was useful" / "this was not".

    The only signal we have for tuning a detector's thresholds, so it is kept
    per insight rather than counted and thrown away.
    """
    table = table_of(Insight)
    await session.execute(
        update(table)
        .where(table.c.id == insight_id, table.c.tenant_id == tenant_id)
        .values(was_useful=useful, feedback_note=note, updated_at=datetime.now(tz=UTC))
    )
    await session.commit()


async def mark_notified(
    session: AsyncSession, tenant_id: uuid.UUID, insight_ids: list[uuid.UUID]
) -> None:
    """Remember that these went out, so the next digest does not repeat them."""
    if not insight_ids:
        return
    table = table_of(Insight)
    now = datetime.now(tz=UTC)
    await session.execute(
        update(table)
        .where(table.c.tenant_id == tenant_id, table.c.id.in_(insight_ids))
        .values(notified_at=now, updated_at=now)
    )


async def add_outcome(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    insight_id: uuid.UUID,
    *,
    metric: str,
    before_value: Decimal | None = None,
    after_value: Decimal | None = None,
    attributed_revenue: Decimal | None = None,
    notes: str | None = None,
    measured_at: datetime | None = None,
) -> uuid.UUID:
    """Log what happened after an action. Insert-only: outcomes are a history."""
    outcome_id = uuid.uuid4()
    await session.execute(
        insert(table_of(InsightOutcome)).values(
            id=outcome_id,
            tenant_id=tenant_id,
            insight_id=insight_id,
            measured_at=measured_at or datetime.now(tz=UTC),
            metric=metric,
            before_value=before_value,
            after_value=after_value,
            attributed_revenue=attributed_revenue,
            notes=notes,
        )
    )
    return outcome_id


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


async def list_insights(
    session: AsyncSession,
    ctx: AnalyticsContext,
    *,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    include_snoozed: bool = False,
) -> list[StoredInsight]:
    query = select(Insight).where(Insight.tenant_id == ctx.tenant_id)
    if kind:
        query = query.where(Insight.kind == kind)
    if status == "open":
        query = query.where(Insight.status.in_([s.value for s in OPEN_STATUSES]))
    elif status:
        query = query.where(Insight.status == status)
    if not include_snoozed:
        query = query.where(
            or_(
                Insight.snoozed_until.is_(None),
                Insight.snoozed_until <= datetime.now(tz=UTC),
                Insight.status != InsightStatus.SNOOZED,
            )
        )
    rows = (await session.execute(_ranked(query).limit(limit).offset(offset))).scalars().all()
    return [_row_to_insight(row) for row in rows]


async def top_open(
    session: AsyncSession, ctx: AnalyticsContext, limit: int = 3, *, unnotified_only: bool = False
) -> list[StoredInsight]:
    """The findings most worth a shop owner's next five minutes.

    What the digest's "top actions" block and the dashboard's summary both
    read, so the email and the screen cannot rank them differently.
    """
    now = datetime.now(tz=UTC)
    query = select(Insight).where(
        Insight.tenant_id == ctx.tenant_id,
        Insight.status.in_([InsightStatus.NEW.value, InsightStatus.SEEN.value]),
        or_(Insight.expires_at.is_(None), Insight.expires_at > now),
        or_(Insight.snoozed_until.is_(None), Insight.snoozed_until <= now),
    )
    if unnotified_only:
        query = query.where(Insight.notified_at.is_(None))
    rows = (await session.execute(_ranked(query).limit(limit))).scalars().all()
    return [_row_to_insight(row) for row in rows]


async def counts_by_status(session: AsyncSession, ctx: AnalyticsContext) -> dict[str, int]:
    rows = await session.execute(
        select(Insight.status, func.count())
        .where(Insight.tenant_id == ctx.tenant_id)
        .group_by(Insight.status)
    )
    return {str(status): int(count) for status, count in rows}


async def value_ledger(
    session: AsyncSession, ctx: AnalyticsContext, start: date, end: date
) -> ValueLedger:
    """What this product was demonstrably worth to one shop over one window.

    Deliberately the conservative reading. Revenue only counts when an outcome
    row tied it to an action; the open findings' dollar figures are reported
    separately as what is *available*, and are never added to money already
    made. "We flagged $2,100 and you cleared $900 of it" is a sentence a shop
    owner can check, which is the only kind worth saying.
    """
    first, after = _window(ctx, start, end)

    created, acted = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(Insight.status == InsightStatus.ACTED),
            ).where(
                Insight.tenant_id == ctx.tenant_id,
                Insight.created_at >= first,
                Insight.created_at < after,
            )
        )
    ).one()

    revenue, recovered, outcomes = (
        await session.execute(
            select(
                func.coalesce(func.sum(InsightOutcome.attributed_revenue), Decimal("0")),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                InsightOutcome.metric == METRIC_CASH_RECOVERED,
                                InsightOutcome.after_value,
                            ),
                            else_=Decimal("0"),
                        )
                    ),
                    Decimal("0"),
                ),
                func.count(),
            ).where(
                InsightOutcome.tenant_id == ctx.tenant_id,
                InsightOutcome.measured_at >= first,
                InsightOutcome.measured_at < after,
            )
        )
    ).one()

    flagged = (
        await session.execute(
            select(func.coalesce(func.sum(Insight.dollar_impact), Decimal("0"))).where(
                Insight.tenant_id == ctx.tenant_id,
                Insight.created_at >= first,
                Insight.created_at < after,
                Insight.status.in_([s.value for s in OPEN_STATUSES]),
            )
        )
    ).scalar_one()

    return ValueLedger(
        tenant=ctx.slug,
        start=start,
        end=end,
        insights_created=int(created),
        insights_acted=int(acted),
        attributed_revenue=Decimal(revenue),
        cash_recovered=Decimal(recovered),
        flagged_impact=Decimal(flagged or 0),
        outcomes=int(outcomes),
    )


async def acted_insights(
    session: AsyncSession, ctx: AnalyticsContext, kind: str, *, since_days: int = 90
) -> list[StoredInsight]:
    """Findings of one kind the owner acted on, newest first.

    The nightly outcome job's worklist: these are the actions whose effect is
    worth measuring.
    """
    cutoff = datetime.now(tz=UTC) - timedelta(days=since_days)
    rows = (
        (
            await session.execute(
                select(Insight)
                .where(
                    Insight.tenant_id == ctx.tenant_id,
                    Insight.kind == kind,
                    Insight.status == InsightStatus.ACTED,
                    Insight.acted_at.is_not(None),
                    Insight.acted_at >= cutoff,
                )
                .order_by(Insight.acted_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_row_to_insight(row) for row in rows]


async def outcome_exists(
    session: AsyncSession, insight_id: uuid.UUID, metric: str, on_day: date
) -> bool:
    """Has this action already been measured for this metric today?

    What makes the nightly job idempotent: running it twice on one evening
    must not double the shop's reported value.
    """
    found = (
        await session.execute(
            select(InsightOutcome.id)
            .where(
                InsightOutcome.insight_id == insight_id,
                InsightOutcome.metric == metric,
                func.date(InsightOutcome.measured_at) == on_day,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return found is not None


def _window(ctx: AnalyticsContext, start: date, end: date) -> tuple[datetime, datetime]:
    """Shop-local calendar days as half-open UTC instants."""
    from app.analytics import DateRange

    return DateRange(start, end).bounds(ctx.tz)


__all__ = [
    "METRIC_CASH_RECOVERED",
    "OPEN_STATUSES",
    "acted_insights",
    "add_outcome",
    "counts_by_status",
    "expire_stale",
    "list_insights",
    "mark_notified",
    "outcome_exists",
    "record",
    "record_feedback",
    "run_detectors",
    "set_status",
    "top_open",
    "value_ledger",
]
