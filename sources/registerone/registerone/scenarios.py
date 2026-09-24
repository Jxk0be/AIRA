"""Things planted in the fixture on purpose, for the detectors to find.

The quirks in QUIRKS.md are about surviving messy data. These are the opposite:
each one is a specific situation a specific feature claims it can spot, put
into the data at a known size so the claim is testable rather than plausible.

Every scenario obeys RegisterOne's own invariants — a count still equals the
sum of its movement history, refunds never exceed a payment, nothing goes
negative — because a fixture that has to break its own rules to make a feature
look good is not evidence of anything.

What is planted, and why each one is shaped the way it is, is in SCENARIOS.md.
The seeder prints the measured figures after every run, so this file and that
one cannot quietly drift apart.
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from registerone.catalog import SHOP_TIMEZONE

if TYPE_CHECKING:  # generate imports this module, so only the types come back
    from registerone.generate import Dataset, Variation

TZ = ZoneInfo(SHOP_TIMEZONE)


def _local(day: date, hour: int, minute: int) -> datetime:
    """A shop-local wall-clock moment, as UTC. Mirrors the generator's own."""
    return datetime.combine(day, time(hour, minute), tzinfo=TZ).astimezone(UTC)

# How many items should be visibly about to run out, and inside how many days.
RUNNING_OUT_COUNT = 8
RUNNING_OUT_MAX_COVER_DAYS = 10
# A steady seller in a shop this size moves a handful a month, not a handful a
# day. Setting the bar at a day's worth of sales a week is what makes eight of
# them exist at all; anything higher and the scenario is about two items.
RUNNING_OUT_MIN_UNITS_28D = 3

# Twelve items, and roughly this much money at cost sitting still.
DEAD_STOCK_COUNT = 12
DEAD_STOCK_TARGET_CENTS = 150_000

# One variation loses this many units with nothing rung up behind them.
SHRINK_UNITS = 6
SHRINK_DAYS_AGO = 4
SHRINK_MIN_UNITS_28D = 3

# What a quiet Saturday is left taking, as a share of what it originally took.
#
# Trimmed by money rather than by a share of the orders, because a Saturday
# here is only half a dozen baskets and removing a fixed proportion of them
# lands anywhere between "a slow day" and "the shop was shut", depending on
# which baskets they were.
#
# A third is the number, and the reason is worth knowing: an ordinary Saturday
# here ranges from about $230 to about $620, so a day 45% below the median is
# still inside normal variation and a detector that flagged it would be
# flagging a third of the Saturdays in the year. For the scenario to be a
# scenario, the day has to be one anybody would agree is alarming.
QUIET_SATURDAY_KEEPS = 0.33
QUIET_SATURDAY_WEEKS_AGO = 3

# One week where refunds run well above normal. Planted as a count of extra
# refunds rather than as a probability, because a refund is dated when the
# money goes back, not when the sale happened: raising the chance on orders
# placed that week would put the refunds one to three weeks later, which is
# a different week and not the scenario.
REFUND_SPIKE_EXTRA = 8
REFUND_SPIKE_DRAWN_FROM_DAYS = 21
REFUND_SPIKE_WEEKS_AGO = 5


# ---------------------------------------------------------------------------
# Chosen before the simulation runs
# ---------------------------------------------------------------------------


def quiet_saturday(end_date: date) -> date:
    """The Saturday that will trade well below a normal Saturday.

    Far enough back that the eight-week baseline has plenty of ordinary
    Saturdays around it, recent enough that a detector looking at the last
    month still sees it.
    """
    day = end_date - timedelta(days=QUIET_SATURDAY_WEEKS_AGO * 7)
    while day.weekday() != 5:
        day -= timedelta(days=1)
    return day


