"""One definition per metric, in plain English.

These strings are not documentation for us — they are shipped. The agent puts
them in its tool descriptions so it knows what it is reporting, and the
dashboard shows them on hover. When the owner's POS report and our number
disagree, this is the text that has to explain why, so every definition names
what it includes *and* what it leaves out.

Where a definition says "excludes tax and tips", that is the choice every POS
makes when it prints "net sales", and matching it matters more than any
argument about which is more correct.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    key: str
    label: str
    # What the number means, written for a shop owner.
    definition: str
    # The arithmetic, for anyone reconciling against their own report.
    formula: str
    # "money", "count", "units", "percent" or "days".
    unit: str
    # Capabilities the tenant's source must declare for this to be computable.
    requires: tuple[str, ...] = ()

    def as_prompt(self) -> str:
        """The form the agent sees inside a tool description."""
        return f"{self.label}: {self.definition} ({self.formula})"


def _metric(
    key: str,
    label: str,
    definition: str,
    formula: str,
    unit: str,
    requires: tuple[str, ...] = (),
) -> MetricDefinition:
    return MetricDefinition(key, label, definition, formula, unit, requires)


# Applies to every sales metric below, and is the first thing to check when a
# number disagrees with the shop's own report.
SALES_SCOPE = (
    "Sales are counted on the day they were rung up, in the shop's own timezone. "
    "Canceled orders are left out entirely; open and completed orders both count."
)

METRICS: dict[str, MetricDefinition] = {
    m.key: m
    for m in (
        _metric(
            "gross_sales",
            "Gross sales",
            "What everything sold for at full price, before any discount, tax or tip. "
            + SALES_SCOPE,
            "sum of quantity x unit price across order lines",
            "money",
        ),
        _metric(
            "discounts",
            "Discounts",
            "Everything taken off the price at the register: sale prices, staff discounts, "
            "bundle deals.",
            "sum of the discount on each order line",
            "money",
        ),
        _metric(
            "refunds",
            "Refunds",
            "Money given back, counted on the day it was refunded rather than the day of the "
            "original sale. A refund in January for a December sale reduces January.",
            "sum of refund amounts in the period",
            "money",
        ),
        _metric(
            "net_sales",
            "Net sales",
            "The headline sales figure, and the one a POS prints: what was sold, less "
            "discounts, less refunds. Tax and tips are excluded because neither is the shop's "
            "money. " + SALES_SCOPE,
            "gross sales - discounts - refunds",
            "money",
        ),
        _metric(
            "units_sold",
            "Units sold",
            "How many individual items went out of the door. A sale of three of the same book "
            "counts as three.",
            "sum of quantity across order lines",
            "units",
        ),
        _metric(
            "order_count",
            "Orders",
            "How many separate transactions there were, however many items each one had.",
            "count of distinct orders",
            "count",
        ),
        _metric(
            "average_order_value",
            "Average order value",
            "What a typical transaction was worth, after discounts but before refunds are "
            "netted off.",
            "(gross sales - discounts) / orders",
            "money",
        ),
        _metric(
            "cogs",
            "Cost of goods sold",
            "What the items sold had cost the shop to buy. Only lines where a cost was "
            "recorded at the time of sale count, and the share of sales that covers is "
            "always reported next to it.",
            "sum of quantity x cost-at-time-of-sale, over lines that have one",
            "money",
            ("has_costs",),
        ),
        _metric(
            "gross_margin",
            "Gross margin",
            "The share of each covered sales dollar left after paying for the goods. Worked "
            "out only over the sales whose cost is known, never over all sales, because "
            "spreading a known margin across unknown-cost items invents a number.",
            "(covered net sales - cost of goods sold) / covered net sales",
            "percent",
            ("has_costs",),
        ),
        _metric(
            "cost_coverage",
            "Cost coverage",
            "The share of sales in the period whose item had a cost recorded. Margin is only "
            "as trustworthy as this number.",
            "sales on lines with a cost / all sales",
            "percent",
            ("has_costs",),
        ),
        _metric(
            "inventory_value",
            "Inventory value",
            "What is sitting on the shelves right now, valued two ways: at what it would sell "
            "for, and at what it cost to buy. Stock levels are as of the last sync, not live.",
            "sum of on-hand x price, and sum of on-hand x cost",
            "money",
        ),
        _metric(
            "sell_through",
            "Sell-through",
            "Of everything that was available to sell in the period, the share that actually "
            "sold. A high number means it moved; a low one means it is still sitting there.",
            "units sold / (units sold + units on hand now)",
            "percent",
        ),
        _metric(
            "days_of_cover",
            "Days of cover",
            "How many days the current stock would last at the recent rate of selling. Items "
            "that have not sold at all in the window get no answer rather than an infinite one.",
            "units on hand / average units sold per day over the last 28 days",
            "days",
        ),
        _metric(
            "low_stock",
            "Low stock",
            "Items about to run out: either at or below the shop's threshold, or with less "
            "than a set number of days of cover at the current rate of selling. Only things "
            "that have actually sold lately count, so nothing on this list is dead stock.",
            "sold in the last 28 days, and (on hand <= threshold or days of cover < limit)",
            "count",
        ),
        _metric(
            "dead_stock",
            "Dead stock",
            "Items still on the shelf that have not sold once in the period given — money "
            "sitting still. Items that have never sold at all are included and flagged as such.",
            "on hand > 0 and no sale in N days",
            "count",
        ),
        _metric(
            "repeat_rate",
            "Repeat rate",
            "Of the customers who bought in this period, the share who had bought before it "
            "too. Sales with no customer attached are not counted either way, and how many "
            "those were is reported alongside.",
            "returning customers / customers who bought in the period",
            "percent",
            ("has_customers",),
        ),
        _metric(
            "new_vs_returning",
            "New vs returning",
            "How the period's identified customers split between people buying for the first "
            "time and people coming back.",
            "customers with no earlier order, against those with one",
            "count",
            ("has_customers",),
        ),
    )
}

# Breakdown dimensions, defined the same way, so a tool description can explain
# what a split actually means.
DIMENSIONS: dict[str, str] = {
    "product": (
        "By product. Custom-amount sales rung up without a product attached cannot belong to "
        "any row and are reported as a caveat instead."
    ),
    "variant": "By individual variant — the specific size, edition or condition that sold.",
    "category": (
        "By category, using the category a product is in today. Products with no category are "
        "grouped as Uncategorised rather than dropped."
    ),
    "channel": "By where the sale happened: in the shop, online, or at an event or pop-up.",
    "location": "By which of the shop's locations rang the sale.",
}


def definition_of(key: str) -> MetricDefinition:
    try:
        return METRICS[key]
    except KeyError:
        raise KeyError(f"no metric {key!r}. Known: {', '.join(sorted(METRICS))}") from None
