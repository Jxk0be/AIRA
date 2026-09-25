"""CardNexus: a fake TCG marketplace, and Animanga Knox's second register.

    uv run python -m cardnexus.generate

Animanga Knox opened an online storefront in July 2026. It does not run on
RegisterOne, it has no API on any plan the shop can afford, and what it gives
them is one spreadsheet a month.

That makes Animanga Knox a **two-source shop**, which is the whole point. Square
AI needs Square and Sidekick needs Shopify; neither will ever add a competitor's
takings to their own. A shop with a till on the floor and a marketplace online has
two dashboards that never meet, and adds them up by hand on the first of the
month. This is the fixture that lets us show the alternative.

It is deliberately *not* a second Panel & Pawn. Panel & Pawn is a badly typed
till export: no ids, no structure, human mess. A marketplace export is the
opposite kind of awkward — machine-clean, perfectly consistent, and missing
things on purpose because the marketplace keeps them:

* **No costs.** CardNexus knows what the shop sold something for, never what the
  shop paid for it. So margin covers only the in-store half of the business, and
  says so.
* **No customers.** There is a buyer handle, and it is pseudonymous and cannot be
  joined to the person who walks into the shop. It is in the file and
  deliberately not mapped, which is what `has_customers: false` means here.
* **No stock.** The shop's shelf is the shop's business; the marketplace only
  reports what left it.
* **No refunds in this file.** They arrive on a separate statement the shop has
  never sent us.
* **Fees and shipping are not merchandise revenue.** Both are columns here, and
  counting either into net sales would overstate the shop's takings. The mapping
  ignores them on purpose.
* **A settlement date, not a sale time.** So hour-of-day questions stay
  answerable for the shop floor and unanswerable online.

Every listing title is in the marketplace's own format — set name, card name,
condition — and matches nothing in RegisterOne's catalog. That is true to life and
it is an honest limit worth showing: revenue consolidates across two systems,
*product* identity does not.

Sized so that December 2025 stays the shop's best month. A storefront a few
months old taking a seventh of what the counter does is realistic, and it keeps every historical
figure in `scripts/evals/golden/animanga_knox.yaml` untouched — those questions
all ask about windows that closed before the storefront opened.
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

SHOP_NAME = "Animanga Knox"
MARKETPLACE = "CardNexus"

# The storefront's opening day. Fixed rather than relative to today, because the
# eval questions all ask about windows that closed before it — see the module
# docstring — and a rolling start date would eventually walk into them.
OPENED = date(2026, 7, 1)

# About two and a half orders a day of mostly cheap singles — around a seventh
# of what the counter takes. Small on purpose: it is a storefront a few months
# old, and it leaves the December spike comfortably ahead as the best month.
ORDERS_PER_DAY = 2.5

# Headers exactly as CardNexus writes them. Machine-generated, so unlike Panel &
# Pawn's export they are consistent — the awkwardness here is what is *absent*,
# not how it is typed.
HEADERS = (
    "Order #",
    "Settlement Date",
    "Listing Title",
    "Set",
    "Category",
    "Condition",
    "Qty",
    "Item Price",
    "Shipping Paid",
    "Marketplace Fee",
    "Buyer Handle",
)

# Listing titles in the marketplace's format. None of these match a RegisterOne
# product name, on purpose: the same card is a different row in each system, and
# pretending otherwise is how you get a consolidated product list that is wrong.
#
# The weight is what keeps the numbers honest. A shop's online sales are mostly
# cheap singles with the occasional chase card, so picking uniformly from this
# list would sell as many $95 sealed boxes as $4 commons and put the storefront
# ahead of the shop floor.
CATALOG: tuple[tuple[str, str, str, int, int, int], ...] = (
    # title, set, category, low cents, high cents, weight
    ("Iron Valiant ex - 089", "Prismatic Evolutions", "Pokemon Singles", 250, 700, 26),
    ("Pikachu VMAX - 044/185", "Journey Together", "Pokemon Singles", 450, 1400, 22),
    ("Nezuko Kamado RRR", "Demon Slayer", "Weiss Schwarz Singles", 400, 1200, 18),
    ("Mikasa Ackerman SR", "Attack on Titan", "Weiss Schwarz Singles", 550, 1500, 16),
    ("Dark Magician (Ultra Rare)", "Legend of Blue Eyes", "Yu-Gi-Oh Singles", 600, 1800, 14),
    ("Levi Ackerman SP", "Attack on Titan", "Weiss Schwarz Singles", 900, 2200, 8),
    ("Charizard ex - 199/165", "Prismatic Evolutions", "Pokemon Singles", 1600, 3800, 5),
    ("Blue-Eyes White Dragon (1st Ed)", "Legend of Blue Eyes", "Yu-Gi-Oh Singles", 1800, 4200, 4),
    # The chase card. Rare on purpose: one of these a month, not one a day.
    ("Umbreon VMAX (Alt Art)", "Destined Rivals", "Pokemon Singles", 6500, 14500, 1),
    ("Booster Bundle - Sealed", "Destined Rivals", "Sealed Boxes", 2400, 3100, 4),
    ("Elite Trainer Box - Sealed", "Journey Together", "Sealed Boxes", 4200, 5400, 2),
    ("Premium Collection - Sealed", "Prismatic Evolutions", "Sealed Boxes", 8500, 11500, 1),
    # Two rows with no set recorded, because the marketplace lets a seller list
    # without one and the shop sometimes does.
    ("Mixed Singles Lot (25 cards)", "", "Pokemon Singles", 900, 2400, 6),
    ("Playmat - Anime Print", "", "Accessories", 1400, 2200, 7),
)

CONDITIONS = ("Near Mint", "Lightly Played", "Moderately Played")

# Marketplace handles. Pseudonymous on purpose — present in the file, never
# mapped, and the reason `has_customers` is false for this source.
HANDLES = (
    "knoxcollector",
    "tcg_vault_88",
    "smokymtn_cards",
    "deckbuilder_jr",
    "volsfan2012",
    "gridiron_gary",
    "animangafan",
    "cardsbykatie",
)

# Marketplace commission. Recorded so the shop can see it, and deliberately not
# treated as anything but a fee.
FEE_RATE = Decimal("0.1025")

# Flat postage the buyer pays. Not merchandise revenue.
SHIPPING_CENTS = (0, 99, 149, 499)


def build_rows(rng: random.Random, start: date, end: date) -> list[dict]:
    """One row per line item, with multi-item orders sharing an order number."""
    rows: list[dict] = []
    order_number = 4_820_117

    day = start
    while day <= end:
        # Weekends run hotter online too, though less sharply than the counter.
        weight = 1.35 if day.weekday() >= 5 else 1.0
        count = _poisson(rng, ORDERS_PER_DAY * weight)

        for _ in range(count):
            order_number += rng.randint(1, 4)
            # Most orders are one card; a collector buying three is common enough
            # to matter for average order value.
            lines = rng.choices((1, 2, 3), weights=(72, 21, 7))[0]
            handle = rng.choice(HANDLES)
            shipping = rng.choice(SHIPPING_CENTS)

            for index, (title, card_set, category, low, high, _weight) in enumerate(
                weighted_sample(rng, lines)
            ):
                price = rng.randint(low, high)
                # Sealed product moves one at a time; singles sometimes in pairs.
                units = 1 if category == "Sealed Boxes" else rng.choices((1, 2), weights=(88, 12))[0]
                rows.append(
                    {
                        "order": f"CN-{order_number}",
                        "date": day,
                        "title": title,
                        "set": card_set,
                        "category": category,
                        "condition": rng.choice(CONDITIONS)
                        if category.endswith("Singles")
                        else "Sealed",
                        "units": units,
                        "price_cents": price,
                        # Postage is charged once per order, on its first line.
                        "shipping_cents": shipping if index == 0 else 0,
                        "handle": handle,
                    }
                )
        day += timedelta(days=1)
    return rows


def weighted_sample(rng: random.Random, k: int) -> list[tuple[str, str, str, int, int, int]]:
    """`k` distinct listings, cheap ones far more often.

    Distinct matters as well as weighted: a line's identity in the mapping is the
    order number plus the folded title, so two lines of the same listing in one
    order would collide and silently become one.
    """
    remaining = list(CATALOG)
    picked: list[tuple[str, str, str, int, int, int]] = []
    for _ in range(min(k, len(remaining))):
        chosen = rng.choices(remaining, weights=[entry[5] for entry in remaining])[0]
        remaining.remove(chosen)
        picked.append(chosen)
    return picked


def _poisson(rng: random.Random, mean: float) -> int:
    """Knuth's method. `random.Random` has no Poisson and importing numpy into a
    fixture generator for one draw would be silly."""
    import math

    limit = math.exp(-mean)
    count, product = 0, rng.random()
    while product > limit:
        count += 1
        product *= rng.random()
    return count


def money(cents: int) -> str:
    """CardNexus writes money as a string with a currency symbol, like every
    marketplace export anybody has ever been handed."""
    return f"${Decimal(cents) / 100:.2f}"


def write_orders_sheet(sheet: Worksheet, rows: list[dict]) -> None:
    sheet.append(list(HEADERS))
    for row in rows:
        gross = row["price_cents"] * row["units"]
        fee = int((Decimal(gross) * FEE_RATE).to_integral_value())
        sheet.append(
            [
                row["order"],
                # A real date cell, not a string: the export is machine-written.
                row["date"],
                row["title"],
                row["set"],
                row["category"],
                row["condition"],
                row["units"],
                money(row["price_cents"]),
                money(row["shipping_cents"]),
                money(fee),
                row["handle"],
            ]
        )
    for column, width in zip(
        "ABCDEFGHIJK", (14, 16, 38, 24, 20, 18, 6, 12, 14, 16, 18), strict=True
    ):
        sheet.column_dimensions[column].width = width


def build(seed: int, end: date, destination: Path) -> dict:
    rng = random.Random(seed)
    rows = build_rows(rng, OPENED, end)

    workbook = Workbook()
    workbook.remove(workbook.active)
    write_orders_sheet(workbook.create_sheet("Orders"), rows)

    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(destination)

    gross = sum(row["price_cents"] * row["units"] for row in rows)
    shipping = sum(row["shipping_cents"] for row in rows)
    fees = sum(
        int((Decimal(row["price_cents"] * row["units"]) * FEE_RATE).to_integral_value())
        for row in rows
    )
    return {
        "rows": len(rows),
        "orders": len({row["order"] for row in rows}),
        "titles": len({row["title"] for row in rows}),
        "range": f"{OPENED} to {end}",
        "gross": gross,
        "shipping": shipping,
        "fees": fees,
    }


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=19)
    parser.add_argument("--end-date", type=date.fromisoformat, default=None)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "export" / "animanga_knox_online.xlsx",
    )
    args = parser.parse_args()

    end = args.end_date or date.today()
    if end < OPENED:
        print(f"the storefront does not open until {OPENED}", file=sys.stderr)
        return 1

    summary = build(args.seed, end, args.out)

    print(f"\n{SHOP_NAME} — {MARKETPLACE} export (the shop's second register)")
    print(f"{args.out}\n")
    print(f"  period          {summary['range']}")
    print(f"  sheet           Orders")
    print(f"  line rows       {summary['rows']:,}")
    print(f"  orders          {summary['orders']:,}")
    print(f"  listings        {summary['titles']}")
    print(f"  merchandise     ${Decimal(summary['gross']) / 100:,.2f}")
    print(f"  shipping paid   ${Decimal(summary['shipping']) / 100:,.2f}   (not revenue)")
    print(f"  fees charged    ${Decimal(summary['fees']) / 100:,.2f}   (not revenue)")
    print("\n  No costs, no customers, no stock, no refunds. See the mapping's notes.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