def trim_quiet_saturday(data: Dataset, rng: random.Random) -> date:
    """Take most of one Saturday's takings away, and say which day it was.

    Runs between the sales simulation and the inventory build, so the orders
    are gone before any stock movement is derived from them and the fixture's
    invariants hold without anything being patched up afterwards.

    Biggest baskets first. What is left then reads like a genuinely dead day —
    a handful of small sales — rather than like one unexplained large one.
    """
    day = quiet_saturday(data.end_date)
    opens, closes = _local(day, 0, 0), _local(day + timedelta(days=1), 0, 0)

    that_day = [
        order
        for order in data.orders
        if opens <= order["created_at"] < closes
        and order["location_id"] == "LOC_MAIN"
        and order["state"] != "CANCELED"
    ]
    if len(that_day) < 2:
        return day

    took = sum(
        order["total_money"] - order["total_tax_money"] - order["total_tip_money"]
        for order in that_day
    )
    target = took * QUIET_SATURDAY_KEEPS

    doomed: set[str] = set()
    remaining = took
    for order in sorted(that_day, key=lambda o: (-o["total_money"], o["id"])):
        if remaining <= target or len(doomed) >= len(that_day) - 1:
            break
        doomed.add(order["id"])
        remaining -= order["total_money"] - order["total_tax_money"] - order["total_tip_money"]

    if not doomed:
        return day

    doomed_payments = {
        payment["id"] for payment in data.payments if payment["order_id"] in doomed
    }
    data.orders[:] = [order for order in data.orders if order["id"] not in doomed]
    data.line_items[:] = [line for line in data.line_items if line["order_id"] not in doomed]
    data.payments[:] = [
        payment for payment in data.payments if payment["order_id"] not in doomed
    ]
    data.refunds[:] = [
        refund
        for refund in data.refunds
        if refund["order_id"] not in doomed and refund["payment_id"] not in doomed_payments
    ]
    return day


def refund_spike_week(end_date: date) -> date:
    """The Monday of the week refunds go through the roof."""
    day = end_date - timedelta(days=REFUND_SPIKE_WEEKS_AGO * 7)
    return day - timedelta(days=day.weekday())


def in_refund_spike(day: date, week_start: date) -> bool:
    return week_start <= day < week_start + timedelta(days=7)


def _refund_spike(data: Dataset, rng: random.Random) -> dict:
    """A week where a lot of money goes back over the counter.

    Drawn from sales in the three weeks before it, which is how a real refund
    week works — a bad batch or a display model that everyone returns at once.
    Every refund still fits inside its payment, so the fixture's own invariant
    about that holds.
    """
    week_start = refund_spike_week(data.end_date)
    already: dict[str, int] = defaultdict(int)
    for refund in data.refunds:
        already[refund["payment_id"]] += refund["amount_money"]

    payments_by_order: dict[str, list[dict]] = defaultdict(list)
    for payment in data.payments:
        payments_by_order[payment["order_id"]].append(payment)

    window_opens = _local(week_start - timedelta(days=REFUND_SPIKE_DRAWN_FROM_DAYS), 0, 0)
    window_closes = _local(week_start, 0, 0)
    eligible = sorted(
        (
            order
            for order in data.orders
            if order["state"] == "COMPLETED"
            and window_opens <= order["created_at"] < window_closes
            and payments_by_order.get(order["id"])
        ),
        key=lambda order: order["id"],
    )
    if not eligible:
        return {}

    rng.shuffle(eligible)
    highest = max((int(r["id"].split("_")[1]) for r in data.refunds), default=0)
    planted: list[dict] = []

    for order in eligible:
        if len(planted) >= REFUND_SPIKE_EXTRA:
            break
        payment = max(payments_by_order[order["id"]], key=lambda p: p["amount_money"])
        headroom = payment["amount_money"] - already[payment["id"]]
        if headroom < 200:
            continue
        amount = max(100, int(headroom * rng.uniform(0.3, 0.7)))
        highest += 1
        refunded_at = _local(
            week_start + timedelta(days=rng.randint(0, 6)), rng.randint(10, 17), rng.randint(0, 59)
        )
        data.refunds.append(
            {
                "id": f"REF_{highest:05d}",
                "payment_id": payment["id"],
                "order_id": order["id"],
                "amount_money": amount,
                "reason": rng.choice(
                    ["Damaged in box", "Misprint run", "Wrong volume", "Faulty product"]
                ),
                "status": "COMPLETED",
                "created_at": refunded_at,
            }
        )
        already[payment["id"]] += amount
        planted.append({"order_id": order["id"], "amount_cents": amount})

    return {
        "week_start": str(week_start),
        "refunds_added": len(planted),
        "value_cents": sum(row["amount_cents"] for row in planted),
    }


# ---------------------------------------------------------------------------
# Planted afterwards, on the finished inventory
# ---------------------------------------------------------------------------


def plant(data: Dataset, rng: random.Random, variations: list[Variation]) -> None:
    """Shape the finished fixture so each detector has something to find.

    Runs after the inventory is built because all three of these are statements
    about what is on the shelf *now* relative to what has been selling, and
    neither half of that exists until both have been simulated.
    """
    notes: dict[str, object] = {}
    sold_28 = _units_sold_recently(data)
    counts = _count_index(data)
    receipts = _receipt_index(data)
    costs = {v.id: v.cost_amount for v in variations}
    names = {v.id: (v.item_name, v.name) for v in variations}

    notes["running_out"] = _running_out(data, rng, sold_28, counts, receipts, names)
    notes["dead_stock"] = _dead_stock(data, sold_28, counts, receipts, costs, names)
    notes["shrink"] = _shrink(data, rng, sold_28, counts, costs, names)
    notes["refund_spike"] = _refund_spike(data, rng)
    data.notes["scenarios"] = notes


