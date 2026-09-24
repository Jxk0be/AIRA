"""The detectors that watch the shop.

Precision over coverage, everywhere. A noisy alert is not a small cost: it is
permanent, because the owner mutes the channel and then misses the one that
mattered. So every detector here would rather say nothing than say something
thin, and three habits enforce that:

* **Check the plumbing first.** Missing data looks exactly like a bad day. The
  sales detector refuses to fire while the sync is stale, and the stale-data
  detector fires instead.
* **Group.** Three items walking off the shelf is one alert about shrink, not
  three alerts about items.
* **Ask.** Every alert carries Useful / Not useful, and `precision_report`
  turns the answers into the numbers these thresholds should be tuned on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import AnalyticsContext, DateRange
from app.analytics.queries import fetch_all, fetch_one
from app.anomalies.baselines import PERIODS, build
from app.canonical.enums import InsightSeverity, MovementKind
from app.insights import register
from app.insights.models import InsightDraft
from app.insights.tables import Insight

# Robust z-scores, by how much the owner wants to hear from us. The gap
# between "normal" and "low" is roughly the difference between a handful of
# alerts a month and one a quarter.
#
# Lower than they look, because the spread they are measured against is
# one-sided (see `Baseline`). Against a symmetric spread these would be far
# too twitchy; against a spread taken from the side the day actually fell on,
# they are the point where a day stops being a bad day and starts being a
# question.
SENSITIVITY_Z: dict[str, Decimal] = {
    "low": Decimal("3.2"),
    "normal": Decimal("2.5"),
    "high": Decimal("2.0"),
}
DEFAULT_SENSITIVITY = "normal"

# Nothing is worth an alert below this, whatever the z-score says. A quiet
# Tuesday at a small shop can be two standard deviations down and forty dollars.
MIN_DOLLAR_DIFFERENCE = Decimal("75")

# How many recent days the sales detector judges on each run, and how many
# recent weeks the refund detector does. More than one of each, so a worker
# that was down over a weekend still reports the Saturday it missed. Both
# dedupe keys carry the period, so covering a day twice updates one finding
# rather than adding a second.
SALES_SCAN_DAYS = 7
REFUND_SCAN_WEEKS = 6

# Unexplained stock movement has to be worth this much before it is raised —
# or, whatever it is worth, one item has to have lost this many units. Five
# copies of one thing walking off is a pattern at any price, and a shop whose
# fastest movers are three-dollar booster packs would never clear a
# money-only bar.
MIN_SHRINK_VALUE = Decimal("40")
MIN_SHRINK_UNITS_ONE_ITEM = Decimal("5")
SHRINK_WINDOW_DAYS = 14

# Refunds or discounts above this share of sales, when normal is well below it.
REFUND_SHARE_FLOOR = Decimal("0.05")
REFUND_MULTIPLE = Decimal("2.5")

# How much later than the shop's own normal gap counts as the sync being stuck.
STALE_MULTIPLE = Decimal("3")
STALE_FLOOR_HOURS = 30


def sensitivity_of(ctx: AnalyticsContext) -> Decimal:
    name = str(ctx.setting("anomaly_sensitivity", DEFAULT_SENSITIVITY)).lower()
    return SENSITIVITY_Z.get(name, SENSITIVITY_Z[DEFAULT_SENSITIVITY])


# --------------------------------------------------------------------------
# Sync health, which everything else depends on
# --------------------------------------------------------------------------


@dataclass(slots=True)
class SyncHealth:
    last_order_at: datetime | None
    hours_since: Decimal | None
    typical_gap_hours: Decimal | None
    stale: bool

    @property
    def trustworthy(self) -> bool:
        """Whether today's numbers are worth judging at all."""
        return self.last_order_at is not None and not self.stale


