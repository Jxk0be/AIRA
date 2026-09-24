"""The tools the assistant is given, and nothing else.

Every one is a thin wrapper over `app.analytics` or `app.rag`. No arithmetic
happens here, and the model never writes SQL (CLAUDE.md rule 4) — it picks a
tool and some arguments, and the semantic layer does the maths. That is what
makes the number in a sentence the same as the number on the dashboard.

Three rules hold across all of them:

* **`tenant_id` is never a model-controlled argument.** It comes from the
  request's `ShopContext`. There is no tool argument that could point at another
  shop's data, so there is nothing to get wrong.
* **Names, not ids.** The model has never seen a uuid and should not have to
  invent one. Tools take "Manga" or "Con Booth" and resolve them here, and an
  unknown name comes back as an error listing the real ones.
* **A tool that cannot answer says so.** `CapabilityUnavailable` from the
  semantic layer is returned as the tool's error result, in the words a shop
  owner would want, rather than crashing the turn or returning zeros.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from anthropic.types import ToolParam
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import analytics as an
from app.agent.charts import ChartRejected, ChartSpec, ChartType, Observed, validate
from app.agent.context import ShopContext
from app.canonical import tables as t
from app.canonical.enums import Channel
from app.rag import SearchMode, search
from app.rag.embeddings import Embedder, EmbeddingError

DEFAULT_DAYS = 30
MAX_ROWS = 25


class ToolError(RuntimeError):
    """This call cannot be answered. The message goes back to the model."""


@dataclass
class ToolContext:
    """Everything a tool is allowed to touch."""

    session: AsyncSession
    shop: ShopContext
    # Built lazily: a question about sales should not pay for an embedder.
    embedder: Embedder | None = None
    # Every scalar every tool has returned this turn, for chart validation.
    observed: Observed = field(default_factory=Observed)
    charts: list[ChartSpec] = field(default_factory=list)

    @property
    def analytics(self) -> an.AnalyticsContext:
        return self.shop.analytics


Handler = Callable[[ToolContext, Any], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    handler: Handler
    # Capabilities the tenant's source must declare for this tool to exist.
    requires: tuple[str, ...] = ()

    def as_param(self) -> ToolParam:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        return ToolParam(
            name=self.name,
            description=self.description,
            input_schema=schema,
        )

    async def call(self, ctx: ToolContext, raw: dict[str, Any]) -> Any:
        """Validate the arguments, then run.

        Deliberately not `strict: true` on the tool definition: half of these
        arguments are optional dates, strict mode wants every property present,
        and a Pydantic error handed back to the model is a better fix loop than
        a schema that makes the common call awkward to write.
        """
        try:
            parsed = self.args.model_validate(raw)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}"
                for error in exc.errors()[:4]
            )
            raise ToolError(f"Those arguments are not valid — {problems}") from exc
        return await self.handler(ctx, parsed)


# --------------------------------------------------------------------------
# Argument shapes
# --------------------------------------------------------------------------

ChannelName = Literal["in_store", "online", "event", "other"]


class Period(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date | None = Field(
        default=None, description="First day to include, YYYY-MM-DD, in the shop's timezone."
    )
    end_date: date | None = Field(
        default=None, description="Last day to include, inclusive. Defaults to today."
    )


class SalesArgs(Period):
    location: str | None = Field(
        default=None, description="Limit to one location, by name as listed in the briefing."
    )
    channel: ChannelName | None = Field(
        default=None, description="Limit to one channel: in_store, online, event or other."
    )
    category: str | None = Field(
        default=None,
        description="Limit to one category, by name. Includes any categories filed under it.",
    )


class RankedArgs(SalesArgs):
    limit: int = Field(default=10, ge=1, le=MAX_ROWS, description="How many rows to return.")


class SeriesArgs(SalesArgs):
    grain: Literal["day", "week", "month"] = Field(
        default="week", description="Bucket size for the series."
    )


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=400, description="What to look for.")
    kind: Literal["product", "document"] | None = Field(
        default=None,
        description="Restrict to catalogue products or to uploaded documents (policies, FAQs).",
    )
    limit: int = Field(default=5, ge=1, le=15)


class StockArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location: str | None = Field(default=None, description="Limit to one location, by name.")
    category: str | None = Field(default=None, description="Limit to one category, by name.")
    limit: int = Field(default=15, ge=1, le=MAX_ROWS)


class LowStockArgs(StockArgs):
    threshold: int | None = Field(
        default=None,
        ge=0,
        description="Units at or below which an item counts as low. Defaults to the shop's own.",
    )
    days_of_cover: int = Field(
        default=14, ge=1, le=120, description="Also flag anything with less cover than this."
    )


class DeadStockArgs(StockArgs):
    days: int | None = Field(
        default=None,
        ge=1,
        le=1000,
        description="How long with no sale counts as dead. Defaults to the shop's own setting.",
    )


class ReorderArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor: str | None = Field(
        default=None, description="Limit to one supplier, by name as it appears on the order."
    )
    category: str | None = Field(default=None, description="Limit to one category, by name.")
    limit: int = Field(default=15, ge=1, le=MAX_ROWS)


class BusiestHoursArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location: str | None = Field(default=None, description="Limit to one location, by name.")
    weekday: (
        Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"] | None
    ) = Field(default=None, description="Limit to one day of the week.")
    limit: int = Field(default=8, ge=1, le=MAX_ROWS)


class SplitArgs(SalesArgs):
    by: Literal["channel", "location"] = Field(
        default="channel", description="Which way to split the period's sales."
    )


class MakeChartArgs(BaseModel):
    """Deliberately flat, and deliberately strict about where `data` comes from."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["line", "bar", "pie", "table"]
    title: str = Field(max_length=200)
    x: str = Field(description="The key in each row that labels the point.")
    y: list[str] = Field(min_length=1, max_length=6, description="One key per series.")
    data: list[dict[str, Any]] = Field(
        min_length=1,
        max_length=400,
        description=(
            "Rows copied from a previous tool result in this turn. Every number must be one a "
            "tool returned — the chart is rejected otherwise."
        ),
    )
    note: str | None = Field(default=None, max_length=400)


