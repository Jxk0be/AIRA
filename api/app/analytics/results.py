"""What a metric hands back.

Typed, and always with room for a caveat. A number on its own is a claim that
nothing qualified it, and in a small shop's data something almost always does:
ten percent of the items have no cost, four percent of sales were rung up as a
custom amount, the sync last brought data in on Tuesday.

Caveats carry a code as well as a sentence. The sentence is what the agent says
and what the dashboard prints under a widget; the code is what tests and the UI
match on, so rewording a message never breaks either.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

CENTS = Decimal("0.01")
RATE = Decimal("0.0001")


def money(value: Decimal | int | float | None) -> Decimal:
    """Round to the cent, half up, the way a till does."""
    if value is None:
        return Decimal("0.00")
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def units(value: Decimal | int | float | None) -> Decimal:
    """Quantities stay fractional: shops really do sell 0.5 kg of something.

    `normalize()` is what strips the trailing zeros off "12.000", and it is
    also what turns ten into `1E+1`. Re-reading it in plain notation costs one
    more parse and means nothing downstream — a JSON payload, a chart, a
    tooltip, a tool result handed to the model — ever has to know that.
    """
    if value is None:
        return Decimal("0")
    return Decimal(format(Decimal(value).normalize(), "f"))


def share(
    part: Decimal | int | float | None, whole: Decimal | int | float | None
) -> Decimal | None:
    """A 0-1 ratio, or None when there is nothing to divide by.

    None rather than 0: "no sales, so no margin" and "sales with zero margin"
    are different answers and must not print the same.
    """
    if not whole:
        return None
    return (Decimal(part or 0) / Decimal(whole)).quantize(RATE, rounding=ROUND_HALF_UP)


class Dimension(StrEnum):
    """What a sales breakdown is cut by."""

    PRODUCT = "product"
    VARIANT = "variant"
    CATEGORY = "category"
    CHANNEL = "channel"
    LOCATION = "location"
    # Which system the sale came out of. The only dimension that is about us
    # rather than about the shop's merchandise, and the one neither Square nor
    # Shopify can offer: it only means anything when a shop's tills are not all
    # the same brand.
    SOURCE = "source"


class Grain(StrEnum):
    """Bucket size for a time series, cut in the shop's timezone."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class Caveat(BaseModel):
    """Something the reader has to know for the number to mean what it says."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str


class Result(BaseModel):
    """Common shape: every result says what window and shop it is for."""

    model_config = ConfigDict(extra="forbid")

    tenant: str
    currency: str
    timezone: str
    period_start: date
    period_end: date
    caveats: list[Caveat] = []

    @property
    def caveat_codes(self) -> set[str]:
        return {c.code for c in self.caveats}


class SalesSummary(Result):
    gross_sales: Decimal
    discounts: Decimal
    refunds: Decimal
    net_sales: Decimal
    units_sold: Decimal
    order_count: int
    average_order_value: Decimal


class SeriesPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The first shop-local day of the bucket.
    bucket: date
    label: str
    gross_sales: Decimal
    discounts: Decimal
    refunds: Decimal
    net_sales: Decimal
    units_sold: Decimal
    order_count: int


class SalesSeries(Result):
    grain: Grain
    points: list[SeriesPoint]


class BreakdownRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # None where the dimension genuinely has no value, e.g. an uncategorised
    # product. Never a stand-in for "everything else".
    key: str | None
    label: str
    gross_sales: Decimal
    discounts: Decimal
    net_sales: Decimal
    units_sold: Decimal
    order_count: int
    share_of_net_sales: Decimal | None


class Breakdown(Result):
    dimension: Dimension
    rows: list[BreakdownRow]
    # Net sales across every row, including any the `limit` cut off.
    total_net_sales: Decimal
    truncated: bool = False


class MarginReport(Result):
    # Sales after discounts, before refunds — refunds cannot be tied back to
    # the item that came back, so they are left out of both sides here.
    sales: Decimal
    # Of that, the part on lines that had a cost. This is the denominator of
    # the margin, and it is not `sales` unless coverage is 100%.
    covered_sales: Decimal
    cogs: Decimal
    gross_profit: Decimal
    gross_margin: Decimal | None
    cost_coverage: Decimal | None


class InventoryValue(Result):
    variants_counted: int
    units_on_hand: Decimal
    retail_value: Decimal
    # None when the shop's system records no costs at all.
    cost_value: Decimal | None
    cost_coverage: Decimal | None
    as_of: datetime | None


class SellThroughRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: uuid.UUID
    sku: str | None
    label: str
    units_sold: Decimal
    units_on_hand: Decimal
    sell_through: Decimal | None


class SellThrough(Result):
    units_sold: Decimal
    units_on_hand: Decimal
    sell_through: Decimal | None
    rows: list[SellThroughRow]


class StockRow(BaseModel):
    """A line in a low-stock, dead-stock or days-of-cover list."""

    model_config = ConfigDict(extra="forbid")

    variant_id: uuid.UUID
    product_name: str
    variant_name: str | None
    sku: str | None
    category: str | None
    units_on_hand: Decimal
    price: Decimal | None
    cost: Decimal | None
    retail_value: Decimal
    # Average units per day over the velocity window. Zero means it did not sell.
    daily_units: Decimal
    # None when nothing sold in the window: no rate, so no honest projection.
    days_of_cover: Decimal | None
    last_sold_at: datetime | None
    days_since_last_sale: int | None
    reason: str | None = None


class StockList(Result):
    rows: list[StockRow]
    row_count: int
    truncated: bool = False
    # Only the rows returned, so the agent can say "that is $412 sitting still".
    retail_value: Decimal


class CustomerStats(Result):
    customers_in_period: int
    new_customers: int
    returning_customers: int
    repeat_rate: Decimal | None
    orders_with_customer: int
    orders_total: int
    # Share of the period's orders that had anyone attached at all.
    identified_share: Decimal | None
