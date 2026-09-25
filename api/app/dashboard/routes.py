"""HTTP for the dashboard, the inventory table and pinned charts.

Every figure on these screens comes out of `app.analytics`. The routes choose
the window and the limits; they never add up a column themselves.

Two ideas shape the shape of the response. The first is that a widget a shop
cannot support does not render at all, so each one arrives wrapped: either
`available` with data, or unavailable with a sentence saying why, which the UI
prints instead of an empty chart. The second is that the whole first paint is
one request, so every widget on the screen is cut from the same period and the
same sync — a dashboard whose KPI row is a second newer than its chart is a
dashboard people stop trusting.

A tenant is named in the path and resolved here. No id ever arrives from the
client, and nothing from the client is interpolated into SQL. The caller's right
to this shop was already settled before any of these functions ran, by
`app.accounts.guard`, which is installed across the whole app — so `_context`
resolves a slug it already knows the caller is entitled to.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.roles import MemberRole
from app.accounts.tables import Membership
from app.agent.charts import ChartSpec
from app.analytics import (
    METRICS,
    AnalyticsContext,
    Breakdown,
    CapabilityUnavailable,
    CatalogPage,
    CatalogSort,
    Caveat,
    DateRange,
    Filters,
    Grain,
    SalesSeries,
    StockList,
    TenantNotFound,
    catalog_page,
    category_breakdown,
    channel_breakdown,
    dead_stock,
    inventory_value,
    load_context,
    location_breakdown,
    low_stock,
    sales_series,
    sales_summary,
    source_breakdown,
    top_products,
)
from app.analytics.queries import data_window
from app.canonical import tables as t
from app.db import get_session
from app.http import MANAGER_ONLY, SessionDep, ViewerDep

router = APIRouter(tags=["dashboard"])

# How many rows a dashboard table shows before "see all" is the better answer.
TABLE_ROWS = 8
RANKED_ROWS = 10


class Widget[T](BaseModel):
    """One thing on the screen, or the reason there is nothing to show.

    `available: false` is not an error. It is the shop's system saying it does
    not record this, and the sentence in `reason` is written for the owner —
    the UI prints it where the widget would have been rather than drawing an
    empty chart, which reads as "you sold nothing".
    """

    available: bool = True
    reason: str | None = None
    data: T | None = None


async def _widget[T](build: Callable[[], Awaitable[T]]) -> Widget[T]:
    try:
        return Widget(data=await build())
    except CapabilityUnavailable as exc:
        return Widget(available=False, reason=exc.message)


class Period(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date
    days: int
    label: str

    @classmethod
    def of(cls, span: DateRange) -> Period:
        return cls(start=span.start, end=span.end, days=span.days, label=span.label)


class Kpi(BaseModel):
    """A headline number, with what it means and what it was before.

    The definition travels with the figure so the screen can show it on hover:
    "net sales" is only trustworthy if the owner can check it means what their
    POS means by it.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    definition: str
    formula: str
    unit: str
    value: Decimal
    # None where a comparison would be meaningless — stock on hand is a
    # snapshot of right now, and has no "previous 30 days".
    previous: Decimal | None = None
    change: Decimal | None = None


class LocationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    name: str