async def sync_health(session: AsyncSession, ctx: AnalyticsContext, as_of: date) -> SyncHealth:
    """How long since we last saw a sale, against how long is normal here.

    A shop closed on Mondays has a 48-hour gap every week and is perfectly
    healthy. The comparison is therefore against the shop's own recent gaps,
    never against a fixed number of hours.
    """
    row = await fetch_one(
        session,
        """
        with recent as (
            select o.placed_at as placed_at
            from orders o
            where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
            order by o.placed_at desc
            limit 400
        ),
        gaps as (
            select extract(epoch from (placed_at - lag(placed_at) over (order by placed_at)))
                   / 3600 as hours
            from recent
        )
        select (select max(placed_at) from recent) as last_at,
               (select percentile_cont(0.9) within group (order by hours)
                from gaps where hours is not null) as gap_hours
        """,
        {"tenant": ctx.tenant_id},
    )

    last_at = row.last_at
    if last_at is None:
        return SyncHealth(None, None, None, stale=True)

    now = datetime.now(tz=UTC)
    hours = Decimal((now - last_at).total_seconds()) / Decimal(3600)
    typical = Decimal(str(row.gap_hours)) if row.gap_hours is not None else None
    threshold = max(
        (typical * STALE_MULTIPLE) if typical else Decimal(STALE_FLOOR_HOURS),
        Decimal(STALE_FLOOR_HOURS),
    )
    return SyncHealth(
        last_order_at=last_at,
        hours_since=hours.quantize(Decimal("0.1")),
        typical_gap_hours=typical.quantize(Decimal("0.1")) if typical else None,
        stale=hours > threshold,
    )


# --------------------------------------------------------------------------
# Sales drops and spikes
# --------------------------------------------------------------------------


class SalesAnomalyDetector:
    kind = "sales_anomaly"
    schedule = "daily"
    requires: tuple[str, ...] = ()

    async def run(
        self, session: AsyncSession, ctx: AnalyticsContext, as_of: date
    ) -> list[InsightDraft]:
        health = await sync_health(session, ctx, as_of)
        if not health.trustworthy:
            # Missing data looks exactly like a collapse in trade. The stale
            # data detector is the one with something true to say right now.
            return []

        threshold = sensitivity_of(ctx)
        scan_from = as_of - timedelta(days=SALES_SCAN_DAYS)
        # The week being judged is excluded from the baseline, and the window
        # is widened to make up for it, so a weekday still has eight ordinary
        # observations behind it.
        baselines = await build(session, ctx, as_of=as_of, weeks=PERIODS + 1, judged_from=scan_from)

        drafts: list[InsightDraft] = []
        for observation in baselines.observations:
            if not scan_from <= observation.day < as_of or observation.is_event:
                continue
            baseline = baselines.for_day(observation.day, observation.location_id)
            if baseline is None or not baseline.usable:
                continue

            z = baseline.z(observation.net_sales)
            difference = baseline.difference(observation.net_sales)
            if abs(z) < threshold or abs(difference) < MIN_DOLLAR_DIFFERENCE:
                continue

            down = difference < 0
            direction = "below" if down else "above"
            weekday = observation.day.strftime("%A")
            where = f" at {observation.location_name}" if ctx.has("multi_location") else ""
            drafts.append(
                InsightDraft(
                    kind=self.kind,
                    severity=InsightSeverity.WARN if down else InsightSeverity.INFO,
                    title=(
                        f"{weekday} was ${abs(difference):,.0f} {direction} a normal "
                        f"{weekday}{where}"
                    ),
                    summary=(
                        f"{observation.day.isoformat()} took ${observation.net_sales:,.2f} "
                        f"against a usual ${baseline.median_net_sales:,.2f} for a {weekday}"
                        f"{where} — ${abs(difference):,.2f} {direction}, from "
                        f"{observation.orders} orders against a usual "
                        f"{baseline.median_orders:.0f}."
                    ),
                    dedupe_key=(
                        f"sales_anomaly:{observation.location_id}:{observation.day.isoformat()}"
                    ),
                    dollar_impact=abs(difference) if down else None,
                    evidence={
                        "day": observation.day.isoformat(),
                        "weekday": weekday,
                        "location": observation.location_name,
                        "net_sales": str(observation.net_sales),
                        "expected": str(baseline.median_net_sales),
                        "difference": str(difference),
                        "robust_z": str(z),
                        "threshold": str(threshold),
                        "orders": observation.orders,
                        "expected_orders": str(baseline.median_orders),
                        "baseline_weeks": baseline.observations,
                        "event_days_excluded": sorted(d.isoformat() for d in baselines.event_days),
                    },
                    suggested_action={
                        "type": "review_day",
                        "label": "Look at that day",
                        "route": f"dashboard?day={observation.day.isoformat()}",
                    },
                    expires_at=datetime.now(tz=UTC) + timedelta(days=7),
                )
            )
        return drafts


