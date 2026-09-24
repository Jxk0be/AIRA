"""Panel & Pawn: a comic and board game shop whose entire system is a
spreadsheet somebody exports from the register once a month.

    uv run python -m panelpawn.generate

This is the second fake customer, and it exists to prove the core is genuinely
platform-blind. Nothing about it resembles RegisterOne: no API, no ids, no
cents, no cursors, no customers, no history — just cells, typed by hand and
exported badly.

Every quirk here is one a real shop will hand you. They are listed in
QUIRKS.md, and the generator prints what it actually produced.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

SHOP_NAME = "Panel & Pawn"
TIMEZONE = "America/New_York"
HISTORY_DAYS = 365
TARGET_RECEIPTS = 1200

# Headers exactly as the shop's export writes them — including the one with a
# trailing space, which is the sort of thing that silently breaks a mapping
# keyed on exact strings.
SALES_HEADERS = (
    "Receipt #",
    "Date",
    "Item Desc",
    "Category",
    "Qty Sold",
    "Unit Price",
    "Discount",
    "Sale Amt ",
    "Notes",
)
INVENTORY_HEADERS = ("Item", "On Hand", "Cost", "Retail")

COMIC_SERIES = (
    ("Iron Gutter", 399, 1.9),
    ("The Quiet Siege", 499, 1.4),
    ("Nocturne Mile", 399, 1.6),
    ("Glasshouse Saints", 599, 0.9),
    ("Brass Cathedral", 499, 1.2),
    ("Dead Letter Club", 399, 1.5),
    ("Paper Tigers of Vesper", 699, 0.7),
)
TRADES = (
    ("Iron Gutter Vol. 1", 1999),
    ("Iron Gutter Vol. 2", 1999),
    ("The Quiet Siege: Complete", 2999),
    ("Nocturne Mile Omnibus", 3499),
    ("Dead Letter Club Vol. 1", 1799),
    ("Brass Cathedral Vol. 1", 2499),
)
BOARD_GAMES = (
    ("Harborline", 4999, 1.4),
    ("Rust & Rye", 3999, 1.1),
    ("The Cartographer's Dilemma", 5999, 0.8),
    ("Moth & Moon", 2999, 1.3),
    ("Tidewreck", 6999, 0.6),
    ("Salt Road", 4499, 1.0),
    ("Little Kingdoms", 2499, 1.5),
    ("Ferrous", 7999, 0.5),
    ("Night Market", 3499, 1.2),
    ("Kettle Rock", 5499, 0.7),
)
RPG_BOOKS = (
    ("Hollow Crown RPG Core Rulebook", 4999),
    ("Hollow Crown: Cinders of Vale", 2999),
)
SUPPLIES = (
    ("Comic Bags & Boards, 100ct", 1299, 2.4),
    ("Dice Set, 7pc", 999, 2.0),
    ("Card Sleeves, 100ct", 599, 1.7),
    ("Board Game Sleeves, 100ct", 799, 1.1),
    ("Comic Storage Box", 1899, 0.6),
)

NOTE_TEXTS = (
    "",
    "",
    "",
    "",
    "held at counter",
    "pull list",
    "special order",
    "customer asked about restock",
    "demo copy",
    "birthday gift wrap",
    "price matched",
)

WEEKDAY_WEIGHT = {0: 0.6, 1: 0.7, 2: 0.9, 3: 1.0, 4: 1.5, 5: 1.9, 6: 1.1}
MONTH_WEIGHT = {
    1: 0.85,
    2: 0.9,
    3: 1.0,
    4: 1.0,
    5: 1.05,
    6: 1.1,
    7: 1.15,
    8: 1.0,
    9: 0.95,
    10: 1.1,
    11: 1.4,
    12: 1.9,
}


def money(cents: int) -> str:
    """The way the export writes money: a dollar sign, and thousands commas."""
    return f"${Decimal(cents) / 100:,.2f}"


def sloppy(name: str, rng: random.Random) -> str:
    """The same item, typed slightly differently on a different sheet.

    Nothing here changes which item it is — but any mapping that matches on the
    raw string will decide these are four different products.
    """
    roll = rng.random()
    if roll < 0.12:
        return name.upper()
    if roll < 0.24:
        return f" {name}"
    if roll < 0.34:
        return f"{name} "
    if roll < 0.42:
        return name.replace(" ", "  ", 1)
    if roll < 0.50:
        return name.lower()
    return name


def build_catalog(rng: random.Random) -> list[dict]:
    items: list[dict] = []
    for series, price, popularity in COMIC_SERIES:
        for issue in range(1, rng.randint(6, 14)):
            items.append(
                {
                    "name": f"{series} #{issue}",
                    "category": "Comics",
                    "price": price,
                    "weight": popularity * (0.86 ** (issue - 1)),
                    # Comics come from a distributor with an invoice, so the
                    # cost is sometimes written down and sometimes not.
                    "cost_ratio": 0.55 if rng.random() < 0.55 else None,
                }
            )
    for name, price in TRADES:
        items.append(
            {
                "name": name,
                "category": "Comics",
                "price": price,
                "weight": 0.5,
                "cost_ratio": 0.58 if rng.random() < 0.7 else None,
            }
        )
    for name, price, popularity in BOARD_GAMES:
        items.append(
            {
                "name": name,
                "category": "Board Games",
                "price": price,
                "weight": popularity,
                # Board games were bought at conventions and from three
                # different distributors, and nobody ever wrote the cost down.
                "cost_ratio": None,
            }
        )
    for name, price in RPG_BOOKS:
        items.append(
            {"name": name, "category": "RPG", "price": price, "weight": 0.6, "cost_ratio": 0.60}
        )
    for name, price, popularity in SUPPLIES:
        items.append(
            {
                "name": name,
                "category": "Supplies",
                "price": price,
                "weight": popularity,
                "cost_ratio": 0.50 if rng.random() < 0.6 else None,
            }
        )
    return items


def generate_rows(rng: random.Random, items: list[dict], start: date, end: date) -> list[dict]:
    weights = [item["weight"] for item in items]
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]

    lambdas = {day: WEEKDAY_WEIGHT[day.weekday()] * MONTH_WEIGHT[day.month] for day in days}
    scale = TARGET_RECEIPTS / sum(lambdas.values())

    rows: list[dict] = []
    receipt_number = 4100
    for day in days:
        count = max(0, round(rng.gauss(lambdas[day] * scale, lambdas[day] * scale * 0.35)))
        for _ in range(count):
            receipt_number += 1
            # Most receipts are one item; a good Saturday basket is three.
            lines = rng.choices([1, 2, 3, 4], weights=[62, 24, 10, 4], k=1)[0]
            chosen = rng.choices(items, weights=weights, k=lines)
            seen: set[str] = set()
            for item in chosen:
                if item["name"] in seen:
                    continue
                seen.add(item["name"])
                quantity = rng.choices([1, 2, 3], weights=[86, 11, 3], k=1)[0]
                gross = item["price"] * quantity
                discount = 0
                if rng.random() < 0.07:
                    discount = int(gross * rng.choice([0.1, 0.15, 0.2]))
                rows.append(
                    {
                        # A handful of receipts were rung up before the shop
                        # started numbering them.
                        "receipt": "" if rng.random() < 0.02 else f"R-{receipt_number}",
                        "date": day,
                        "name": item["name"],
                        # The category column is a habit, not a rule.
                        "category": "" if rng.random() < 0.09 else item["category"],
                        "quantity": quantity,
                        "unit_price": item["price"],
                        "discount": discount,
                        "total": gross - discount,
                        "note": rng.choice(NOTE_TEXTS),
                    }
                )
    return rows


def write_sales_sheet(sheet: Worksheet, rows: list[dict], rng: random.Random) -> None:
    sheet.append(list(SALES_HEADERS))
    for index, row in enumerate(rows):
        # Every export this shop has ever produced has blank rows in it.
        if index and rng.random() < 0.012:
            sheet.append([])
        sheet.append(
            [
                row["receipt"],
                # Dates as the two-digit strings a spreadsheet leaves behind.
                f"{row['date'].month}/{row['date'].day}/{row['date']:%y}",
                sloppy(row["name"], rng),
                row["category"],
                # Quantities are sometimes text and sometimes a number, because
                # they were typed by two different people.
                str(row["quantity"]) if rng.random() < 0.3 else row["quantity"],
                money(row["unit_price"]),
                money(row["discount"]) if row["discount"] else "",
                money(row["total"]),
                row["note"],
            ]
        )

    # And every one of them ends with a totals row that is not a sale.
    total = sum(row["total"] for row in rows)
    sheet.append([])
    sheet.append(["", "", "TOTAL", "", sum(r["quantity"] for r in rows), "", "", money(total), ""])


def write_inventory_sheet(
    sheet: Worksheet, items: list[dict], rows: list[dict], rng: random.Random
) -> None:
    sheet.append(list(INVENTORY_HEADERS))
    sold = {item["name"]: 0 for item in items}
    for row in rows:
        sold[row["name"]] += row["quantity"]

    for item in items:
        on_hand = max(0, rng.randint(0, 14) if sold[item["name"]] else rng.randint(1, 6))
        cost = None
        if item["cost_ratio"] is not None:
            cost = int(item["price"] * item["cost_ratio"])
        sheet.append(
            [
                # No SKU column anywhere: the only key between the two sheets is
                # the item name, typed differently on each.
                sloppy(item["name"], rng),
                on_hand,
                money(cost) if cost is not None else "",
                money(item["price"]),
            ]
        )


def build(seed: int, end: date, destination: Path) -> dict:
    rng = random.Random(seed)
    start = end - timedelta(days=HISTORY_DAYS)
    items = build_catalog(rng)
    rows = generate_rows(rng, items, start, end)

    workbook = Workbook()
    workbook.remove(workbook.active)

    years = sorted({row["date"].year for row in rows})
    for year in years:
        # One sheet per year, because that is how the export has always worked.
        sheet = workbook.create_sheet(f"Sales {year}")
        write_sales_sheet(sheet, [r for r in rows if r["date"].year == year], rng)

    write_inventory_sheet(workbook.create_sheet("Inventory"), items, rows, rng)

    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(destination)

    receipts = {row["receipt"] for row in rows if row["receipt"]}
    with_cost = [i for i in items if i["cost_ratio"] is not None]
    gross = sum(r["quantity"] * r["unit_price"] for r in rows)
    discounts = sum(r["discount"] for r in rows)
    return {
        "sheets": [*[f"Sales {y}" for y in years], "Inventory"],
        "items": len(items),
        "rows": len(rows),
        "receipts": len(receipts),
        "range": f"{start} to {end}",
        "gross": gross,
        "discounts": discounts,
        "net": gross - discounts,
        "cost_coverage": len(with_cost) / len(items),
        "board_games_with_cost": sum(
            1 for i in items if i["category"] == "Board Games" and i["cost_ratio"] is not None
        ),
    }


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--end-date", type=date.fromisoformat, default=None)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "export" / "panel_and_pawn.xlsx",
    )
    args = parser.parse_args()

    end = args.end_date or date.today()
    summary = build(args.seed, end, args.out)

    print(f"\n{SHOP_NAME} — spreadsheet export")
    print(f"{args.out}\n")
    print(f"  period            {summary['range']}")
    print(f"  sheets            {', '.join(summary['sheets'])}")
    print(f"  items             {summary['items']:,}")
    print(f"  sale rows         {summary['rows']:,}")
    print(f"  receipts          {summary['receipts']:,}")
    print(f"  gross             ${Decimal(summary['gross']) / 100:,.2f}")
    print(f"  discounts         ${Decimal(summary['discounts']) / 100:,.2f}")
    print(f"  net               ${Decimal(summary['net']) / 100:,.2f}")
    print(
        f"  items with a cost {summary['cost_coverage']:.0%} "
        f"({summary['board_games_with_cost']} of the board games)"
    )
    print("\n  No customers, no SKUs, no ids, no history. See QUIRKS.md.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
