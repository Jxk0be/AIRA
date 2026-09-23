"""Fill RegisterOne with eighteen months of Tsundoku & Tabletop history.

    uv run python -m registerone.seed --reset

Deterministic for a given (--seed, --end-date). The default end date is today,
so the fixture always has fresh data for "last 30 days" questions; pass
--end-date for byte-identical reruns.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb

from registerone.catalog import CATEGORY_RENAMED, SHOP_NAME
from registerone.db import dsn
from registerone.generate import Dataset, generate

TABLES_IN_INSERT_ORDER = (
    "locations",
    "categories",
    "vendors",
    "catalog_items",
    "item_variations",
    "variation_vendor_info",
    "customers",
    "orders",
    "order_line_items",
    "payments",
    "refunds",
    "inventory_adjustments",
    "inventory_counts",
)

COLUMNS: dict[str, tuple[str, ...]] = {
    "locations": ("id", "name", "timezone", "status"),
    "categories": ("id", "name", "parent_id", "updated_at"),
    "vendors": ("id", "name", "account_number"),
    "catalog_items": (
        "id",
        "name",
        "description",
        "category_id",
        "product_type",
        "is_deleted",
        "custom_attributes",
        "created_at",
        "updated_at",
    ),
    "item_variations": (
        "id",
        "item_id",
        "name",
        "sku",
        "upc",
        "price_amount",
        "currency",
        "pricing_type",
        "track_inventory",
        "is_deleted",
        "updated_at",
    ),
    "variation_vendor_info": ("variation_id", "vendor_id", "unit_cost_amount"),
    "customers": (
        "id",
        "given_name",
        "family_name",
        "email",
        "phone",
        "reference_id",
        "created_at",
        "updated_at",
    ),
    "orders": (
        "id",
        "location_id",
        "customer_id",
        "state",
        "source",
        "total_money",
        "total_tax_money",
        "total_discount_money",
        "total_tip_money",
        "created_at",
        "updated_at",
        "closed_at",
        "version",
    ),
    "order_line_items": (
        "uid",
        "order_id",
        "catalog_object_id",
        "name",
        "variation_name",
        "quantity",
        "base_price_money",
        "total_discount_money",
        "gross_sales_money",
        "total_money",
        "note",
    ),
    "payments": (
        "id",
        "order_id",
        "amount_money",
        "tip_money",
        "source_type",
        "card_brand",
        "status",
        "created_at",
    ),
    "refunds": (
        "id",
        "payment_id",
        "order_id",
        "amount_money",
        "reason",
        "status",
        "created_at",
    ),
    "inventory_adjustments": (
        "id",
        "variation_id",
        "location_id",
        "from_state",
        "to_state",
        "quantity",
        "occurred_at",
        "reason",
    ),
    "inventory_counts": (
        "variation_id",
        "location_id",
        "state",
        "quantity",
        "calculated_at",
    ),
}

DATASET_ATTRIBUTE: dict[str, str] = {
    "locations": "locations",
    "categories": "categories",
    "vendors": "vendors",
    "catalog_items": "items",
    "item_variations": "variations",
    "variation_vendor_info": "vendor_info",
    "customers": "customers",
    "orders": "orders",
    "order_line_items": "line_items",
    "payments": "payments",
    "refunds": "refunds",
    "inventory_adjustments": "adjustments",
    "inventory_counts": "counts",
}


def money(cents: int) -> str:
    return f"${Decimal(cents) / 100:,.2f}"


def write(connection: psycopg.Connection, data: Dataset, *, reset: bool) -> None:
    with connection.cursor() as cursor:
        if reset:
            cursor.execute(
                "truncate " + ", ".join(reversed(TABLES_IN_INSERT_ORDER)) + " restart identity cascade"
            )
        for table in TABLES_IN_INSERT_ORDER:
            columns = COLUMNS[table]
            rows = getattr(data, DATASET_ATTRIBUTE[table])
            if not rows:
                continue
            placeholders = ", ".join(["%s"] * len(columns))
            statement = (
                f"insert into {table} ({', '.join(columns)}) values ({placeholders}) on conflict do nothing"
            )
            cursor.executemany(
                statement,
                [
                    tuple(Jsonb(row[c]) if isinstance(row[c], dict) else row[c] for c in columns)
                    for row in rows
                ],
            )
    connection.commit()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def summarise(connection: psycopg.Connection, data: Dataset) -> None:
    with connection.cursor() as cursor:
        print(f"\n{SHOP_NAME} — RegisterOne fixture")
        print(f"{data.start_date} to {data.end_date}\n")

        for table in TABLES_IN_INSERT_ORDER:
            cursor.execute(f"select count(*) from {table}")
            print(f"  {table:24} {cursor.fetchone()[0]:>7,}")

        # Net sales, the way a POS reports it: gross, less discounts, less
        # refunds, excluding tax and tips. Canceled orders do not count.
        print("\n  Net sales by month (shop local time)")
        cursor.execute(
            """
            with sold as (
                select date_trunc('month', o.created_at at time zone 'America/New_York') as month,
                       sum(l.gross_sales_money) as gross,
                       sum(l.total_discount_money) as discounts
                from orders o join order_line_items l on l.order_id = o.id
                where o.state <> 'CANCELED'
                group by 1
            ),
            refunded as (
                select date_trunc('month', r.created_at at time zone 'America/New_York') as month,
                       sum(r.amount_money) as refunds
                from refunds r group by 1
            )
            select to_char(s.month, 'YYYY-MM'),
                   s.gross, s.discounts, coalesce(f.refunds, 0),
                   s.gross - s.discounts - coalesce(f.refunds, 0)
            from sold s left join refunded f on f.month = s.month
            order by 1
            """
        )
        rows = cursor.fetchall()
        peak = max(rows, key=lambda r: r[4])
        for label, _gross, _discounts, _refunds, net in rows:
            bar = "#" * max(1, round(net / peak[4] * 32))
            marker = "  <- peak" if label == peak[0] else ""
            print(f"    {label}  {money(net):>12}  {bar}{marker}")

        print("\n  Top 10 items by net sales")
        cursor.execute(
            """
            select coalesce(i.name, l.name) as item,
                   sum(l.gross_sales_money - l.total_discount_money) as net,
                   sum(l.quantity::numeric) as units
            from order_line_items l
            join orders o on o.id = l.order_id and o.state <> 'CANCELED'
            left join item_variations v on v.id = l.catalog_object_id
            left join catalog_items i on i.id = v.item_id
            group by 1 order by 2 desc limit 10
            """
        )
        for item, net, units in cursor.fetchall():
            print(f"    {money(net):>11}  {units:>6.0f} u   {item}")

        print("\n  TCG release weeks (net sales that week vs the four weeks before)")
        for release in data.notes["release_weeks"]:
            cursor.execute(
                """
                with net as (
                    select o.created_at at time zone 'America/New_York' as local_at,
                           l.gross_sales_money - l.total_discount_money as amount
                    from orders o join order_line_items l on l.order_id = o.id
                    where o.state <> 'CANCELED'
                )
                select
                  coalesce(sum(amount) filter (
                      where local_at >= %(day)s::timestamp
                        and local_at < %(day)s::timestamp + interval '7 days'), 0),
                  coalesce(sum(amount) filter (
                      where local_at >= %(day)s::timestamp - interval '28 days'
                        and local_at < %(day)s::timestamp), 0) / 4
                from net
                """,
                {"day": release},
            )
            week, baseline = cursor.fetchone()
            lift = (week / baseline - 1) if baseline else 0
            print(
                f"    {release}  {money(int(week)):>11}  vs {money(int(baseline)):>11} typical   {lift:+.0%}"
            )

        print("\n  Convention weekends (both locations, net sales per trading day)")
        cursor.execute(
            """
            with by_day as (
                select (o.created_at at time zone 'America/New_York')::date as day,
                       sum(l.gross_sales_money - l.total_discount_money) as amount
                from orders o join order_line_items l on l.order_id = o.id
                where o.state <> 'CANCELED' group by 1
            )
            select
              avg(amount) filter (where day = any(%(con)s::date[])),
              avg(amount) filter (
                  where day <> all(%(con)s::date[]) and extract(dow from day) in (0, 6)),
              count(*) filter (where day = any(%(con)s::date[]))
            from by_day
            """,
            {"con": data.notes["con_weekends"]},
        )
        con_avg, normal_avg, con_days = cursor.fetchone()
        lift = (con_avg / normal_avg - 1) if normal_avg else 0
        print(
            f"    {money(int(con_avg or 0))} on a booth day vs "
            f"{money(int(normal_avg or 0))} on a normal weekend day   {lift:+.0%}"
            f"  ({con_days} booth days)"
        )

        print("\n  Location and channel")
        cursor.execute(
            """
            select loc.name, o.source, count(*), sum(o.total_money - o.total_tax_money - o.total_tip_money)
            from orders o join locations loc on loc.id = o.location_id
            where o.state <> 'CANCELED'
            group by 1, 2 order by 4 desc
            """
        )
        for name, source, count, net in cursor.fetchall():
            print(f"    {name:34} {source:7} {count:>5,} orders  {money(net):>12}")

        cursor.execute(
            """
            select count(*) filter (where orders >= 2)::numeric / nullif(count(*), 0),
                   count(*) filter (where orders >= 2), count(*)
            from (
                select customer_id, count(*) as orders from orders
                where customer_id is not null and state <> 'CANCELED'
                group by 1
            ) per_customer
            """
        )
        rate, repeat, total = cursor.fetchone()
        print(f"\n  Repeat-customer rate  {rate:.1%}  ({repeat:,} of {total:,} identified customers)")

        cursor.execute(
            """
            select
              sum(c.quantity::numeric * v.price_amount) filter (where v.price_amount is not null),
              sum(c.quantity::numeric * vi.unit_cost_amount) filter (where vi.unit_cost_amount is not null),
              sum(c.quantity::numeric) filter (where vi.unit_cost_amount is not null)
                / nullif(sum(c.quantity::numeric), 0)
            from inventory_counts c
            join item_variations v on v.id = c.variation_id
            left join variation_vendor_info vi on vi.variation_id = c.variation_id
            where c.state = 'IN_STOCK'
            """
        )
        retail, at_cost, coverage = cursor.fetchone()
        print(
            f"  Stock value           {money(int(retail or 0))} at retail, "
            f"{money(int(at_cost or 0))} at cost ({coverage or 0:.0%} of units have a cost)"
        )


def quirk_report(connection: psycopg.Connection) -> None:
    """What the run actually produced, against what QUIRKS.md promises."""
    checks: list[tuple[str, str, str]] = [
        (
            "Orders with no customer",
            "0.60",
            "select count(*) filter (where customer_id is null)::numeric / count(*) from orders",
        ),
        (
            "Line items with no catalog object",
            "0.04",
            """select count(*) filter (where catalog_object_id is null)::numeric / count(*)
            from order_line_items""",
        ),
        ("Items soft-deleted", "8 items", "select count(*) from catalog_items where is_deleted"),
        (
            "...still referenced by old orders",
            ">0",
            """select count(distinct l.order_id) from order_line_items l
            join item_variations v on v.id = l.catalog_object_id
            join catalog_items i on i.id = v.item_id where i.is_deleted""",
        ),
        (
            "Duplicate customers (shared email)",
            "0.05",
            """select count(*)::numeric / (select count(*) from customers) from (
              select email from customers where email is not null
              group by email having count(*) > 1) d""",
        ),
        (
            "Orders refunded",
            "0.02",
            "select (select count(distinct order_id) from refunds)::numeric / count(*) from orders",
        ),
        (
            "Orders canceled",
            "0.01",
            "select count(*) filter (where state = 'CANCELED')::numeric / count(*) from orders",
        ),
        (
            "Orders with split payments",
            "0.03",
            """select count(*)::numeric / (select count(*) from orders where state <> 'CANCELED') from (
              select order_id from payments group by order_id having count(*) > 1) s""",
        ),
        (
            "Variations with no unit cost",
            "0.10",
            """select count(*) filter (where vi.unit_cost_amount is null)::numeric / count(*)
            from item_variations v left join variation_vendor_info vi on vi.variation_id = v.id""",
        ),
        (
            "Variations priced at the register",
            "5",
            "select count(*) from item_variations where pricing_type = 'VARIABLE'",
        ),
        (
            "Variations low on stock (<= 3)",
            "0.08",
            """select count(*) filter (where quantity::numeric between 0 and 3)::numeric / count(*)
            from inventory_counts where state = 'IN_STOCK'""",
        ),
        (
            "Variations never sold",
            "0.05",
            """select count(*)::numeric / (select count(*) from item_variations) from item_variations v
            where not exists (select 1 from order_line_items l where l.catalog_object_id = v.id)""",
        ),
    ]

    print("\n  Deliberate mess (see QUIRKS.md)")
    with connection.cursor() as cursor:
        for label, target, sql in checks:
            cursor.execute(sql)
            value = cursor.fetchone()[0] or 0
            shown = f"{float(value):.1%}" if 0 < float(value) < 1 else f"{value:,.0f}"
            print(f"    {label:38} {shown:>8}   (target {target})")

        cursor.execute("select name, to_char(updated_at, 'YYYY-MM-DD') from categories where id = 'CAT_TCG'")
        name, renamed_on = cursor.fetchone()
        print(
            f"    {'Category renamed mid-history':38} {name!r} since {renamed_on}, "
            f"was {CATEGORY_RENAMED['old_name']!r}"
        )


def check_invariants(connection: psycopg.Connection) -> bool:
    """Things that must be true, or the fixture is lying to our tests."""
    problems: list[str] = []
    with connection.cursor() as cursor:
        cursor.execute(
            """
            select count(*) from (
                select c.variation_id, c.location_id, c.quantity::numeric as counted,
                       coalesce(sum(case when a.to_state = 'IN_STOCK' then a.quantity::numeric
                                         else -a.quantity::numeric end), 0) as summed
                from inventory_counts c
                left join inventory_adjustments a
                  on a.variation_id = c.variation_id and a.location_id = c.location_id
                where c.state = 'IN_STOCK'
                group by 1, 2, 3
            ) t where counted <> summed
            """
        )
        if bad := cursor.fetchone()[0]:
            problems.append(f"{bad} inventory counts do not equal the sum of their history")

        cursor.execute(
            """
            select count(*) from orders o
            where o.state <> 'CANCELED' and abs(
                o.total_money - (
                    coalesce((select sum(l.gross_sales_money - l.total_discount_money)
                              from order_line_items l where l.order_id = o.id), 0)
                    + o.total_tax_money + o.total_tip_money)) > 1
            """
        )
        if bad := cursor.fetchone()[0]:
            problems.append(f"{bad} orders do not reconcile with their line items")

        cursor.execute(
            """
            select count(*) from (
                select p.id, p.amount_money, coalesce(sum(r.amount_money), 0) as refunded
                from payments p left join refunds r on r.payment_id = p.id
                group by 1, 2
            ) t where refunded > amount_money
            """
        )
        if bad := cursor.fetchone()[0]:
            problems.append(f"{bad} payments were refunded for more than they took")

        cursor.execute(
            "select count(*) from inventory_counts where state = 'IN_STOCK' and quantity::numeric < 0"
        )
        if bad := cursor.fetchone()[0]:
            problems.append(f"{bad} variations have negative stock")

    print("\n  Invariants")
    if problems:
        for problem in problems:
            print(f"    FAIL  {problem}")
        return False
    print("    ok    counts reconcile with history, orders reconcile with lines,")
    print("          refunds never exceed payments, no negative stock")
    return True


def main() -> int:
    # The shop name has an ampersand and an em dash in it; a Windows console
    # defaulting to cp1252 would mangle both.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--end-date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="last day of history (default: today, UTC)",
    )
    parser.add_argument("--reset", action="store_true", help="truncate before inserting")
    parser.add_argument("--dsn", default=dsn())
    parser.add_argument("--json-notes", action="store_true", help="dump generator notes as JSON")
    args = parser.parse_args()

    data = generate(seed=args.seed, end_date=args.end_date)

    started = datetime.now()
    with psycopg.connect(args.dsn) as connection:
        write(connection, data, reset=args.reset)
        elapsed = (datetime.now() - started).total_seconds()
        summarise(connection, data)
        quirk_report(connection)
        ok = check_invariants(connection)

    if args.json_notes:
        print(json.dumps(data.notes, indent=2))
    print(f"\n  Seeded in {elapsed:.1f}s. Mock API: http://localhost:8100/v2/locations\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