# --------------------------------------------------------------------------
# Shrink
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ShrinkItem:
    variant_id: uuid.UUID
    label: str
    sku: str | None
    units: Decimal
    value: Decimal
    at_cost: bool


class ShrinkDetector:
    kind = "possible_shrink"
    schedule = "daily"
    # Without a movement history there is nothing to reconcile a shelf against,
    # and guessing is how a feature accuses a shop's staff of theft.
    requires: tuple[str, ...] = ("has_inventory_history",)

    async def run(
        self, session: AsyncSession, ctx: AnalyticsContext, as_of: date
    ) -> list[InsightDraft]:
        window = DateRange(as_of - timedelta(days=SHRINK_WINDOW_DAYS - 1), as_of)
        start, end = window.bounds(ctx.tz)

        rows = await fetch_all(
            session,
            """
            with moved_out as (
                select m.variant_id as variant_id,
                       sum(case when m.kind = :sold then m.quantity else 0 end) as sold_moves,
                       sum(case when m.kind in (:damaged, :adjusted, :transferred, :counted)
                                then m.quantity else 0 end) as explained
                from inventory_movements m
                where m.tenant_id = :tenant and m.deleted_at is null
                  and m.occurred_at >= :start and m.occurred_at < :end
                group by 1
            ),
            rang_up as (
                select l.variant_id as variant_id, sum(l.quantity) as units
                from orders o
                join order_lines l on l.order_id = o.id and l.deleted_at is null
                where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
                  and o.placed_at >= :start and o.placed_at < :end
                  and l.variant_id is not null
                group by 1
            )
            select v.id as variant_id,
                   p.name as product_name,
                   v.name as variant_name,
                   v.sku as sku,
                   v.cost as cost,
                   v.price as price,
                   moved_out.sold_moves as sold_moves,
                   moved_out.explained as explained,
                   coalesce(rang_up.units, 0) as rang_up
            from moved_out
            join variants v on v.id = moved_out.variant_id and v.deleted_at is null
            join products p on p.id = v.product_id
            left join rang_up on rang_up.variant_id = moved_out.variant_id
            where moved_out.sold_moves - coalesce(rang_up.units, 0) > 0
            """,
            {
                "tenant": ctx.tenant_id,
                "start": start,
                "end": end,
                "sold": MovementKind.SOLD.value,
                "damaged": MovementKind.DAMAGED.value,
                "adjusted": MovementKind.ADJUSTED.value,
                "transferred": MovementKind.TRANSFERRED.value,
                "counted": MovementKind.COUNTED.value,
            },
        )

        items: list[ShrinkItem] = []
        for row in rows:
            missing = Decimal(row.sold_moves) - Decimal(row.rang_up)
            if missing <= 0:
                continue
            unit_value = row.cost if row.cost is not None else row.price
            if unit_value is None:
                continue
            label = row.product_name
            if row.variant_name and row.variant_name != row.product_name:
                label = f"{row.product_name} — {row.variant_name}"
            items.append(
                ShrinkItem(
                    variant_id=row.variant_id,
                    label=label,
                    sku=row.sku,
                    units=missing,
                    value=(missing * Decimal(unit_value)).quantize(Decimal("0.01")),
                    at_cost=row.cost is not None,
                )
            )

        total = sum((item.value for item in items), Decimal("0"))
        worst = max((item.units for item in items), default=Decimal("0"))
        if not items or (total < MIN_SHRINK_VALUE and worst < MIN_SHRINK_UNITS_ONE_ITEM):
            return []

        items.sort(key=lambda item: item.value, reverse=True)
        named = ", ".join(f"{item.label} ({item.units.normalize()})" for item in items[:5])
        basis = "at cost" if all(item.at_cost for item in items) else "at retail for some items"

        # One alert for the lot. Three items walking off the shelf is one
        # problem, and three separate alerts about it is how an owner learns
        # to ignore this detector.
        year, week, _ = as_of.isocalendar()
        return [
            InsightDraft(
                kind=self.kind,
                severity=InsightSeverity.URGENT,
                title=f"${total:,.0f} of stock left without a sale",
                summary=(
                    f"Over the last {SHRINK_WINDOW_DAYS} days, {len(items)} "
                    f"item{'s' if len(items) != 1 else ''} show"
                    f"{'' if len(items) != 1 else 's'} stock leaving with no sale, refund or "
                    f"recorded adjustment behind it: {named}. That is ${total:,.2f} {basis}. "
                    "Worth a count before assuming anything — a miskeyed adjustment looks "
                    "identical."
                ),
                dedupe_key=f"possible_shrink:{year}-W{week:02d}",
                dollar_impact=total,
                evidence={
                    "window_days": SHRINK_WINDOW_DAYS,
                    "as_of": as_of.isoformat(),
                    "basis": basis,
                    "items": [
                        {
                            "variant_id": str(item.variant_id),
                            "label": item.label,
                            "sku": item.sku,
                            "units_unaccounted": str(item.units),
                            "value": str(item.value),
                            "valued_at_cost": item.at_cost,
                        }
                        for item in items[:25]
                    ],
                },
                suggested_action={
                    "type": "count_stock",
                    "label": "Count these items",
                    "route": "inventory",
                },
                expires_at=datetime.now(tz=UTC) + timedelta(days=10),
            )
        ]


