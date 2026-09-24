"""The two documents: one to read, one to work with.

The PDF is for the owner and for the file. The workbook is for the bookkeeper,
who is going to sort, filter and add a column, and for whom a PDF of a table is
a worse version of nothing.

Both are built from the stored `figures` dict rather than from live queries, so
a packet downloaded in March is the same packet that was generated on the 1st
of February — a late refund cannot quietly rewrite a closed month.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.analytics.finance import tender_label
from app.reporting import Column, Document, Sheet, money, workbook_bytes


def _d(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _money(value: Any) -> str:
    return money(_d(value))


def _percent(value: Any) -> str:
    number = _d(value)
    return "—" if number is None else f"{number:.1%}"


def packet_pdf(figures: dict[str, Any], *, generated: str) -> bytes:
    """The document an owner files and a bookkeeper skims."""
    sales = figures["sales"]
    doc = Document(
        f"{figures['shop']} — month end",
        f"{figures['period_start']} to {figures['period_end']}",
    )
    doc.field("Generated", generated)

    doc.heading("Sales")
    doc.table(
        [Column("", 300), Column("Amount", 120, "right")],
        [
            ["Gross sales", _money(sales["gross_sales"])],
            ["Discounts", _money(sales["discounts"])],
            ["Refunds", _money(sales["refunds"])],
        ],
        total=["Net sales", _money(sales["net_sales"])],
    )
    doc.table(
        [Column("", 300), Column("", 120, "right")],
        [
            ["Orders", str(sales["orders"])],
            ["Units", str(sales["units"])],
            ["Average order value", _money(sales["average_order_value"])],
        ],
    )

    doc.heading("Tax, tips and takings")
    tax = figures["tax_and_tips"]
    rows = [
        ["Tax collected (as recorded)", _money(tax["tax_collected"])],
        ["Tips", _money(tax["tips"])],
    ]
    doc.table([Column("", 300), Column("Amount", 120, "right")], rows)

    if figures.get("tenders"):
        doc.table(
            [
                Column("Tender", 160),
                Column("Payments", 90, "right"),
                Column("Amount", 110, "right"),
                Column("Tips", 90, "right"),
            ],
            [
                [
                    tender_label(str(line["tender"])),
                    str(line["payments"]),
                    _money(line["amount"]),
                    _money(line["tips"]),
                ]
                for line in figures["tenders"]
            ],
        )

    if figures.get("margin"):
        doc.heading("Cost of goods and margin")
        margin = figures["margin"]
        doc.table(
            [Column("", 300), Column("Amount", 120, "right")],
            [
                ["Sales with a recorded cost", _money(margin["covered_sales"])],
                ["Cost of goods sold", _money(margin["cogs"])],
                ["Gross profit (on covered sales)", _money(margin["gross_profit"])],
                ["Gross margin", _percent(margin["gross_margin"])],
                ["Cost coverage", _percent(margin["cost_coverage"])],
            ],
        )

    doc.heading("Inventory")
    inventory = figures["inventory"]
    inv_rows = []
    for label, key in (("Opening", "opening"), ("Closing", "closing")):
        snapshot = inventory.get(key)
        if snapshot is None:
            inv_rows.append([label, "not available", "", ""])
            continue
        inv_rows.append(
            [
                label,
                str(snapshot["units"]),
                _money(snapshot["at_cost"]),
                _money(snapshot["at_retail"]),
            ]
        )
    doc.table(
        [
            Column("", 150),
            Column("Units", 90, "right"),
            Column("At cost", 110, "right"),
            Column("At retail", 110, "right"),
        ],
        inv_rows,
    )
    dead = figures["dead_stock"]
    doc.text(
        f"Dead and stale stock: {dead['items']} items, {_money(dead['value'])} tied up.",
        size=10,
    )

    for title, key in (
        ("By category", "by_category"),
        ("By channel", "by_channel"),
        ("By location", "by_location"),
    ):
        breakdown: list[dict[str, Any]] = figures.get(key) or []
        if not breakdown:
            continue
        doc.heading(title)
        doc.table(
            [
                Column("", 250),
                Column("Net sales", 110, "right"),
                Column("Units", 80, "right"),
                Column("Share", 80, "right"),
            ],
            [
                [
                    str(row["label"]),
                    _money(row["net_sales"]),
                    str(row["units"]),
                    _percent(row["share"]),
                ]
                for row in breakdown[:25]
            ],
        )

    doc.page_break()
    doc.heading("Top 10 products")
    doc.table(
        [Column("", 300), Column("Net sales", 110, "right"), Column("Units", 80, "right")],
        [
            [str(row["label"]), _money(row["net_sales"]), str(row["units"])]
            for row in figures.get("best_sellers") or []
        ],
    )
    doc.heading("Bottom 10 that still sold")
    doc.table(
        [Column("", 300), Column("Net sales", 110, "right"), Column("Units", 80, "right")],
        [
            [str(row["label"]), _money(row["net_sales"]), str(row["units"])]
            for row in figures.get("worst_sellers") or []
        ],
    )

    checks = figures.get("reconciliation") or []
    if checks:
        doc.heading("Checks")
        doc.table(
            [
                Column("", 240),
                Column("Expected", 110, "right"),
                Column("Found", 110, "right"),
                Column("Gap", 80, "right"),
            ],
            [
                [
                    str(check["name"]),
                    _money(check["right"]),
                    _money(check["left"]),
                    _money(check["gap"]) if not check["ok"] else "ok",
                ]
                for check in checks
            ],
        )

    doc.heading("Data notes")
    for note in figures.get("notes") or []:
        doc.note(f"• {note}")

    doc.footer(f"{figures['shop']} — figures as recorded in your POS")
    return doc.render()


def packet_workbook(figures: dict[str, Any]) -> bytes:
    """One sheet per table, numbers as numbers."""
    sales = figures["sales"]
    tax = figures["tax_and_tips"]
    period = f"{figures['period_start']} to {figures['period_end']}"

    sheets = [
        Sheet(
            name="Summary",
            note=f"{figures['shop']} — {period}. Figures as recorded in the POS.",
            columns=["figure", "amount"],
            rows=[
                ["Gross sales", _d(sales["gross_sales"])],
                ["Discounts", _d(sales["discounts"])],
                ["Refunds", _d(sales["refunds"])],
                ["Net sales", _d(sales["net_sales"])],
                ["Orders", sales["orders"]],
                ["Units", _d(sales["units"])],
                ["Average order value", _d(sales["average_order_value"])],
                ["Tax collected", _d(tax["tax_collected"])],
                ["Tips", _d(tax["tips"])],
            ],
        )
    ]

    if figures.get("margin"):
        margin = figures["margin"]
        sheets.append(
            Sheet(
                name="Margin",
                note="Margin is worked out only over sales with a recorded cost.",
                columns=["figure", "amount"],
                rows=[
                    ["Sales with a recorded cost", _d(margin["covered_sales"])],
                    ["Cost of goods sold", _d(margin["cogs"])],
                    ["Gross profit", _d(margin["gross_profit"])],
                    ["Gross margin", _d(margin["gross_margin"])],
                    ["Cost coverage", _d(margin["cost_coverage"])],
                ],
            )
        )

    if figures.get("tenders"):
        sheets.append(
            Sheet(
                name="Payments",
                note="Amounts exclude tips, which are shown separately.",
                columns=["tender", "payments", "amount", "tips"],
                rows=[
                    [
                        tender_label(str(line["tender"])),
                        line["payments"],
                        _d(line["amount"]),
                        _d(line["tips"]),
                    ]
                    for line in figures["tenders"]
                ],
            )
        )

    for name, key in (
        ("By category", "by_category"),
        ("By channel", "by_channel"),
        ("By location", "by_location"),
        ("Top products", "best_sellers"),
        ("Bottom products", "worst_sellers"),
    ):
        rows: list[dict[str, Any]] = figures.get(key) or []
        if not rows:
            continue
        sheets.append(
            Sheet(
                name=name,
                columns=["label", "net_sales", "units"],
                rows=[[str(row["label"]), _d(row["net_sales"]), _d(row["units"])] for row in rows],
            )
        )

    inventory = figures["inventory"]
    sheets.append(
        Sheet(
            name="Inventory",
            columns=["point", "as_of", "units", "at_cost", "at_retail", "note"],
            rows=[
                [
                    label,
                    snapshot["at"] if snapshot else None,
                    _d(snapshot["units"]) if snapshot else None,
                    _d(snapshot["at_cost"]) if snapshot else None,
                    _d(snapshot["at_retail"]) if snapshot else None,
                    (snapshot or {}).get("note") or "not available",
                ]
                for label, snapshot in (
                    ("Opening", inventory.get("opening")),
                    ("Closing", inventory.get("closing")),
                )
            ],
        )
    )

    sheets.append(
        Sheet(
            name="Data notes",
            note="Read these before using any figure above.",
            columns=["note"],
            rows=[[note] for note in figures.get("notes") or []],
        )
    )

    return workbook_bytes(sheets, title=f"{figures['shop']} {period}")