# --------------------------------------------------------------------------
# Resolving what the model said into what analytics needs
# --------------------------------------------------------------------------


def resolve_period(ctx: ToolContext, args: Period) -> an.DateRange:
    """Turn two optional dates into a range, the way a person would mean it."""
    today = ctx.shop.today
    if args.start_date and args.end_date:
        start, end = args.start_date, args.end_date
    elif args.start_date:
        start, end = args.start_date, max(args.start_date, today)
    elif args.end_date:
        start, end = args.end_date - timedelta(days=DEFAULT_DAYS - 1), args.end_date
    else:
        return ctx.shop.default_period(DEFAULT_DAYS)

    if end < start:
        raise ToolError(f"end_date {end} is before start_date {start}.")
    return an.DateRange(start, end)


async def _category_ids(ctx: ToolContext, name: str) -> tuple[uuid.UUID, ...]:
    rows = (
        await ctx.session.execute(
            select(t.Category.id, t.Category.name).where(
                t.Category.tenant_id == ctx.shop.tenant_id, t.Category.deleted_at.is_(None)
            )
        )
    ).all()
    wanted = name.strip().lower()
    exact = [row.id for row in rows if row.name.lower() == wanted]
    if exact:
        return tuple(exact)
    partial = [row.id for row in rows if wanted in row.name.lower()]
    if len(partial) == 1:
        return tuple(partial)
    known = ", ".join(sorted(row.name for row in rows)) or "none"
    raise ToolError(f"No category called {name!r}. This shop's categories are: {known}.")


async def resolve_filters(ctx: ToolContext, args: SalesArgs | StockArgs) -> an.Filters:
    location_ids: tuple[uuid.UUID, ...] = ()
    channels: tuple[Channel, ...] = ()
    category_ids: tuple[uuid.UUID, ...] = ()

    if args.location:
        found = ctx.shop.location_named(args.location)
        if found is None:
            known = ", ".join(location.name for location in ctx.shop.locations) or "none"
            raise ToolError(f"No location called {args.location!r}. Locations are: {known}.")
        location_ids = (found.id,)

    channel = getattr(args, "channel", None)
    if channel:
        channels = (Channel(channel),)

    if args.category:
        category_ids = await _category_ids(ctx, args.category)

    return an.Filters(location_ids=location_ids, channels=channels, category_ids=category_ids)