# --------------------------------------------------------------------------
# Refunds and discounts
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefundShare:
    """One location's giving-back for one period.

    A dict would do, but a week's refunds and the name of the shop they
    happened at are not the same kind of thing, and a `dict[str, Decimal | str]`
    says they are.
    """

    location_name: str
    net_sales: Decimal
    refunds: Decimal
    discounts: Decimal
    refund_share: Decimal
    discount_share: Decimal

    def amount(self, what: str) -> Decimal:
        return self.refunds if what == "refund" else self.discounts

    def share(self, what: str) -> Decimal:
        return self.refund_share if what == "refund" else self.discount_share


class RefundSpikeDetector:
    kind = "refund_spike"
    schedule = "weekly"
    requires: tuple[str, ...] = ()

    async def run(
        self, session: AsyncSession, ctx: AnalyticsContext, as_of: date
    ) -> list[InsightDraft]:
        """Judge every recent week, not only the last one.

        A bad refund week that nobody was around to notice is still worth
        surfacing, and the baseline is the trailing quarter either way, so the
        extra weeks cost one query each.
        """
        baseline_window = DateRange(as_of - timedelta(days=91), as_of - timedelta(days=1))
        normal = await _refund_shares(session, ctx, baseline_window)

        drafts: list[InsightDraft] = []
        for weeks_back in range(REFUND_SCAN_WEEKS):
            end = as_of - timedelta(days=7 * weeks_back + 1)
            drafts.extend(
                await self._week(session, ctx, DateRange(end - timedelta(days=6), end), normal)
            )
        return drafts

    async def _week(
        self,
        session: AsyncSession,
        ctx: AnalyticsContext,
        week: DateRange,
        normal: dict[uuid.UUID | None, RefundShare],
    ) -> list[InsightDraft]:
        recent = await _refund_shares(session, ctx, week)

        drafts: list[InsightDraft] = []
        for location_id, current in recent.items():
            usual = normal.get(location_id)
            if usual is None or current.net_sales <= 0:
                continue
            for what in ("refund", "discount"):
                share = current.share(what)
                baseline_share = usual.share(what)
                if share < REFUND_SHARE_FLOOR:
                    continue
                if baseline_share > 0 and share < baseline_share * REFUND_MULTIPLE:
                    continue
                if baseline_share == 0 and share < REFUND_SHARE_FLOOR * 2:
                    continue

                amount = current.amount(what)
                expected = (baseline_share * current.net_sales).quantize(Decimal("0.01"))
                where = f" at {current.location_name}" if ctx.has("multi_location") else ""
                drafts.append(
                    InsightDraft(
                        kind=self.kind,
                        severity=InsightSeverity.WARN,
                        title=(
                            f"{what.capitalize()}s were {share:.0%} of sales in the week of "
                            f"{week.start.isoformat()}{where}"
                        ),
                        summary=(
                            f"${amount:,.2f} of {what}s against ${current.net_sales:,.2f} of "
                            f"sales{where} in the week of {week.start.isoformat()} — "
                            f"{share:.1%}, where the last three months ran at "
                            f"{baseline_share:.1%}. At the usual rate that would have been "
                            f"about ${expected:,.2f}."
                        ),
                        dedupe_key=f"refund_spike:{what}:{location_id}:{week.start.isoformat()}",
                        dollar_impact=max(amount - expected, Decimal("0")),
                        evidence={
                            "kind": what,
                            "location": current.location_name,
                            "week_start": week.start.isoformat(),
                            "week_end": week.end.isoformat(),
                            "amount": str(amount),
                            "net_sales": str(current.net_sales),
                            "share": str(share),
                            "baseline_share": str(baseline_share),
                            "expected_amount": str(expected),
                        },
                        suggested_action={
                            "type": "review_refunds",
                            "label": "Look at that week",
                            "route": "dashboard",
                        },
                        expires_at=datetime.now(tz=UTC) + timedelta(days=10),
                    )
                )
        return drafts