def _units_sold_recently(data: Dataset, days: int = 28) -> dict[str, int]:
    """Units per variation over the last four weeks, at any location."""
    orders = {o["id"]: o for o in data.orders}
    cutoff = _local(data.end_date - timedelta(days=days - 1), 0, 0)
    sold: dict[str, int] = defaultdict(int)
    for line in data.line_items:
        variation_id = line.get("_variation_id")
        if variation_id is None:
            continue
        order = orders[line["order_id"]]
        if order["state"] == "CANCELED":
            continue
        if order["created_at"] < cutoff:
            continue
        sold[variation_id] += line["_quantity"]
    return dict(sold)


def _count_index(data: Dataset) -> dict[tuple[str, str, str], dict]:
    return {
        (row["variation_id"], row["location_id"], row["state"]): row for row in data.counts
    }


def _receipt_index(data: Dataset) -> dict[tuple[str, str], list[dict]]:
    """Receiving movements per variation and location, newest first."""
    receipts: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in data.adjustments:
        if row["from_state"] is None and row["to_state"] == "IN_STOCK":
            receipts[(row["variation_id"], row["location_id"])].append(row)
    for rows in receipts.values():
        rows.sort(key=lambda row: row["occurred_at"], reverse=True)
    return receipts


def _take_from_receipts(receipts: list[dict], wanted: int) -> int:
    """Remove `wanted` units from the newest receipts. Returns what was removed.

    Newest first, and never below one unit on a shipment: taking units out of
    an early delivery would send the running balance negative halfway through
    the history, which the fixture's own invariant check would catch.
    """
    removed = 0
    for receipt in receipts:
        if removed >= wanted:
            break
        have = int(receipt["quantity"])
        take = min(have - 1, wanted - removed)
        if take > 0:
            receipt["quantity"] = str(have - take)
            removed += take
    return removed


def _label(names: dict[str, tuple[str, str | None]], variation_id: str) -> str:
    item, variant = names.get(variation_id, (variation_id, None))
    return f"{item} — {variant}" if variant else item


def _running_out(
    data: Dataset,
    rng: random.Random,
    sold_28: dict[str, int],
    counts: dict[tuple[str, str, str], dict],
    receipts: dict[tuple[str, str], list[dict]],
    names: dict[str, tuple[str, str | None]],
) -> list[dict]:
    """Eight steady sellers left with under ten days of cover.

    Deliberately not "nearly zero". An item with one left is obvious to anyone
    walking past the shelf; an item with nine left that sells one a day is the
    one a reorder assistant earns its keep on.
    """
    # Sorted by units then by id: ties have to break the same way every run or
    # the fixture stops being reproducible from a seed.
    candidates = sorted(
        (
            (variation_id, units)
            for variation_id, units in sold_28.items()
            if units >= RUNNING_OUT_MIN_UNITS_28D
            and (variation_id, "LOC_MAIN", "IN_STOCK") in counts
        ),
        key=lambda pair: (-pair[1], pair[0]),
    )

    planted: list[dict] = []
    for variation_id, units in candidates:
        if len(planted) >= RUNNING_OUT_COUNT:
            break
        row = counts[(variation_id, "LOC_MAIN", "IN_STOCK")]
        on_hand = int(row["quantity"])
        daily = units / 28
        cover_days = rng.randint(3, RUNNING_OUT_MAX_COVER_DAYS - 1)
        target = max(1, round(daily * cover_days))
        # Needs room to trim: an item already at the target was not planted by
        # us and cannot be asserted on.
        if on_hand < target + 2:
            continue

        removed = _take_from_receipts(receipts[(variation_id, "LOC_MAIN")], on_hand - target)
        if removed <= 0:
            continue
        row["quantity"] = str(on_hand - removed)
        planted.append(
            {
                "variation_id": variation_id,
                "label": _label(names, variation_id),
                "on_hand": on_hand - removed,
                "units_last_28_days": units,
                "days_of_cover": round((on_hand - removed) / daily, 1),
            }
        )
    return planted