def _dump(result: an.Result, args: BaseModel | None = None) -> dict[str, Any]:
    """A result as JSON the model reads.

    `mode="json"` keeps money exact by rendering Decimals as strings, which is
    what we want: a float would let 18534.57 come back as 18534.569999999999
    and fail chart validation for a reason nobody could debug.

    The narrowing is echoed back with it. A category-filtered summary that does
    not say "Manga" anywhere in its output reads as the whole shop's figure —
    to the model, to the tool chip in the UI, and to anyone auditing an answer
    later.
    """
    payload = result.model_dump(mode="json", exclude_none=True)
    if args is not None:
        applied = {
            name: value
            for name, value in (
                ("location", getattr(args, "location", None)),
                ("channel", getattr(args, "channel", None)),
                ("category", getattr(args, "category", None)),
            )
            if value
        }
        if applied:
            payload["filtered_by"] = applied
    return payload


# --------------------------------------------------------------------------
# The tools themselves
# --------------------------------------------------------------------------


async def _search_catalog(ctx: ToolContext, args: SearchArgs) -> dict[str, Any]:
    if ctx.embedder is None:
        raise ToolError("Search is not available right now.")
    try:
        hits = await search(
            ctx.session,
            ctx.shop.tenant_id,
            args.query,
            ctx.embedder,
            k=args.limit,
            filters={"kind": args.kind} if args.kind else None,
            mode=SearchMode.HYBRID,
        )
    except EmbeddingError as exc:
        raise ToolError(f"Search is unavailable: {exc}") from exc

    return {
        "query": args.query,
        "results": [
            {
                "title": hit.title,
                "kind": hit.metadata.get("kind", hit.source),
                "text": hit.content[:600],
            }
            for hit in hits
        ],
    }


async def _sales_summary(ctx: ToolContext, args: SalesArgs) -> dict[str, Any]:
    return _dump(
        await an.sales_summary(
            ctx.session, ctx.analytics, resolve_period(ctx, args), await resolve_filters(ctx, args)
        ),
        args,
    )


async def _sales_over_time(ctx: ToolContext, args: SeriesArgs) -> dict[str, Any]:
    return _dump(
        await an.sales_series(
            ctx.session,
            ctx.analytics,
            resolve_period(ctx, args),
            an.Grain(args.grain),
            await resolve_filters(ctx, args),
        ),
        args,
    )


async def _top_products(ctx: ToolContext, args: RankedArgs) -> dict[str, Any]:
    return _dump(
        await an.top_products(
            ctx.session,
            ctx.analytics,
            resolve_period(ctx, args),
            await resolve_filters(ctx, args),
            limit=args.limit,
        ),
        args,
    )


async def _category_breakdown(ctx: ToolContext, args: RankedArgs) -> dict[str, Any]:
    return _dump(
        await an.category_breakdown(
            ctx.session,
            ctx.analytics,
            resolve_period(ctx, args),
            await resolve_filters(ctx, args),
            limit=args.limit,
        ),
        args,
    )


async def _split(ctx: ToolContext, args: SplitArgs) -> dict[str, Any]:
    period = resolve_period(ctx, args)
    filters = await resolve_filters(ctx, args)
    if args.by == "location":
        return _dump(await an.location_breakdown(ctx.session, ctx.analytics, period, filters), args)
    return _dump(await an.channel_breakdown(ctx.session, ctx.analytics, period, filters), args)


async def _margin_report(ctx: ToolContext, args: SalesArgs) -> dict[str, Any]:
    return _dump(
        await an.margin_report(
            ctx.session, ctx.analytics, resolve_period(ctx, args), await resolve_filters(ctx, args)
        ),
        args,
    )


async def _customer_stats(ctx: ToolContext, args: SalesArgs) -> dict[str, Any]:
    return _dump(
        await an.customer_stats(
            ctx.session, ctx.analytics, resolve_period(ctx, args), await resolve_filters(ctx, args)
        ),
        args,
    )


async def _inventory_value(ctx: ToolContext, args: StockArgs) -> dict[str, Any]:
    return _dump(
        await an.inventory_value(ctx.session, ctx.analytics, await resolve_filters(ctx, args)), args
    )


async def _sell_through(ctx: ToolContext, args: RankedArgs) -> dict[str, Any]:
    return _dump(
        await an.sell_through(
            ctx.session,
            ctx.analytics,
            resolve_period(ctx, args),
            await resolve_filters(ctx, args),
            limit=args.limit,
        ),
        args,
    )