async def _refund_shares(
    session: AsyncSession, ctx: AnalyticsContext, period: DateRange
) -> dict[uuid.UUID | None, RefundShare]:
    start, end = period.bounds(ctx.tz)
    rows = await fetch_all(
        session,
        """
        with sales as (
            select o.location_id as location_id,
                   coalesce(max(loc.name), 'Unknown location') as location_name,
                   sum(l.quantity * l.unit_price - l.discount) as net_sales,
                   sum(l.discount) as discounts
            from orders o
            join order_lines l on l.order_id = o.id and l.deleted_at is null
            left join locations loc on loc.id = o.location_id
            where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
              and o.placed_at >= :start and o.placed_at < :end
            group by 1
        ),
        given_back as (
            select o.location_id as location_id, sum(r.amount) as refunds
            from refunds r
            join orders o on o.id = r.order_id
            where r.tenant_id = :tenant and r.deleted_at is null
              and r.occurred_at >= :start and r.occurred_at < :end
            group by 1
        )
        select sales.location_id as location_id,
               sales.location_name as location_name,
               sales.net_sales as net_sales,
               sales.discounts as discounts,
               coalesce(given_back.refunds, 0) as refunds
        from sales
        left join given_back on given_back.location_id is not distinct from sales.location_id
        """,
        {"tenant": ctx.tenant_id, "start": start, "end": end},
    )
    out: dict[uuid.UUID | None, RefundShare] = {}
    for row in rows:
        net = Decimal(row.net_sales or 0)
        refunds = Decimal(row.refunds or 0)
        discounts = Decimal(row.discounts or 0)
        out[row.location_id] = RefundShare(
            location_name=row.location_name,
            net_sales=net,
            refunds=refunds,
            discounts=discounts,
            refund_share=(refunds / net).quantize(Decimal("0.0001")) if net else Decimal("0"),
            discount_share=(discounts / net).quantize(Decimal("0.0001")) if net else Decimal("0"),
        )
    return out


# --------------------------------------------------------------------------
# The plumbing itself
# --------------------------------------------------------------------------


class StaleDataDetector:
    kind = "stale_data"
    schedule = "daily"
    requires: tuple[str, ...] = ()

    async def run(
        self, session: AsyncSession, ctx: AnalyticsContext, as_of: date
    ) -> list[InsightDraft]:
        health = await sync_health(session, ctx, as_of)
        if not health.stale:
            return []

        if health.last_order_at is None:
            summary = (
                f"No sales have ever synced for {ctx.name}. Every number in the app is "
                "empty rather than zero until the connection works."
            )
            last = None
        else:
            last = health.last_order_at.astimezone(ctx.tz)
            usual = (
                f"about {health.typical_gap_hours} hours"
                if health.typical_gap_hours
                else "much shorter"
            )
            summary = (
                f"The last sale we have is from {last:%d %b, %H:%M} — {health.hours_since} "
                f"hours ago, where the usual quiet gap here is {usual}. Until this clears, "
                "today's figures are missing rather than low."
            )

        return [
            InsightDraft(
                kind=self.kind,
                severity=InsightSeverity.URGENT,
                title="We have not seen a sale in a while",
                summary=summary,
                dedupe_key=f"stale_data:{as_of.isoformat()}",
                evidence={
                    "last_order_at": (
                        health.last_order_at.isoformat() if health.last_order_at else None
                    ),
                    "hours_since": str(health.hours_since) if health.hours_since else None,
                    "typical_gap_hours": (
                        str(health.typical_gap_hours) if health.typical_gap_hours else None
                    ),
                },
                suggested_action={
                    "type": "check_connection",
                    "label": "Check the connection",
                    "route": "data",
                },
                expires_at=datetime.now(tz=UTC) + timedelta(days=3),
            )
        ]


