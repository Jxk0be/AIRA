"""What we actually had to work with.

Written after every sync. Two audiences:

* **Analytics**, which reads the metrics to attach caveats to numbers — "margin
  covers 88% of sales; 12% of lines had no cost".
* **The shop owner**, who sees the findings in plain language on the Data & sync
  screen. Missing costs and uncategorised products are usually the biggest wins,
  and fixing them in their own POS makes our product better for free.

Nothing here guesses. A missing cost is reported as missing, never as zero.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.canonical.enums import Severity
from app.db import table_of

# A product with less text than this retrieves badly: there is nothing for the
# embedding to hold on to beyond the name.
THIN_CONTENT_CHARS = 40
STALE_AFTER_DAYS = 7


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    message: str
    count: int = 0
    share: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "count": self.count,
            "share": self.share,
        }


@dataclass
class QualityReport:
    id: uuid.UUID
    generated_at: datetime
    metrics: dict[str, Any]
    findings: list[Finding]

    def owner_summary(self) -> list[str]:
        return [f.message for f in self.findings]


def _share(part: int | None, whole: int | None) -> float | None:
    if not whole:
        return None
    return round((part or 0) / whole, 4)


async def collect_metrics(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    params = {"tenant": str(tenant_id)}

    async def one(sql: str) -> Any:
        return (await session.execute(text(sql), params)).first()

    catalog = await one(
        """
        select
          count(*) filter (where v.deleted_at is null) as variants,
          count(*) filter (where v.deleted_at is null and v.cost is null) as variants_no_cost,
          count(*) filter (where v.deleted_at is null and v.price is null) as variants_no_price
        from variants v where v.tenant_id = :tenant
        """
    )
    products = await one(
        """
        select
          count(*) filter (where deleted_at is null) as products,
          count(*) filter (where deleted_at is null and category_id is null) as uncategorised,
          count(*) filter (
            where deleted_at is null and coalesce(length(description), 0) < :thin
          ) as thin_content
        from products where tenant_id = :tenant
        """.replace(":thin", str(THIN_CONTENT_CHARS))
    )
    sales = await one(
        """
        select
          count(distinct o.id) as orders,
          min(o.placed_at) as first_order_at,
          max(o.placed_at) as last_order_at,
          coalesce(sum(l.quantity * l.unit_price - l.discount), 0) as gross,
          coalesce(sum(l.quantity * l.unit_price - l.discount)
                   filter (where l.variant_id is null), 0) as custom_amount_gross,
          count(*) filter (where l.unit_cost_snapshot is not null) as lines_with_cost,
          count(*) as lines
        from orders o
        join order_lines l on l.order_id = o.id and l.deleted_at is null
        where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
        """
    )
    reconciliation = await one(
        """
        select count(*) from (
          select o.id,
                 o.total - (
                   coalesce((select sum(l.quantity * l.unit_price)
                             from order_lines l
                             where l.order_id = o.id and l.deleted_at is null), 0)
                   - o.discount_total + o.tax_total + o.tip_total) as drift
          from orders o
          where o.tenant_id = :tenant and o.deleted_at is null and o.status <> 'canceled'
        ) d where abs(drift) > 0.01
        """
    )
    stock = await one(
        """
        select
          count(*) filter (where on_hand < 0) as negative,
          count(*) as levels,
          coalesce(sum(on_hand), 0) as units
        from inventory_levels where tenant_id = :tenant and deleted_at is null
        """
    )
    customers = await one(
        """
        select
          count(*) filter (where deleted_at is null) as customers,
          count(*) filter (where deleted_at is null and merged_into_id is not null) as merged
        from customers where tenant_id = :tenant
        """
    )
    refunds = await one(
        "select count(*), coalesce(sum(amount), 0) from refunds "
        "where tenant_id = :tenant and deleted_at is null"
    )

    gross = Decimal(sales.gross or 0)
    custom = Decimal(sales.custom_amount_gross or 0)
    last_order_at = sales.last_order_at
    days_stale = None
    if last_order_at is not None:
        days_stale = (datetime.now(tz=UTC) - last_order_at).days

    return {
        "variants": catalog.variants,
        "variants_missing_cost": catalog.variants_no_cost,
        "variants_missing_cost_share": _share(catalog.variants_no_cost, catalog.variants),
        "variants_missing_price": catalog.variants_no_price,
        "products": products.products,
        "products_uncategorised": products.uncategorised,
        "products_uncategorised_share": _share(products.uncategorised, products.products),
        "products_thin_content": products.thin_content,
        "orders": sales.orders,
        "order_lines": sales.lines,
        "first_order_at": sales.first_order_at.isoformat() if sales.first_order_at else None,
        "last_order_at": last_order_at.isoformat() if last_order_at else None,
        "days_since_last_order": days_stale,
        "gross_sales": float(gross),
        "custom_amount_gross": float(custom),
        "custom_amount_share": float(custom / gross) if gross else None,
        "lines_with_cost": sales.lines_with_cost,
        "cost_coverage_share": _share(sales.lines_with_cost, sales.lines),
        "orders_not_reconciling": reconciliation[0],
        "inventory_levels": stock.levels,
        "negative_stock_levels": stock.negative,
        "units_on_hand": float(stock.units or 0),
        "customers": customers.customers,
        "customers_merged": customers.merged,
        "refunds": refunds[0],
        "refund_total": float(refunds[1] or 0),
    }


def derive_findings(metrics: dict[str, Any]) -> list[Finding]:
    """Plain-language findings, worst first. Every message is written to be
    read by a shop owner, not by us."""
    findings: list[Finding] = []

    missing_cost = metrics["variants_missing_cost"] or 0
    coverage = metrics.get("cost_coverage_share")
    if missing_cost:
        covered = f"{coverage:.0%}" if coverage is not None else "an unknown share"
        findings.append(
            Finding(
                "missing_costs",
                Severity.WARNING if (coverage or 0) > 0.7 else Severity.ERROR,
                f"{missing_cost:,} of {metrics['variants']:,} items have no cost recorded, "
                f"so margin covers {covered} of sales. Adding costs in your POS fixes this.",
                count=missing_cost,
                share=metrics["variants_missing_cost_share"],
            )
        )

    uncategorised = metrics["products_uncategorised"] or 0
    if uncategorised:
        findings.append(
            Finding(
                "uncategorised_products",
                Severity.WARNING,
                f"{uncategorised:,} products have no category, so they are missing from every "
                "category breakdown.",
                count=uncategorised,
                share=metrics["products_uncategorised_share"],
            )
        )

    custom_share = metrics.get("custom_amount_share")
    if custom_share and custom_share > 0.01:
        findings.append(
            Finding(
                "custom_amount_sales",
                Severity.INFO,
                f"{custom_share:.1%} of sales were rung up as a custom amount with no product "
                "attached, so they count toward revenue but not toward any product's numbers.",
                share=round(custom_share, 4),
            )
        )

    drift = metrics["orders_not_reconciling"] or 0
    if drift:
        findings.append(
            Finding(
                "orders_not_reconciling",
                Severity.ERROR,
                f"{drift:,} orders do not add up: their total differs from their line items "
                "plus tax and tips. Sales figures for those orders cannot be trusted.",
                count=drift,
            )
        )

    negative = metrics["negative_stock_levels"] or 0
    if negative:
        findings.append(
            Finding(
                "negative_stock",
                Severity.WARNING,
                f"{negative:,} items show negative stock, which usually means sales were rung "
                "up before a shipment was received.",
                count=negative,
            )
        )

    merged = metrics["customers_merged"] or 0
    if merged:
        findings.append(
            Finding(
                "customers_merged",
                Severity.INFO,
                f"{merged:,} duplicate customer records were matched to an existing customer, "
                "so repeat-customer figures count each person once.",
                count=merged,
            )
        )

    thin = metrics["products_thin_content"] or 0
    if thin:
        findings.append(
            Finding(
                "thin_product_descriptions",
                Severity.INFO,
                f"{thin:,} products have little or no description, so searching for them by "
                "what they are rather than by name works less well.",
                count=thin,
                share=_share(thin, metrics["products"]),
            )
        )

    days = metrics.get("days_since_last_order")
    if days is None:
        findings.append(Finding("no_sales", Severity.ERROR, "No sales have been synced yet."))
    elif days > STALE_AFTER_DAYS:
        findings.append(
            Finding(
                "stale_data",
                Severity.ERROR,
                f"The most recent sale we have is {days} days old. Either the shop has been "
                "quiet or the connection has stopped bringing data in.",
                count=days,
            )
        )

    order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    return sorted(findings, key=lambda f: (order[f.severity], -f.count))


async def build_report(
    session: AsyncSession,
    tenant: t.Tenant,
    integration: t.Integration,
    sync_run_id: uuid.UUID | None = None,
) -> QualityReport:
    metrics = await collect_metrics(session, tenant.id)
    metrics["adapter"] = integration.adapter
    metrics["capabilities"] = integration.capabilities
    findings = derive_findings(metrics)

    report = QualityReport(
        id=uuid.uuid4(),
        generated_at=datetime.now(tz=UTC),
        metrics=metrics,
        findings=findings,
    )
    await session.execute(
        insert(table_of(t.DataQualityReport)).values(
            id=report.id,
            tenant_id=tenant.id,
            sync_run_id=sync_run_id,
            generated_at=report.generated_at,
            metrics=metrics,
            findings=[f.as_dict() for f in findings],
        )
    )
    return report