def _stock_rows(listing: an.StockList) -> dict[str, Any]:
    """Stock lists trimmed to what the answer needs.

    The full row carries price, cost, category and timestamps; a reorder answer
    needs the name, what is left and why it was flagged, and every field beyond
    that is tokens on every turn of the conversation.
    """
    return {
        "period_start": listing.period_start.isoformat(),
        "period_end": listing.period_end.isoformat(),
        "row_count": listing.row_count,
        "truncated": listing.truncated,
        "retail_value": str(listing.retail_value),
        "caveats": [caveat.message for caveat in listing.caveats],
        "rows": [
            {
                "product": row.product_name,
                "variant": row.variant_name,
                "sku": row.sku,
                "units_on_hand": str(row.units_on_hand),
                "days_of_cover": str(row.days_of_cover) if row.days_of_cover is not None else None,
                "retail_value": str(row.retail_value),
                "why": row.reason,
            }
            for row in listing.rows
        ],
    }


async def _low_stock(ctx: ToolContext, args: LowStockArgs) -> dict[str, Any]:
    return _stock_rows(
        await an.low_stock(
            ctx.session,
            ctx.analytics,
            await resolve_filters(ctx, args),
            threshold=args.threshold,
            cover_days=args.days_of_cover,
            limit=args.limit,
        )
    )


async def _dead_stock(ctx: ToolContext, args: DeadStockArgs) -> dict[str, Any]:
    return _stock_rows(
        await an.dead_stock(
            ctx.session,
            ctx.analytics,
            await resolve_filters(ctx, args),
            days=args.days,
            limit=args.limit,
        )
    )


async def _reorder_suggestions(ctx: ToolContext, args: ReorderArgs) -> dict[str, Any]:
    from app.reorder import group_by_vendor, suggest

    filters = an.Filters()
    if args.category:
        filters = an.Filters(category_ids=await _category_ids(ctx, args.category))

    forecast = await suggest(ctx.session, ctx.analytics, filters)
    groups = group_by_vendor(forecast)
    if args.vendor:
        wanted = args.vendor.strip().lower()
        matched = [g for g in groups if wanted in g.vendor_name.lower()]
        if not matched:
            known = ", ".join(sorted({g.vendor_name for g in groups})) or "none on file"
            raise ToolError(f"No supplier called {args.vendor!r}. Suppliers here are: {known}.")
        groups = matched

    return {
        "as_of": ctx.analytics.today().isoformat(),
        "total_at_cost": str(forecast.total_at_cost),
        "cost_coverage": (
            str(forecast.cost_coverage) if forecast.cost_coverage is not None else None
        ),
        "caveats": forecast.caveats,
        "vendors": [
            {
                "vendor": group.vendor_name,
                "total_at_cost": (
                    str(group.total_at_cost) if group.total_at_cost is not None else None
                ),
                "lines": [
                    {
                        "item": line.label,
                        "sku": line.sku,
                        "on_hand": str(line.on_hand),
                        "order": str(line.suggested_qty),
                        "days_of_cover": (
                            str(line.days_of_cover) if line.days_of_cover is not None else None
                        ),
                        "why": line.explanation,
                        "urgent": line.stocks_out_before_delivery,
                    }
                    for line in group.lines[: args.limit]
                ],
            }
            for group in groups
        ],
    }


async def _busiest_hours(ctx: ToolContext, args: BusiestHoursArgs) -> dict[str, Any]:
    from app.staffing import WEEKDAY_NAMES, busiest_hours

    location_id = None
    if args.location:
        found = ctx.shop.location_named(args.location)
        if found is None:
            known = ", ".join(location.name for location in ctx.shop.locations) or "none"
            raise ToolError(f"No location called {args.location!r}. Locations are: {known}.")
        location_id = found.id

    weekday = [name.lower() for name in WEEKDAY_NAMES].index(args.weekday) if args.weekday else None
    rows = await busiest_hours(
        ctx.session,
        ctx.analytics,
        location_id=location_id,
        weekday=weekday,
        limit=args.limit,
    )
    return {"window": "the last 8 weeks, event days excluded", "hours": rows}


async def _make_chart(ctx: ToolContext, args: MakeChartArgs) -> dict[str, Any]:
    spec = ChartSpec(
        type=ChartType(args.type),
        title=args.title,
        x=args.x,
        y=args.y,
        data=args.data,
        note=args.note,
    )
    try:
        validate(spec, ctx.observed)
    except ChartRejected as exc:
        raise ToolError(str(exc)) from exc

    ctx.charts.append(spec)
    return {"charted": True, "title": spec.title, "rows": len(spec.data)}


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------


def _definition(key: str) -> str:
    return an.definition_of(key).as_prompt()