class ShopSource(BaseModel):
    """One register a shop runs, as a screen needs it.

    `capabilities` is this register's own, not the shop's union: "the marketplace
    export has no costs" is the sentence that makes a partial margin figure make
    sense, and it cannot be said from the union.
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    label: str
    capabilities: dict[str, bool]


class ShopProfile(BaseModel):
    """Everything the app shell needs to render a shop honestly."""

    model_config = ConfigDict(extra="forbid")

    tenant: str
    name: str
    timezone: str
    currency: str
    today: date
    capabilities: dict[str, bool]
    locations: list[LocationOut]
    channels: list[str]
    categories: list[str]
    # Every register this shop runs. One entry for most shops; the screens use
    # the length of this to decide whether consolidation is worth a headline.
    sources: list[ShopSource]
    data_from: date | None
    data_to: date | None
    # The shop's own color, or None for the palette Direction A ships with.
    # Whether it is *usable* is decided in the browser against the real
    # surfaces; see `web/src/lib/brand.ts`.
    brand_color: str | None


class DashboardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant: str
    name: str
    currency: str
    timezone: str
    period: Period
    previous: Period
    kpis: list[Kpi]
    kpi_caveats: list[Caveat]
    sales_over_time: Widget[SalesSeries]
    top_products: Widget[Breakdown]
    category_mix: Widget[Breakdown]
    by_location: Widget[Breakdown]
    by_channel: Widget[Breakdown]
    # Net sales per register. Unavailable, with a sentence, for a shop whose tills
    # are all the same system — there is nothing to consolidate and a one-bar
    # chart claiming otherwise would be noise.
    by_source: Widget[Breakdown]
    low_stock: Widget[StockList]
    dead_stock: Widget[StockList]


class TenantOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant: str
    name: str
    timezone: str
    currency: str
    capabilities: dict[str, bool]
    # What the caller may do here. The shop switcher shows it, and the screens
    # use it to hide a button rather than offer one that will 403.
    role: MemberRole


class PinnedChart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    title: str
    spec: dict[str, Any]
    position: int
    created_at: datetime


class PinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: ChartSpec
    # Defaults to the chart's own title; overridable so a pinned copy can be
    # renamed without touching the conversation it came from.
    title: str | None = Field(default=None, max_length=200)
    source_message_id: uuid.UUID | None = None


async def _context(session: AsyncSession, slug: str) -> AnalyticsContext:
    try:
        return await load_context(session, slug)
    except TenantNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _locations(session: AsyncSession, ctx: AnalyticsContext) -> list[LocationOut]:
    rows = (
        await session.execute(
            select(t.Location.id, t.Location.name)
            .where(t.Location.tenant_id == ctx.tenant_id, t.Location.deleted_at.is_(None))
            .order_by(t.Location.name)
        )
    ).all()
    return [LocationOut(id=row.id, name=row.name) for row in rows]


async def _channels(session: AsyncSession, ctx: AnalyticsContext) -> list[str]:
    """Channels this shop has actually sold through, not the ones it could."""
    return list(
        (
            await session.execute(
                select(t.Order.channel)
                .where(t.Order.tenant_id == ctx.tenant_id, t.Order.deleted_at.is_(None))
                .distinct()
                .order_by(t.Order.channel)
            )
        )
        .scalars()
        .all()
    )


def _kpi(key: str, value: Decimal, previous: Decimal | None = None) -> Kpi:
    """Pair a number with its definition, and with what it was last period."""
    metric = METRICS[key]
    change: Decimal | None = None
    if previous is not None and previous != 0:
        change = ((value - previous) / previous).quantize(Decimal("0.0001"))
    return Kpi(
        key=metric.key,
        label=metric.label,
        definition=metric.definition,
        formula=metric.formula,
        unit=metric.unit,
        value=value,
        previous=previous,
        change=change,
    )


@router.get("/tenants", response_model=list[TenantOut])
async def list_tenants(viewer: ViewerDep, session: SessionDep) -> list[TenantOut]:
    """The shops you may open, with what each one can do.

    What the shop switcher reads. An empty list is a normal answer: it means the
    account is real but has not been added to a shop, and the UI says so rather
    than showing a broken picker.

    This is the only route where the tenant is not in the path, so it is also the
    only one that has to do its own filtering — the join on `memberships` *is* the
    access check here.
    """
    rows = (
        await session.execute(
            select(t.Tenant, Membership.role)
            .join(Membership, Membership.tenant_id == t.Tenant.id)
            .where(Membership.user_id == viewer.id, t.Tenant.deleted_at.is_(None))
            .order_by(t.Tenant.name)
        )
    ).all()

    out: list[TenantOut] = []
    for row, role in rows:
        ctx = await load_context(session, row.slug)
        out.append(
            TenantOut(
                tenant=row.slug,
                name=row.name,
                timezone=row.timezone,
                currency=row.currency,
                capabilities=ctx.capabilities.model_dump(),
                role=role,
            )
        )
    return out


@router.get("/tenants/{slug}/profile", response_model=ShopProfile)
async def shop_profile(
    slug: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> ShopProfile:
    ctx = await _context(session, slug)
    categories = list(
        (
            await session.execute(
                select(t.Category.name)
                .where(t.Category.tenant_id == ctx.tenant_id, t.Category.deleted_at.is_(None))
                .order_by(t.Category.name)
            )
        )
        .scalars()
        .all()
    )

    # How far the data goes, so the UI can say "synced through Tuesday" rather
    # than let an empty week read as a bad week.
    first_at, last_at = await data_window(session, ctx)

    return ShopProfile(
        tenant=ctx.slug,
        name=ctx.name,
        timezone=ctx.timezone,
        currency=ctx.currency,
        today=ctx.today(),
        capabilities=ctx.capabilities.model_dump(),
        locations=await _locations(session, ctx),
        channels=await _channels(session, ctx),
        categories=categories,
        sources=[
            ShopSource(
                source=ref.source,
                label=ref.label,
                capabilities=ref.capabilities.model_dump(),
            )
            for ref in ctx.sources
        ],
        data_from=first_at.astimezone(ctx.tz).date() if first_at else None,
        data_to=last_at.astimezone(ctx.tz).date() if last_at else None,
        brand_color=ctx.setting("brand_color", None),
    )


class AppearanceIn(BaseModel):
    """The shop's color, or null to go back to the one we ship.

    Validated as a six-digit hex here and nothing more. The question this
    endpoint cannot answer is the interesting one — whether text set in that
    color can be read on the surfaces it lands on, in both themes — because
    answering it means knowing the palette, and the palette lives in
    `web/src/style.css`. Duplicating it in Python would give us two palettes
    that drift, and the gate would start passing a palette nobody ships.

    So the gate is in the browser, where the real values are. The client
    refuses to *apply* a stored color that fails it, which closes the loop for
    anything written past the picker.
    """

    model_config = ConfigDict(extra="forbid")

    brand_color: Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")] | None = None


class AppearanceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant: str
    brand_color: str | None


@router.put("/tenants/{slug}/appearance", response_model=AppearanceOut, dependencies=MANAGER_ONLY)
async def set_appearance(
    slug: str,
    body: AppearanceIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppearanceOut:
    """Store the shop's color on the tenant, not in the browser.

    In `Tenant.settings` rather than a column of its own, which is where the
    shop's other preferences already live (`low_stock_threshold`,
    `dead_stock_days`) and means no migration. It follows the owner to the
    phone behind the counter, which is the point: a color kept in
    localStorage would be a different shop on every device.
    """
    ctx = await _context(session, slug)
    tenant = await session.get(t.Tenant, ctx.tenant_id)
    if tenant is None:  # pragma: no cover - _context already resolved it
        raise HTTPException(status_code=404, detail=f"No shop called {slug!r}.")

    color = body.brand_color.lower() if body.brand_color else None
    settings = dict(tenant.settings or {})
    if color is None:
        settings.pop("brand_color", None)
    else:
        settings["brand_color"] = color
    # Reassigned rather than mutated: a plain JSONB column does not track
    # in-place changes, so mutating the dict would commit nothing.
    tenant.settings = settings
    await session.commit()

    return AppearanceOut(tenant=ctx.slug, brand_color=color)


@router.get("/tenants/{slug}/dashboard", response_model=DashboardResponse)
async def dashboard(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query(ge=1, le=400, description="length of the headline period")] = 30,
    weeks: Annotated[int, Query(ge=4, le=104, description="how far the chart looks back")] = 52,
    grain: Annotated[Grain, Query(description="bucket size for the chart")] = Grain.WEEK,
) -> DashboardResponse:
    """The whole first paint, cut from one period and one read of the data.

    `grain` is additive and defaults to the weekly buckets every caller before
    it assumed, so an old client asking without it gets exactly what it got
    before. The analytics layer has always supported day and month — only this
    route was hardcoded — so the owner can now ask "per day" of the same
    definitions rather than a different query.

    The window stays `weeks`, so a caller wanting daily detail asks for fewer of
    them: 52 weeks of daily points is 364 marks on a phone-width axis, which is
    a smear rather than a chart.
    """
    ctx = await _context(session, slug)
    period = ctx.last_days(days)
    previous = period.previous()
    chart_period = ctx.last_days(weeks * 7)

    current = await sales_summary(session, ctx, period)
    before = await sales_summary(session, ctx, previous)
    stock = await inventory_value(session, ctx)

    kpis = [
        _kpi("net_sales", current.net_sales, before.net_sales),
        _kpi("order_count", Decimal(current.order_count), Decimal(before.order_count)),
        _kpi("average_order_value", current.average_order_value, before.average_order_value),
        _kpi("inventory_value", stock.retail_value),
    ]

    # The KPI row is two results, so it carries both their caveats, deduped:
    # "12% of items have no cost" only needs saying once.
    seen: set[str] = set()
    kpi_caveats: list[Caveat] = []
    for caveat in [*current.caveats, *stock.caveats]:
        if caveat.code not in seen:
            seen.add(caveat.code)
            kpi_caveats.append(caveat)

    channels = await _channels(session, ctx)

    async def channel_split() -> Breakdown:
        if len(channels) < 2:
            raise CapabilityUnavailable(
                "has_online_channel",
                f"Every sale at {ctx.name} happens the same way, so there is nothing to "
                "split by channel.",
            )
        return await channel_breakdown(session, ctx, period)

    async def source_split() -> Breakdown:
        """One row per register, or a sentence saying there is only one.

        The gate is deliberately here rather than in the metric. A dashboard has
        to decide whether a section is worth the space, and for the great majority
        of shops — everybody whose tills are all one brand — it is not.
        `source_breakdown` itself always answers, because the month-end packet
        prints the split unconditionally and a bookkeeper wants the row even when
        there is one of it.
        """
        if not ctx.has_more_than_one_source:
            only = ctx.sources[0].label if ctx.sources else "one system"
            raise CapabilityUnavailable(
                "multi_source",
                f"Everything {ctx.name} sells goes through {only}, so there is nothing "
                "to add together yet. Connect a second register and this splits by system.",
            )
        return await source_breakdown(session, ctx, period)

    return DashboardResponse(
        tenant=ctx.slug,
        name=ctx.name,
        currency=ctx.currency,
        timezone=ctx.timezone,
        period=Period.of(period),
        previous=Period.of(previous),
        kpis=kpis,
        kpi_caveats=kpi_caveats,
        sales_over_time=await _widget(lambda: sales_series(session, ctx, chart_period, grain)),
        top_products=await _widget(lambda: top_products(session, ctx, period, limit=RANKED_ROWS)),
        category_mix=await _widget(
            lambda: category_breakdown(session, ctx, period, limit=RANKED_ROWS)
        ),
        by_location=await _widget(lambda: location_breakdown(session, ctx, period)),
        by_channel=await _widget(channel_split),
        by_source=await _widget(source_split),
        low_stock=await _widget(lambda: low_stock(session, ctx, limit=TABLE_ROWS)),
        dead_stock=await _widget(lambda: dead_stock(session, ctx, limit=TABLE_ROWS)),
    )


@router.get("/tenants/{slug}/inventory", response_model=CatalogPage)
async def inventory(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str | None, Query(max_length=120, description="name, SKU or category")] = None,
    sort: CatalogSort = CatalogSort.NAME,
    desc: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    location: Annotated[uuid.UUID | None, Query(description="count stock at one location")] = None,
) -> CatalogPage:
    """The inventory table: what is on the shelf, what it costs, what it makes."""
    ctx = await _context(session, slug)
    filters = Filters(location_ids=(location,) if location else ())
    return await catalog_page(
        session,
        ctx,
        query=q,
        sort=sort,
        descending=desc,
        limit=limit,
        offset=offset,
        filters=filters,
    )


@router.get("/tenants/{slug}/charts", response_model=list[PinnedChart])
async def pinned_charts(
    slug: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> list[PinnedChart]:
    ctx = await _context(session, slug)
    rows = (
        (
            await session.execute(
                select(t.SavedChart)
                .where(
                    t.SavedChart.tenant_id == ctx.tenant_id,
                    t.SavedChart.deleted_at.is_(None),
                )
                .order_by(t.SavedChart.position, t.SavedChart.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [
        PinnedChart(
            id=row.id,
            title=row.title,
            spec=row.spec,
            position=row.position,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post(
    "/tenants/{slug}/charts", response_model=PinnedChart, status_code=201, dependencies=MANAGER_ONLY
)
async def pin_chart(
    slug: str,
    body: PinRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PinnedChart:
    """Keep a chart the assistant drew.

    The spec is stored exactly as it was validated during the conversation —
    re-running the question later could give a different answer, and a pinned
    chart is a record of what was said, not a live query.
    """
    ctx = await _context(session, slug)
    last = (
        await session.execute(
            select(t.SavedChart.position)
            .where(t.SavedChart.tenant_id == ctx.tenant_id, t.SavedChart.deleted_at.is_(None))
            .order_by(t.SavedChart.position.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    chart = t.SavedChart(
        tenant_id=ctx.tenant_id,
        title=body.title or body.spec.title,
        spec=body.spec.model_dump(mode="json"),
        source_message_id=body.source_message_id,
        position=0 if last is None else last + 1,
    )
    session.add(chart)
    await session.commit()
    await session.refresh(chart)

    return PinnedChart(
        id=chart.id,
        title=chart.title,
        spec=chart.spec,
        position=chart.position,
        created_at=chart.created_at,
    )


@router.delete("/tenants/{slug}/charts/{chart_id}", status_code=204, dependencies=MANAGER_ONLY)
async def unpin_chart(
    slug: str,
    chart_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    ctx = await _context(session, slug)
    chart = (
        await session.execute(
            select(t.SavedChart).where(
                # Scoped in the query rather than checked afterwards: a chart
                # belonging to another shop simply does not exist here.
                t.SavedChart.id == chart_id,
                t.SavedChart.tenant_id == ctx.tenant_id,
                t.SavedChart.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if chart is None:
        raise HTTPException(status_code=404, detail="no such chart")

    chart.deleted_at = datetime.now(tz=ctx.tz)
    await session.commit()