def _dead_stock(
    data: Dataset,
    sold_28: dict[str, int],
    counts: dict[tuple[str, str, str], dict],
    receipts: dict[tuple[str, str], list[dict]],
    costs: dict[str, int | None],
    names: dict[str, tuple[str, str | None]],
) -> dict:
    """Twelve items that have never sold, worth about $1,500 at cost.

    The total is the number the feature quotes back to the owner, so it is set
    here rather than left to fall out of the simulation — a test that asserts
    "about fifteen hundred dollars" against a figure nobody chose is a test
    that fails the first time the seed changes.
    """
    ever_sold = {
        line["_variation_id"] for line in data.line_items if line.get("_variation_id")
    }
    candidates = sorted(
        variation_id
        for (variation_id, location_id, state), row in counts.items()
        if state == "IN_STOCK"
        and location_id == "LOC_MAIN"
        and variation_id not in ever_sold
        and variation_id not in sold_28
        and costs.get(variation_id)
        and int(row["quantity"]) > 0
    )[:DEAD_STOCK_COUNT]

    if not candidates:
        return {"items": [], "value_at_cost": 0}

    per_item = DEAD_STOCK_TARGET_CENTS // len(candidates)
    items: list[dict] = []
    total = 0
    for index, variation_id in enumerate(candidates):
        cost = int(costs[variation_id] or 0) or 1
        # Spend what is left on the last one, so the total lands exactly.
        budget = (
            per_item
            if index < len(candidates) - 1
            else DEAD_STOCK_TARGET_CENTS - total
        )
        quantity = max(1, min(40, round(budget / cost)))
        row = counts[(variation_id, "LOC_MAIN", "IN_STOCK")]
        row["quantity"] = str(quantity)
        # One shipment, never sold, so the whole count sits on one receipt.
        shipments = receipts[(variation_id, "LOC_MAIN")]
        if len(shipments) == 1:
            shipments[0]["quantity"] = str(quantity)
        else:
            continue
        total += quantity * cost
        items.append(
            {
                "variation_id": variation_id,
                "label": _label(names, variation_id),
                "on_hand": quantity,
                "unit_cost_cents": cost,
                "value_at_cost_cents": quantity * cost,
            }
        )
    return {"items": items, "value_at_cost": total}


def _shrink(
    data: Dataset,
    rng: random.Random,
    sold_28: dict[str, int],
    counts: dict[tuple[str, str, str], dict],
    costs: dict[str, int | None],
    names: dict[str, tuple[str, str | None]],
) -> dict:
    """Six units that left the shelf as a sale with nothing rung up.

    This is what shrink actually looks like in a POS that reconciles its
    counts: the stock movement is there, the receipt is not. The fixture's
    invariant still holds — the count equals the sum of its history — which is
    the point. A detector has to find this by comparing the movement history
    against the order lines, not by finding a broken total.
    """
    candidates = sorted(
        variation_id
        for variation_id, units in sold_28.items()
        if units >= SHRINK_MIN_UNITS_28D
        and costs.get(variation_id)
        and (variation_id, "LOC_MAIN", "IN_STOCK") in counts
        and int(counts[(variation_id, "LOC_MAIN", "IN_STOCK")]["quantity"]) > SHRINK_UNITS + 2
    )
    if not candidates:
        return {}

    variation_id = candidates[len(candidates) // 2]
    in_stock = counts[(variation_id, "LOC_MAIN", "IN_STOCK")]
    occurred = _local(data.end_date - timedelta(days=SHRINK_DAYS_AGO), 15, rng.randint(0, 59))

    highest = max((int(row["id"].split("_")[1]) for row in data.adjustments), default=0)
    data.adjustments.append(
        {
            "id": f"ADJ_{highest + 1:06d}",
            "variation_id": variation_id,
            "location_id": "LOC_MAIN",
            "from_state": "IN_STOCK",
            "to_state": "SOLD",
            "quantity": str(SHRINK_UNITS),
            "occurred_at": occurred,
            "reason": "Sale",
        }
    )
    in_stock["quantity"] = str(int(in_stock["quantity"]) - SHRINK_UNITS)
    sold_row = counts.get((variation_id, "LOC_MAIN", "SOLD"))
    if sold_row is not None:
        sold_row["quantity"] = str(int(sold_row["quantity"]) + SHRINK_UNITS)

    cost = int(costs[variation_id] or 0)
    return {
        "variation_id": variation_id,
        "label": _label(names, variation_id),
        "units": SHRINK_UNITS,
        "value_at_cost_cents": SHRINK_UNITS * cost,
        "occurred_at": str(occurred.astimezone(TZ).date()),
    }
