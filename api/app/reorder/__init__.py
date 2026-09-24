"""The reorder assistant: what to buy, how many, and why.

A Sunday-night chore turned into five minutes. Three parts, kept apart on
purpose:

* `app.analytics.demand` measures — units a week, stock on hand, lead times.
* `forecast` judges — velocity, seasonality, safety stock, pack rounding. Pure
  functions, no database, so every rule is testable as arithmetic.
* `service` and `exports` turn the judgement into a draft purchase order the
  owner edits and sends themselves. Nothing in this package can send an order.

    forecast = await suggest(session, ctx)
    groups = group_by_vendor(forecast)
    order_ids = await create_drafts(session, ctx, groups)
"""

from app.reorder.exports import (
    mailto_link,
    purchase_order_csv,
    purchase_order_pdf,
    vendor_email,
)
from app.reorder.forecast import (
    Forecast,
    Suggestion,
    build,
    round_to_pack,
    seasonal_factor,
    velocity_of,
)
from app.reorder.outcomes import measure
from app.reorder.service import (
    UNASSIGNED,
    PurchaseOrderLineView,
    PurchaseOrderView,
    VendorGroup,
    create_drafts,
    get_order,
    group_by_vendor,
    list_orders,
    outstanding_quantities,
    set_order_status,
    suggest,
    update_line,
)
from app.reorder.tables import PurchaseOrder, PurchaseOrderLine

__all__ = [
    "UNASSIGNED",
    "Forecast",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "PurchaseOrderLineView",
    "PurchaseOrderView",
    "Suggestion",
    "VendorGroup",
    "build",
    "create_drafts",
    "get_order",
    "group_by_vendor",
    "list_orders",
    "mailto_link",
    "measure",
    "outstanding_quantities",
    "purchase_order_csv",
    "purchase_order_pdf",
    "round_to_pack",
    "seasonal_factor",
    "set_order_status",
    "suggest",
    "update_line",
    "velocity_of",
    "vendor_email",
]
