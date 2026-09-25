"""The semantic layer.

Every number this product shows comes from here — the dashboard endpoints and
the agent's tools are both thin wrappers over these functions, so "net sales"
means one thing across the whole product.

This package reads the canonical schema and nothing else. It does not know
which POS a tenant runs, and it must never import from `app.connectors`
(CLAUDE.md rule 1). What a source can and cannot provide reaches it as
capabilities on the tenant's context, which is canonical data.

    ctx = await load_context(session, "animanga_knox")
    summary = await sales_summary(session, ctx, ctx.last_days(30))
"""

from app.analytics.catalog import (
    CatalogPage,
    CatalogRow,
    CatalogSort,
    StockAtLocation,
    catalog_page,
)
from app.analytics.context import (
    AnalyticsContext,
    AnalyticsError,
    CapabilityUnavailable,
    DateRange,
    TenantNotFound,
    load_context,
)
from app.analytics.definitions import DIMENSIONS, METRICS, MetricDefinition, definition_of
from app.analytics.metrics import (
    breakdown,
    category_breakdown,
    channel_breakdown,
    customer_stats,
    days_of_cover,
    dead_stock,
    inventory_value,
    location_breakdown,
    low_stock,
    margin_report,
    sales_series,
    sales_summary,
    sell_through,
    top_products,
)
from app.analytics.queries import Filters
from app.analytics.results import (
    Breakdown,
    BreakdownRow,
    Caveat,
    CustomerStats,
    Dimension,
    Grain,
    InventoryValue,
    MarginReport,
    Result,
    SalesSeries,
    SalesSummary,
    SellThrough,
    SeriesPoint,
    StockList,
    StockRow,
)

__all__ = [
    "DIMENSIONS",
    "METRICS",
    "AnalyticsContext",
    "AnalyticsError",
    "Breakdown",
    "BreakdownRow",
    "CapabilityUnavailable",
    "CatalogPage",
    "CatalogRow",
    "CatalogSort",
    "Caveat",
    "CustomerStats",
    "DateRange",
    "Dimension",
    "Filters",
    "Grain",
    "InventoryValue",
    "MarginReport",
    "MetricDefinition",
    "Result",
    "SalesSeries",
    "SalesSummary",
    "SellThrough",
    "SeriesPoint",
    "StockAtLocation",
    "StockList",
    "StockRow",
    "TenantNotFound",
    "breakdown",
    "catalog_page",
    "category_breakdown",
    "channel_breakdown",
    "customer_stats",
    "days_of_cover",
    "dead_stock",
    "definition_of",
    "inventory_value",
    "load_context",
    "location_breakdown",
    "low_stock",
    "margin_report",
    "sales_series",
    "sales_summary",
    "sell_through",
    "top_products",
]
