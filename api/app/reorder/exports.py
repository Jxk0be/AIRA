"""A draft purchase order, in the forms it has to leave the app in.

Three of them, and the third is the interesting one. "Email to vendor" does not
email the vendor: it produces a subject and a body and hands them to the
owner's own mail client through a `mailto:` link. The shop stays the sender,
which is both the correct business relationship and the reason no code here
could ever place an order by itself (CLAUDE.md rule 2 in spirit as well as
letter).
"""

from __future__ import annotations

from urllib.parse import quote

from app.analytics import AnalyticsContext
from app.reorder.service import PurchaseOrderView
from app.reporting import Column, Document, Sheet, csv_bytes, money, quantity


def purchase_order_pdf(ctx: AnalyticsContext, order: PurchaseOrderView) -> bytes:
    """The document a distributor would accept."""
    doc = Document(f"Purchase order {order.reference}", ctx.name)
    doc.field("Vendor", order.vendor_name)
    if order.vendor_email:
        doc.field("Vendor email", order.vendor_email)
    doc.field("Raised", order.created_at.astimezone(ctx.tz).strftime("%d %b %Y"))
    if order.expected_at:
        doc.field("Expected", order.expected_at.strftime("%d %b %Y"))
    doc.field("Status", order.status)

    doc.heading("Items")
    doc.table(
        [
            Column("Item", 250),
            Column("SKU", 90),
            Column("On hand", 55, "right"),
            Column("Qty", 45, "right"),
            Column("Unit cost", 60, "right"),
            Column("Line", 60, "right"),
        ],
        [
            [
                line.name,
                line.sku or "",
                quantity(line.on_hand_at_draft),
                quantity(line.quantity),
                money(line.unit_cost),
                money(line.line_cost),
            ]
            for line in order.lines
        ],
        total=[
            "Total",
            "",
            "",
            quantity(order.units),
            "",
            money(order.total_at_cost),
        ],
    )

    if order.unpriced_lines:
        doc.note(
            f"{order.unpriced_lines} of {len(order.lines)} lines have no cost on file, so the "
            "total above is only the part we can price."
        )
    doc.note("Quantities suggested from the last four weeks of sales and the vendor's lead time.")
    if order.note:
        doc.heading("Note")
        doc.note(order.note)
    doc.footer(f"{ctx.name} — {order.reference}")
    return doc.render()


def purchase_order_csv(order: PurchaseOrderView) -> bytes:
    """The same order, for a vendor portal or a spreadsheet."""
    return csv_bytes(
        Sheet(
            name=order.reference,
            note=f"{order.reference} — {order.vendor_name}",
            columns=[
                "sku",
                "item",
                "quantity",
                "unit_cost",
                "line_cost",
                "on_hand_at_draft",
                "suggested_qty",
                "why",
            ],
            rows=[
                [
                    line.sku,
                    line.name,
                    line.quantity,
                    line.unit_cost,
                    line.line_cost,
                    line.on_hand_at_draft,
                    line.suggested_qty,
                    line.explanation,
                ]
                for line in order.lines
            ],
        )
    )


def vendor_email(ctx: AnalyticsContext, order: PurchaseOrderView) -> tuple[str, str]:
    """A subject and a body for the owner to send. Returns (subject, body)."""
    subject = f"Purchase order {order.reference} — {ctx.name}"
    lines = [
        "Hello,",
        "",
        f"Please could we order the following for {ctx.name}:",
        "",
    ]
    for line in order.lines:
        sku = f" ({line.sku})" if line.sku else ""
        lines.append(f"  {quantity(line.quantity)} x {line.name}{sku}")
    lines += ["", f"Reference: {order.reference}"]
    if order.total_at_cost is not None and not order.unpriced_lines:
        lines.append(f"Total at our recorded cost: {money(order.total_at_cost)}")
    lines += ["", "Thank you,", ctx.name]
    return subject, "\n".join(lines)


def mailto_link(ctx: AnalyticsContext, order: PurchaseOrderView) -> str:
    """A `mailto:` the app can open. The owner presses send, not us."""
    subject, body = vendor_email(ctx, order)
    to = order.vendor_email or ""
    return f"mailto:{quote(to)}?subject={quote(subject)}&body={quote(body)}"


def totals(order: PurchaseOrderView) -> dict[str, str | int | None]:
    """The figures a screen shows above the table."""
    total = order.total_at_cost
    return {
        "lines": len(order.lines),
        "units": quantity(order.units),
        "total_at_cost": format(total, "f") if total is not None else None,
        "unpriced_lines": order.unpriced_lines,
    }


__all__ = [
    "mailto_link",
    "purchase_order_csv",
    "purchase_order_pdf",
    "totals",
    "vendor_email",
]