ALL_TOOLS: tuple[Tool, ...] = (
    Tool(
        name="search_catalog",
        description=(
            "Search this shop's products and uploaded documents (returns policy, FAQ, event "
            "schedule) in the shop's own words. Use it for anything that is not a number: what "
            "a product is, whether the shop stocks something, what a policy says."
        ),
        args=SearchArgs,
        handler=_search_catalog,
    ),
    Tool(
        name="sales_summary",
        description=(
            "Headline sales for a period. Returns gross sales, discounts, refunds, net sales, "
            f"units, orders and average order value. {_definition('net_sales')}"
        ),
        args=SalesArgs,
        handler=_sales_summary,
    ),
    Tool(
        name="sales_over_time",
        description=(
            "Net sales bucketed by day, week or month, for trends and for charting. Buckets are "
            "cut in the shop's own timezone."
        ),
        args=SeriesArgs,
        handler=_sales_over_time,
    ),
    Tool(
        name="top_products",
        description=(
            "Best-selling products in a period, biggest first. Custom-amount sales with no "
            "product attached cannot appear and are reported as a caveat."
        ),
        args=RankedArgs,
        handler=_top_products,
    ),
    Tool(
        name="category_breakdown",
        description=(
            "Sales split by category for a period. Uncategorised products are their own row."
        ),
        args=RankedArgs,
        handler=_category_breakdown,
    ),
    Tool(
        name="location_channel_breakdown",
        description=(
            "Sales split by channel (in store, online, events) or by location. Unlike a product "
            "split, these reconcile exactly with net sales."
        ),
        args=SplitArgs,
        handler=_split,
    ),
    Tool(
        name="low_stock",
        description=(
            "What to reorder: items that have sold recently and are nearly out. "
            f"{_definition('low_stock')} {_definition('days_of_cover')}"
        ),
        args=LowStockArgs,
        handler=_low_stock,
    ),
    Tool(
        name="dead_stock",
        description=f"Money sitting still. {_definition('dead_stock')}",
        args=DeadStockArgs,
        handler=_dead_stock,
    ),
    Tool(
        name="sell_through",
        description=f"How much of what was available actually sold. {_definition('sell_through')}",
        args=RankedArgs,
        handler=_sell_through,
    ),
    Tool(
        name="inventory_value",
        description=f"What is on the shelves right now. {_definition('inventory_value')}",
        args=StockArgs,
        handler=_inventory_value,
    ),
    Tool(
        name="margin_report",
        description=(
            f"{_definition('gross_margin')} Always report the coverage this comes with — it is "
            "the difference between a margin figure and a guess."
        ),
        args=SalesArgs,
        handler=_margin_report,
        requires=("has_costs",),
    ),
    Tool(
        name="customer_stats",
        description=(
            f"{_definition('repeat_rate')} Duplicate customer records are merged before counting."
        ),
        args=SalesArgs,
        handler=_customer_stats,
        requires=("has_customers",),
    ),
    Tool(
        name="reorder_suggestions",
        description=(
            "What to reorder and how many, grouped by supplier, with the reasoning on every "
            "line. Works from the last four weeks of sales, the vendor's lead time and what "
            'is already on an open order. Answers "what should I order from my manga '
            'distributor?". Never places an order.'
        ),
        args=ReorderArgs,
        handler=_reorder_suggestions,
    ),
    Tool(
        name="busiest_hours",
        description=(
            "When the shop is actually busy, by weekday and hour over the last eight weeks in "
            "the shop's own timezone. Event days such as conventions are left out, because "
            "one con weekend would otherwise be the busiest hour of the week."
        ),
        args=BusiestHoursArgs,
        handler=_busiest_hours,
    ),
    Tool(
        name="make_chart",
        description=(
            "Draw a chart from rows a previous tool returned in this turn. Copy the numbers "
            "exactly as they came back: values that did not come from a tool result are "
            "rejected. Use it when a shape or a trend is the answer, not for two numbers."
        ),
        args=MakeChartArgs,
        handler=_make_chart,
    ),
)


def tools_for(shop: ShopContext) -> tuple[Tool, ...]:
    """Only the tools this shop's system can actually support.

    A tool the tenant cannot use is not registered at all, rather than
    registered and failing: a model that can see `customer_stats` will call it,
    and a shop with no customer data would get an apology instead of an answer
    every time.
    """
    return tuple(
        tool
        for tool in ALL_TOOLS
        if all(shop.analytics.has(capability) for capability in tool.requires)
    )