class ImpossibleValueDetector:
    kind = "impossible_values"
    schedule = "daily"
    requires: tuple[str, ...] = ()

    async def run(
        self, session: AsyncSession, ctx: AnalyticsContext, as_of: date
    ) -> list[InsightDraft]:
        rows = await fetch_all(
            session,
            """
            select v.id as variant_id,
                   p.name as product_name,
                   v.name as variant_name,
                   v.sku as sku,
                   sum(i.on_hand) as on_hand
            from inventory_levels i
            join variants v on v.id = i.variant_id and v.deleted_at is null
            join products p on p.id = v.product_id
            where i.tenant_id = :tenant and i.deleted_at is null
            group by 1, 2, 3, 4
            having sum(i.on_hand) < 0
            order by sum(i.on_hand) asc
            limit 50
            """,
            {"tenant": ctx.tenant_id},
        )
        if not rows:
            return []

        named = ", ".join(
            f"{row.product_name} ({Decimal(row.on_hand).normalize()})" for row in rows[:5]
        )
        return [
            InsightDraft(
                kind=self.kind,
                severity=InsightSeverity.WARN,
                title=f"{len(rows)} items show negative stock",
                summary=(
                    f"{len(rows)} items are recorded as below zero on hand: {named}. That is "
                    "always a data problem rather than a real one — usually a sale rung up "
                    "before the receiving was entered — and it makes reorder suggestions for "
                    "those items wrong until it is corrected."
                ),
                dedupe_key=f"impossible_values:{as_of.isoformat()}",
                evidence={
                    "items": [
                        {
                            "variant_id": str(row.variant_id),
                            "label": row.product_name,
                            "sku": row.sku,
                            "on_hand": str(row.on_hand),
                        }
                        for row in rows
                    ]
                },
                suggested_action={
                    "type": "count_stock",
                    "label": "Correct these counts",
                    "route": "inventory",
                },
                expires_at=datetime.now(tz=UTC) + timedelta(days=14),
            )
        ]


# --------------------------------------------------------------------------
# Tuning
# --------------------------------------------------------------------------


@dataclass(slots=True)
class DetectorPrecision:
    kind: str
    raised: int
    rated: int
    useful: int

    @property
    def precision(self) -> Decimal | None:
        """Of the alerts somebody rated, the share they found useful."""
        if not self.rated:
            return None
        return (Decimal(self.useful) / Decimal(self.rated)).quantize(Decimal("0.01"))


async def precision_report(
    session: AsyncSession, ctx: AnalyticsContext | None = None, *, days: int = 90
) -> list[DetectorPrecision]:
    """Per-detector precision, from the Useful / Not useful buttons.

    Internal: this is the number the thresholds above should be moved on, and
    it is the reason those buttons exist at all. Across all tenants unless one
    is named, because a threshold is a product decision.
    """
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)
    query = (
        select(
            Insight.kind,
            func.count(),
            func.count().filter(Insight.was_useful.is_not(None)),
            func.count().filter(Insight.was_useful.is_(True)),
        )
        .where(Insight.created_at >= cutoff)
        .group_by(Insight.kind)
    )
    if ctx is not None:
        query = query.where(Insight.tenant_id == ctx.tenant_id)

    rows = (await session.execute(query)).all()
    return sorted(
        (
            DetectorPrecision(kind=kind, raised=int(raised), rated=int(rated), useful=int(useful))
            for kind, raised, rated, useful in rows
        ),
        key=lambda report: report.raised,
        reverse=True,
    )


register(SalesAnomalyDetector())
register(ShrinkDetector())
register(RefundSpikeDetector())
register(StaleDataDetector())
register(ImpossibleValueDetector())
