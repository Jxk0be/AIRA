"""A plan per item, not a list of shame.

Which play to run is decided by rules, here, from measured facts. A model is
allowed to phrase the result and nothing else — it never picks the play, never
sets a price, and never sees a number that is not already in the plan. That
split is the difference between "suggest a 30% markdown on these four, which
still clears $2.10 a copy over cost" and a confident paragraph about inventory
velocity.

The order the rules are tried in is the order that loses the shop least money:

1. **Return it** if the vendor takes returns and the money back is the cost.
2. **Bundle it** with something that genuinely sells, if people already buy the
   two together.
3. **Move it** to the location or event where that category actually sells.
4. **Mark it down**, which always works and always costs margin, so it is last.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from app.analytics.stale import CoPurchase, LocationStrength, StaleItem
from app.canonical.enums import StaleKind

Play = Literal["markdown", "bundle", "move", "return_to_vendor"]

# The ladder. Each rung is tried for a month before the next one; a shop that
# goes straight to 50% has given away the margin it did not need to.
MARKDOWN_LADDER: tuple[Decimal, ...] = (Decimal("0.15"), Decimal("0.30"), Decimal("0.50"))

# A bundle partner has to be genuinely popular with the same basket, not a
# coincidence from one Saturday.
MIN_BUNDLE_BASKETS = 3

# A location is only worth moving to if it sells meaningfully more of that
# category than where the item is now.
MIN_MOVE_SHARE = Decimal("0.25")

CENTS = Decimal("0.01")


@dataclass(slots=True)
class Rung:
    """One step of a markdown ladder, priced."""

    discount: Decimal
    price: Decimal
    margin_per_unit: Decimal | None
    clears_cost: bool


@dataclass(slots=True)
class Rescue:
    """What to do with one stale item, and what it is worth doing."""

    item: StaleItem
    play: Play
    headline: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)
    ladder: list[Rung] = field(default_factory=list)

    @property
    def cash_tied_up(self) -> Decimal:
        return self.item.cash_tied_up

    @property
    def break_even_price(self) -> Decimal | None:
        """The price below which the shop is paying to get rid of it."""
        return self.item.cost


def markdown_ladder(item: StaleItem) -> list[Rung]:
    """The three rungs, with the margin each one leaves.

    `clears_cost` is the number an owner actually wants: a 50% cut on something
    bought at 60% of retail is not a discount, it is a loss, and saying so is
    the whole value of showing the ladder rather than one price.
    """
    if item.price is None:
        return []
    rungs: list[Rung] = []
    for discount in MARKDOWN_LADDER:
        price = (item.price * (Decimal("1") - discount)).quantize(CENTS, rounding=ROUND_HALF_UP)
        margin = (price - item.cost).quantize(CENTS) if item.cost is not None else None
        rungs.append(
            Rung(
                discount=discount,
                price=price,
                margin_per_unit=margin,
                clears_cost=margin is None or margin >= 0,
            )
        )
    return rungs


def choose(
    item: StaleItem,
    *,
    partners: list[CoPurchase] | None = None,
    locations: list[LocationStrength] | None = None,
    current_location_id: uuid.UUID | None = None,
    vendor: dict[str, Any] | None = None,
    multi_location: bool = False,
) -> Rescue:
    """Pick the play for one item, from what we can measure about it."""
    ladder = markdown_ladder(item)

    if vendor and vendor.get("takes_returns") and item.kind is StaleKind.DEAD:
        back = item.cash_at_cost
        return Rescue(
            item=item,
            play="return_to_vendor",
            headline=f"Ask {vendor['name']} to take it back",
            reason=(
                f"Nothing has sold in {_days(item)} and {vendor['name']} is on file as the "
                "supplier. A return gets the cost back rather than a fraction of it."
            ),
            detail={
                "vendor_id": str(vendor["id"]),
                "vendor_name": vendor["name"],
                "vendor_email": vendor.get("email"),
                "units": str(item.on_hand),
                "value_at_cost": str(back) if back is not None else None,
            },
            ladder=ladder,
        )

    partner = _best_partner(partners or [])
    if partner is not None:
        bundle_price = _bundle_price(item, partner)
        return Rescue(
            item=item,
            play="bundle",
            headline=f"Bundle it with {partner.label}",
            reason=(
                f"{partner.baskets} customers bought both in the last year, and "
                f"{partner.label} is still selling. A bundle moves the slow half without "
                "marking either down on the shelf."
            ),
            detail={
                "partner_variant_id": str(partner.variant_id),
                "partner_label": partner.label,
                "baskets": partner.baskets,
                "suggested_bundle_price": str(bundle_price) if bundle_price else None,
            },
            ladder=ladder,
        )

    if multi_location:
        target = _best_location(locations or [], current_location_id)
        if target is not None:
            return Rescue(
                item=item,
                play="move",
                headline=f"Move it to {target.location_name}",
                reason=(
                    f"{int(target.share * 100)}% of this category's sales happen at "
                    f"{target.location_name}. The stock is in the wrong room, not the wrong shop."
                ),
                detail={
                    "location_id": str(target.location_id),
                    "location_name": target.location_name,
                    "category_share": str(target.share),
                    "units": str(item.on_hand),
                },
                ladder=ladder,
            )

    rung = _first_sensible_rung(ladder)
    if rung is None:
        return Rescue(
            item=item,
            play="markdown",
            headline="Price it to clear",
            reason=(
                "There is no price on file for this, so a markdown has to be set by hand. "
                f"{_units(item)} have been sitting for {_days(item)}."
            ),
            detail={"needs_price": True, "units": str(item.on_hand)},
            ladder=ladder,
        )

    return Rescue(
        item=item,
        play="markdown",
        headline=f"Mark it down {int(rung.discount * 100)}% to ${rung.price}",
        reason=_markdown_reason(item, rung),
        detail={
            "discount": str(rung.discount),
            "new_price": str(rung.price),
            "margin_per_unit": (
                str(rung.margin_per_unit) if rung.margin_per_unit is not None else None
            ),
            "break_even_price": str(item.cost) if item.cost is not None else None,
            "units": str(item.on_hand),
        },
        ladder=ladder,
    )


def _markdown_reason(item: StaleItem, rung: Rung) -> str:
    base = f"{_units(item)} on the shelf, nothing sold in {_days(item)}."
    if rung.margin_per_unit is None:
        return f"{base} No cost on file, so what this leaves per copy is not knowable here."
    if rung.margin_per_unit > 0:
        return f"{base} At ${rung.price} that is still ${rung.margin_per_unit} a copy over cost."
    return (
        f"{base} At ${rung.price} that is ${abs(rung.margin_per_unit)} a copy below cost — "
        f"break-even is ${item.cost}."
    )


def _first_sensible_rung(ladder: list[Rung]) -> Rung | None:
    """The gentlest markdown that still clears cost, else the gentlest of all."""
    if not ladder:
        return None
    for rung in ladder:
        if rung.clears_cost:
            return rung
    return ladder[0]


def _best_partner(partners: list[CoPurchase]) -> CoPurchase | None:
    for partner in partners:
        if partner.baskets >= MIN_BUNDLE_BASKETS and partner.units_sold_recently > 0:
            return partner
    return None


def _best_location(
    locations: list[LocationStrength], current: uuid.UUID | None
) -> LocationStrength | None:
    for strength in locations:
        if strength.location_id != current and strength.share >= MIN_MOVE_SHARE:
            return strength
    return None


def _bundle_price(item: StaleItem, partner: CoPurchase) -> Decimal | None:
    """A bundle priced at a tenth off the pair, rounded to something sayable."""
    if item.price is None or partner.price is None:
        return None
    pair = item.price + partner.price
    return (pair * Decimal("0.9")).quantize(Decimal("1"), rounding=ROUND_HALF_UP) - Decimal("0.01")


def _days(item: StaleItem) -> str:
    if item.never_sold:
        return "since it arrived — it has never sold"
    return f"{item.days_since_last_sale} days"


def _units(item: StaleItem) -> str:
    count = format(item.on_hand.normalize(), "f")
    return f"{count} cop{'y' if item.on_hand == 1 else 'ies'}"


# --------------------------------------------------------------------------
# Phrasing
# --------------------------------------------------------------------------

PHRASING_SYSTEM = (
    "You write one short line for a shop owner about a specific item that is not selling. "
    "You are given the decision and the numbers behind it; your only job is wording. "
    "Never change the play, the price, the discount or any figure. Never add a figure. "
    "Plain and specific, like a sharp employee. No hype, no emoji, one sentence."
)


def phrasing_payload(rescues: list[Rescue]) -> list[dict[str, Any]]:
    """Exactly what the model is allowed to see: the decision and its numbers."""
    return [
        {
            "id": str(rescue.item.variant_id),
            "item": rescue.item.label,
            "play": rescue.play,
            "headline": rescue.headline,
            "reason": rescue.reason,
            "days_since_last_sale": rescue.item.days_since_last_sale,
            "units_on_hand": str(rescue.item.on_hand),
            "cash_tied_up": str(rescue.cash_tied_up),
            "detail": rescue.detail,
        }
        for rescue in rescues
    ]


def apply_phrasing(rescues: list[Rescue], phrased: dict[str, str]) -> None:
    """Swap in the model's wording where it gave us one, in place.

    Anything missing keeps the rule-written sentence, which is always present
    and always correct, just plainer.
    """
    for rescue in rescues:
        better = phrased.get(str(rescue.item.variant_id))
        if better:
            rescue.reason = better
