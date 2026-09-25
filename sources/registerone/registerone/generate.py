"""Eighteen months of plausible, deliberately messy shop history.

Deterministic: same `--seed` and same `--end-date` produce byte-identical data.

Two things this file is careful about, because they are what make the fixture
worth testing against:

* **Local time is the shop's time.** Order timestamps are built in
  America/New_York and converted to UTC. A Saturday spike really does land on a
  Saturday in the shop's timezone, which means anything that buckets dates in
  UTC will visibly get the wrong answer.
* **Inventory reconciles exactly.** Every current count is the arithmetic sum
  of the adjustment history, by construction. If a sync produces a different
  number, the sync is wrong.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from registerone import scenarios
from registerone.catalog import (
    APPAREL,
    APPAREL_SIZE_WEIGHT,
    APPAREL_SIZES,
    CARD_CONDITION_WEIGHT,
    CARD_CONDITIONS,
    CARD_GAMES,
    CARD_SETS,
    CATEGORIES,
    CHIBI_FIGURES,
    COST_RATIO,
    DESK_MATS,
    DRINKS,
    GAME_NAMES,
    LOCATIONS,
    MAGNETS,
    MANGA_SERIES,
    MANGA_SPECIAL_EDITION_SURCHARGE,
    MODEL_KITS,
    PINS,
    PLUSH,
    PRIZE_FIGURES,
    SCALE_FIGURES,
    SHOP_TIMEZONE,
    SINGLES,
    SNACKS,
    SUPPLIES,
    TAX_RATE,
    VENDORS,
    VENDORS_ACCEPTING_RETURNS,
    WALL_ART,
    ItemSpec,
    Product,
    VariationSpec,
)

TZ = ZoneInfo(SHOP_TIMEZONE)
UTC = ZoneInfo("UTC")

# A shop that sells $2.99 ramune and $0.99 umaibo rings up more tickets a day
# than one that only sells $9.99 volumes, so the target is set for the catalog
# the shop actually carries.
TARGET_ORDERS = 4200
HISTORY_DAYS = 548  # eighteen months

# Target shares for the deliberate mess. QUIRKS.md documents each one, and the
# seed script prints what the run actually produced.
SHARE_ORDERS_WITHOUT_CUSTOMER = 0.60
SHARE_CUSTOM_AMOUNT_LINES = 0.04
SHARE_DUPLICATE_CUSTOMERS = 0.05
SHARE_ORDERS_REFUNDED = 0.02
SHARE_ORDERS_CANCELED = 0.01
SHARE_SPLIT_PAYMENTS = 0.03
SHARE_VARIATIONS_WITHOUT_COST = 0.10
SHARE_VARIATIONS_LOW_STOCK = 0.08
SHARE_VARIATIONS_DEAD_STOCK = 0.05
SOFT_DELETED_ITEMS = 8
VARIABLE_PRICED_VARIATIONS = 5

# How many units of a thing the shop likes to have facing out, by category.
# Without this, receiving exactly covers the next fortnight's demand and every
# single variation ends the history sitting at one or two units.
SHELF_DEPTH = {
    "manga": (4, 9),
    "figures_prize": (4, 10),
    "figures_chibi": (3, 7),
    "figures_scale": (2, 5),
    "tcg_sealed": (6, 18),
    "tcg_singles": (2, 6),
    "kits": (4, 8),
    "plush": (5, 12),
    "apparel": (4, 10),
    "wall_art": (6, 20),
    "desk": (3, 8),
    "pins": (8, 24),
    "magnets": (10, 30),
    "snacks": (12, 40),
    "drinks": (12, 36),
    "supplies": (10, 30),
}

# How often a line is for more than one unit. Nobody buys two $220 scale figures
# in one go; buying four ramune and a box of Pocky is a Saturday afternoon.
MULTI_UNIT_CHANCE = {
    "supplies": 0.18,
    "manga": 0.18,
    "tcg_sealed": 0.28,
    "snacks": 0.46,
    "drinks": 0.40,
    "magnets": 0.22,
    "pins": 0.16,
}

# Roughly what the shop is willing to have standing on the shelf in any one
# product, at retail. Caps the depths above for the expensive end of a category.
FACING_VALUE_CENTS = 50_000

# Categories people buy on the way to the register rather than on the way in.
IMPULSE_CATEGORIES = ("snacks", "drinks", "pins", "magnets")

# Who the shop buys each part of the shelf from, by position in VENDORS. Nobody
# orders Pocky from a book distributor, and a reorder list that says otherwise
# is a reorder list an owner stops trusting.
VENDOR_BY_CATEGORY: dict[str, tuple[int, ...]] = {
    "manga": (1, 2),
    "figures_prize": (5, 2),
    "figures_chibi": (5,),
    "figures_scale": (5, 2),
    "tcg_sealed": (3, 4),
    "tcg_singles": (3,),
    "kits": (5,),
    "plush": (6, 2),
    "apparel": (6,),
    "wall_art": (6, 2),
    "desk": (6,),
    "pins": (6,),
    "magnets": (6,),
    "snacks": (7,),
    "drinks": (7,),
    "supplies": (3, 4),
}

WEEKDAY_WEIGHT = {0: 0.70, 1: 0.75, 2: 0.85, 3: 0.95, 4: 1.50, 5: 1.90, 6: 1.20}
MONTH_WEIGHT = {
    1: 0.80,
    2: 0.85,
    3: 0.95,
    4: 0.95,
    5: 1.00,
    6: 1.05,
    7: 1.10,
    8: 1.05,
    9: 0.95,
    10: 1.05,
    11: 1.55,
    12: 2.10,
}
OPEN_HOURS = {  # weekday -> (open, close) in shop-local time
    0: (11, 20),
    1: (11, 20),
    2: (11, 20),
    3: (11, 20),
    4: (11, 21),
    5: (10, 21),
    6: (12, 18),
}

CARD_BRANDS = ("VISA", "MASTERCARD", "AMERICAN_EXPRESS", "DISCOVER")
FIRST_NAMES = (
    "Avery",
    "Jordan",
    "Riley",
    "Casey",
    "Morgan",
    "Quinn",
    "Devon",
    "Harper",
    "Emery",
    "Rowan",
    "Sasha",
    "Micah",
    "Noel",
    "Aubrey",
    "Sydney",
    "Blake",
    "Kai",
    "Marlowe",
    "Reese",
    "Tatum",
    "Wren",
    "Emerson",
    "Jules",
    "Sloane",
)
LAST_NAMES = (
    "Whitaker",
    "Brantley",
    "Ferris",
    "Okonkwo",
    "Delgado",
    "Nakamura",
    "Bell",
    "Castellano",
    "Duffy",
    "Ramachandran",
    "Halvorsen",
    "Mbeki",
    "Pruitt",
    "Sandoval",
    "Vance",
    "Ionescu",
    "Fairweather",
    "Nguyen",
    "Kowalski",
    "Reyes",
)
EMAIL_DOMAINS = ("gmail.com", "yahoo.com", "outlook.com", "protonmail.com", "icloud.com")


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class Variation:
    id: str
    item_id: str
    name: str | None
    sku: str | None
    upc: str | None
    price_amount: int | None
    pricing_type: str
    track_inventory: bool
    is_deleted: bool
    updated_at: datetime
    # Simulation-only fields, never written to the database.
    category_key: str = ""
    weight: float = 1.0
    con_affinity: float = 1.0
    series_key: str | None = None
    series_index: int | None = None
    item_name: str = ""
    cost_amount: int | None = None
    vendor_id: str | None = None
    dead_stock: bool = False
    retired_at: datetime | None = None
    shelf_depth: int = 4


@dataclass
class Dataset:
    end_date: date
    start_date: date
    locations: list[dict] = field(default_factory=list)
    categories: list[dict] = field(default_factory=list)
    vendors: list[dict] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
    variations: list[dict] = field(default_factory=list)
    vendor_info: list[dict] = field(default_factory=list)
    customers: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    line_items: list[dict] = field(default_factory=list)
    payments: list[dict] = field(default_factory=list)
    refunds: list[dict] = field(default_factory=list)
    adjustments: list[dict] = field(default_factory=list)
    counts: list[dict] = field(default_factory=list)
    notes: dict = field(default_factory=dict)


def _local(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    """A shop-local wall-clock moment, returned as UTC."""
    return datetime.combine(day, time(hour, minute, second), tzinfo=TZ).astimezone(UTC)


def _sku(prefix: str, n: int) -> str:
    return f"{prefix}-{n:05d}"


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def _shelf(
    specs: list[ItemSpec],
    products: tuple[Product, ...],
    category_key: str,
    *,
    describe: str,
) -> None:
    """Put a run of single-variation products on the shelf.

    Most of the shop is like this: one product, one price, one SKU. Manga, cards
    and apparel are the exceptions, and they get their own code below.
    """
    for index, product in enumerate(products):
        attributes: dict[str, str] = {}
        if product.brand:
            attributes["brand"] = product.brand
        if product.franchise:
            attributes["franchise"] = product.franchise
        spec = ItemSpec(
            name=product.name,
            category_key=category_key,
            product_type="REGULAR",
            description=describe.format(name=product.name, franchise=product.franchise or ""),
            custom_attributes=attributes,
            popularity=product.popularity,
            con_affinity=product.con_affinity,
        )
        spec.variations.append(
            VariationSpec(
                name=None,
                price_cents=product.price_cents,
                cost_ratio=COST_RATIO[category_key],
                sku_hint=_hint(category_key, product.name, index),
            )
        )
        specs.append(spec)


def _hint(prefix: str, name: str, index: int) -> str:
    """A SKU stem a human could read off a shelf label."""
    letters = "".join(c for c in name.upper() if c.isalnum())[:6]
    return f"{prefix[:3].upper()}-{letters}-{index:02d}"


def _shelf_depth(rng: random.Random, category_key: str, price_cents: int | None) -> int:
    """How many of one thing the shop keeps facing out.

    Category alone is not enough: "sealed product" covers a $4.49 booster pack
    and a $161.64 booster box, and a shop that keeps eighteen of each has ten
    thousand dollars standing in a display case. Money on the shelf per SKU is
    roughly flat, so the category range is capped by what the item costs.
    """
    low, high = SHELF_DEPTH.get(category_key, (4, 8))
    if price_cents:
        cap = max(1, min(high, FACING_VALUE_CENTS // price_cents))
        low, high = min(low, cap), cap
    return rng.randint(low, high)


def build_item_specs(rng: random.Random) -> list[ItemSpec]:
    specs: list[ItemSpec] = []

    # --- manga: the shelf that defines the shop -----------------------------
    for series in MANGA_SERIES:
        for volume in range(1, series.volumes + 1):
            # Volume 1 outsells volume 12 by a wide margin in every real shop.
            decay = 0.82 ** (volume - 1)
            spec = ItemSpec(
                name=f"{series.title} Vol. {volume}",
                category_key="manga",
                product_type="REGULAR",
                description=(
                    f"Volume {volume} of {series.title}, {series.publisher}, paperback. "
                    f"{'Series opener.' if volume == 1 else 'Continues the story.'}"
                ),
                custom_attributes={
                    "series": series.title,
                    "volume": str(volume),
                    "publisher": series.publisher,
                },
                series_key=series.key,
                series_index=volume,
                popularity=series.popularity * decay,
                con_affinity=0.35,
            )
            spec.variations.append(
                VariationSpec(
                    name="Paperback",
                    price_cents=series.price_cents,
                    cost_ratio=COST_RATIO["manga"],
                    sku_hint=f"MNG-{series.key[:6].upper()}-{volume:02d}",
                )
            )
            if volume == 1 and series.popularity > 1.3:
                spec.variations.append(
                    VariationSpec(
                        name="Special Edition",
                        price_cents=series.price_cents + MANGA_SPECIAL_EDITION_SURCHARGE,
                        cost_ratio=COST_RATIO["manga"] + 0.05,
                        sku_hint=f"MNG-{series.key[:6].upper()}-{volume:02d}-SE",
                        popularity=0.25,
                    )
                )
            specs.append(spec)

    # --- figures ------------------------------------------------------------
    _shelf(
        specs,
        PRIZE_FIGURES,
        "figures_prize",
        describe="{name}. Prize figure, in box.",
    )
    _shelf(
        specs,
        CHIBI_FIGURES,
        "figures_chibi",
        describe="{name}. Articulated collector figure, in box.",
    )
    _shelf(
        specs,
        SCALE_FIGURES,
        "figures_scale",
        describe="{name}. Scale figure, in box. Locked case.",
    )

    # --- sealed trading card product ----------------------------------------
    for game in CARD_GAMES:
        for code, set_name in game.sets:
            for product, price, popularity in game.sealed:
                spec = ItemSpec(
                    name=f"{game.name}: {set_name} {product}",
                    category_key="tcg_sealed",
                    product_type="REGULAR",
                    description=f"Sealed {product.lower()} from the {set_name} set.",
                    custom_attributes={
                        "game": game.name,
                        "set": code,
                        "set_name": set_name,
                    },
                    popularity=popularity,
                    con_affinity=2.2,
                )
                spec.variations.append(
                    VariationSpec(
                        name=None,
                        price_cents=price,
                        cost_ratio=COST_RATIO["tcg_sealed"],
                        sku_hint=f"SLD-{code}-{product[:3].upper()}",
                    )
                )
                specs.append(spec)

    # --- singles, graded by condition ---------------------------------------
    for index, card in enumerate(SINGLES):
        spec = ItemSpec(
            name=f"{card.name} - {card.set_name}",
            category_key="tcg_singles",
            product_type="REGULAR",
            description=f"Single card: {card.name}, {card.set_name}, {GAME_NAMES[card.game_key]}.",
            custom_attributes={
                "game": GAME_NAMES[card.game_key],
                "set_name": card.set_name,
                "card": card.name,
            },
            # A $420 Umbreon moves once a quarter; a $9 Budew moves most weeks.
            # Capped low on purpose: a shop has one or two copies of a given
            # single in the case, not a stack, so even the cheap ones are a few
            # dozen sales a year rather than a few hundred.
            popularity=max(0.06, min(0.40, 800 / max(400, card.price_cents))),
            con_affinity=1.6,
        )
        for condition, ratio in CARD_CONDITIONS:
            spec.variations.append(
                VariationSpec(
                    name=condition,
                    price_cents=max(99, int(card.price_cents * ratio / 25) * 25),
                    cost_ratio=COST_RATIO["tcg_singles"],
                    sku_hint=f"SGL-{card.game_key.upper()}-{index:02d}-{condition}",
                    popularity=CARD_CONDITION_WEIGHT[condition],
                )
            )
        specs.append(spec)

    # --- model kits, plush --------------------------------------------------
    _shelf(specs, MODEL_KITS, "kits", describe="{name}. Snap-fit plastic model kit.")
    _shelf(specs, PLUSH, "plush", describe="{name}. Licensed plush.")

    # --- apparel, one variation per size ------------------------------------
    for index, garment in enumerate(APPAREL):
        spec = ItemSpec(
            name=garment.name,
            category_key="apparel",
            product_type="REGULAR",
            description=f"{garment.name}. Unisex sizing.",
            custom_attributes=({"franchise": garment.franchise} if garment.franchise else {}),
            popularity=garment.popularity,
            con_affinity=garment.con_affinity,
        )
        for size in APPAREL_SIZES:
            spec.variations.append(
                VariationSpec(
                    name=size,
                    price_cents=garment.price_cents,
                    cost_ratio=COST_RATIO["apparel"],
                    sku_hint=f"APP-{_hint('APP', garment.name, index)[4:]}-{size}",
                    popularity=APPAREL_SIZE_WEIGHT[size],
                )
            )
        specs.append(spec)

    # --- the wall, the desk, the counter ------------------------------------
    _shelf(specs, WALL_ART, "wall_art", describe="{name}. Rolled in a tube.")
    _shelf(specs, DESK_MATS, "desk", describe="{name}. Stitched edge, rubber base.")
    _shelf(specs, PINS, "pins", describe="{name}. Counter display.")
    _shelf(specs, MAGNETS, "magnets", describe="{name}. Counter display.")

    # --- the cooler and the snack rack --------------------------------------
    _shelf(specs, SNACKS, "snacks", describe="{name}. Japanese import.")
    _shelf(specs, DRINKS, "drinks", describe="{name}. Japanese import. Cooler.")

    # --- card supplies ------------------------------------------------------
    _shelf(specs, SUPPLIES, "supplies", describe="{name}. Shop staple.")

    return specs


def build_catalog(data: Dataset, rng: random.Random) -> list[Variation]:
    """Turn the item specs into rows, and attach the quirks that live on the
    catalog: soft deletes, variable pricing, missing costs, dead stock."""
    for location in LOCATIONS:
        data.locations.append(
            {
                "id": location.id,
                "name": location.name,
                "timezone": location.timezone,
                "status": location.status,
            }
        )

    category_ids: dict[str, str] = {}
    midpoint = _local(data.start_date + timedelta(days=HISTORY_DAYS // 2), 3, 0)
    for spec in CATEGORIES:
        cat_id = f"CAT_{spec.key.upper()}"
        category_ids[spec.key] = cat_id
        data.categories.append(
            {
                "id": cat_id,
                "name": spec.name,
                "parent_id": category_ids.get(spec.parent) if spec.parent else None,
                # The renamed category carries a mid-history updated_at; every
                # other category was last touched when the shop set things up.
                "updated_at": midpoint if spec.key == "tcg" else _local(data.start_date, 9, 0),
            }
        )

    vendor_ids: list[str] = []
    for i, (name, account, email, phone) in enumerate(VENDORS, start=1):
        vendor_id = f"VEND_{i:03d}"
        vendor_ids.append(vendor_id)
        data.vendors.append(
            {
                "id": vendor_id,
                "name": name,
                "account_number": account,
                "email": email,
                "phone": phone,
                # Two of the seven take stock back, which is what makes "ask
                # them to take it back" a real dead-stock play for some items
                # and not for others.
                "notes": (
                    "Accepts returns on unsold stock within 90 days."
                    if i in VENDORS_ACCEPTING_RETURNS
                    else "No returns; damaged-goods credit only."
                ),
            }
        )

    specs = build_item_specs(rng)
    rng.shuffle(specs)

    variations: list[Variation] = []
    item_counter = 0
    var_counter = 0

    for spec in specs:
        item_counter += 1
        item_id = f"ITEM_{item_counter:05d}"
        created = _local(
            data.start_date + timedelta(days=rng.randint(0, HISTORY_DAYS - 60)),
            rng.randint(9, 17),
            rng.randint(0, 59),
        )
        data.items.append(
            {
                "id": item_id,
                "name": spec.name,
                "description": spec.description,
                "category_id": category_ids[spec.category_key],
                "product_type": spec.product_type,
                "is_deleted": False,
                "custom_attributes": spec.custom_attributes,
                "created_at": created,
                "updated_at": created,
            }
        )
        for var_spec in spec.variations:
            var_counter += 1
            var_id = f"VAR_{var_counter:05d}"
            variations.append(
                Variation(
                    id=var_id,
                    item_id=item_id,
                    name=var_spec.name,
                    sku=_sku(var_spec.sku_hint or "GEN", var_counter),
                    upc="".join(str(rng.randint(0, 9)) for _ in range(12)),
                    price_amount=var_spec.price_cents,
                    pricing_type=var_spec.pricing_type,
                    track_inventory=var_spec.track_inventory,
                    is_deleted=False,
                    updated_at=created,
                    category_key=spec.category_key,
                    weight=max(0.01, spec.popularity * var_spec.popularity),
                    con_affinity=spec.con_affinity,
                    series_key=spec.series_key,
                    series_index=spec.series_index,
                    item_name=spec.name,
                    cost_amount=(
                        None
                        if var_spec.cost_ratio is None or var_spec.price_cents is None
                        else int(var_spec.price_cents * var_spec.cost_ratio)
                    ),
                    vendor_id=f"VEND_{rng.choice(VENDOR_BY_CATEGORY[spec.category_key]):03d}",
                    shelf_depth=_shelf_depth(rng, spec.category_key, var_spec.price_cents),
                )
            )

    # --- quirk: a handful of variations are priced at the register ----------
    candidates = [v for v in variations if v.category_key in {"tcg_singles", "figures_scale"}]
    for variation in rng.sample(candidates, VARIABLE_PRICED_VARIATIONS):
        variation.pricing_type = "VARIABLE"
        variation.price_amount = None
        variation.cost_amount = None

    # --- quirk: ~10% of variations never had a cost recorded ----------------
    missing_cost = rng.sample(variations, int(len(variations) * SHARE_VARIATIONS_WITHOUT_COST))
    for variation in missing_cost:
        variation.cost_amount = None

    # --- quirk: ~5% of variations have never sold ---------------------------
    for variation in rng.sample(variations, int(len(variations) * SHARE_VARIATIONS_DEAD_STOCK)):
        variation.dead_stock = True
        variation.weight = 0.0

    # --- quirk: items deleted upstream that old orders still point at -------
    deletable = [v for v in variations if not v.dead_stock and v.category_key in {"apparel", "plush", "kits"}]
    retired_items: set[str] = set()
    for variation in rng.sample(deletable, min(SOFT_DELETED_ITEMS * 2, len(deletable))):
        if len(retired_items) >= SOFT_DELETED_ITEMS:
            break
        retired_items.add(variation.item_id)
    retired_at_by_item: dict[str, datetime] = {}
    for item in data.items:
        if item["id"] in retired_items:
            retired = _local(
                data.start_date + timedelta(days=rng.randint(180, HISTORY_DAYS - 140)),
                rng.randint(9, 17),
                0,
            )
            item["is_deleted"] = True
            item["updated_at"] = retired
            retired_at_by_item[item["id"]] = retired
    for variation in variations:
        if variation.item_id in retired_at_by_item:
            variation.is_deleted = True
            variation.retired_at = retired_at_by_item[variation.item_id]
            variation.updated_at = variation.retired_at

    for variation in variations:
        data.variations.append(
            {
                "id": variation.id,
                "item_id": variation.item_id,
                "name": variation.name,
                "sku": variation.sku,
                "upc": variation.upc,
                "price_amount": variation.price_amount,
                "currency": "USD",
                "pricing_type": variation.pricing_type,
                "track_inventory": variation.track_inventory,
                "is_deleted": variation.is_deleted,
                "updated_at": variation.updated_at,
            }
        )
        if variation.vendor_id is not None:
            data.vendor_info.append(
                {
                    "variation_id": variation.id,
                    "vendor_id": variation.vendor_id,
                    "unit_cost_amount": variation.cost_amount,
                }
            )

    return variations


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


def build_customers(data: Dataset, rng: random.Random, count: int = 700) -> list[dict]:
    customers: list[dict] = []
    for i in range(1, count + 1):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        created = _local(
            data.start_date + timedelta(days=rng.randint(0, HISTORY_DAYS - 1)),
            rng.randint(10, 20),
            rng.randint(0, 59),
        )
        customers.append(
            {
                "id": f"CUST_{i:05d}",
                "given_name": first,
                "family_name": last,
                "email": f"{first.lower()}.{last.lower()}{rng.randint(1, 99)}@{rng.choice(EMAIL_DOMAINS)}",
                "phone": f"+1865{rng.randint(2000000, 9999999)}",
                "reference_id": None,
                "created_at": created,
                "updated_at": created,
                # Simulation-only: how likely they are to come back.
                "_loyalty": rng.random() ** 2,
            }
        )

    # --- quirk: the same person entered twice, same email, different id -----
    duplicates = rng.sample(customers, int(count * SHARE_DUPLICATE_CUSTOMERS))
    for i, original in enumerate(duplicates, start=count + 1):
        later = original["created_at"] + timedelta(days=rng.randint(20, 300))
        if later > _local(data.end_date, 20, 0):
            later = _local(data.end_date - timedelta(days=rng.randint(1, 30)), 15, 0)
        customers.append(
            {
                "id": f"CUST_{i:05d}",
                # Nickname or a typo'd surname — the name does not match, only
                # the email does, which is exactly how dedupe has to find it.
                "given_name": original["given_name"][:3] if rng.random() < 0.5 else original["given_name"],
                "family_name": original["family_name"],
                "email": original["email"],
                "phone": original["phone"] if rng.random() < 0.5 else None,
                "reference_id": None,
                "created_at": later,
                "updated_at": later,
                "_loyalty": original["_loyalty"] * 0.5,
            }
        )

    data.customers = [{k: v for k, v in c.items() if not k.startswith("_")} for c in customers]
    return customers


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------


def _poisson(rng: random.Random, lam: float) -> int:
    """Knuth's sampler. Fine at the rates a small shop actually trades at."""
    if lam <= 0:
        return 0
    target = math.exp(-lam)
    k, product = 0, 1.0
    while True:
        product *= rng.random()
        if product <= target:
            return k
        k += 1
        if k > 400:
            return k


def pick_con_weekends(rng: random.Random, start: date, end: date) -> set[date]:
    """Six weekends a year, spread out rather than clustered."""
    saturdays = []
    day = start
    while day <= end:
        if day.weekday() == 5:
            saturdays.append(day)
        day += timedelta(days=1)
    wanted = max(1, round(len(saturdays) / 52 * 6))
    stride = max(1, len(saturdays) // wanted)
    chosen: set[date] = set()
    for i in range(wanted):
        index = min(len(saturdays) - 1, i * stride + rng.randint(0, max(0, stride - 1)))
        saturday = saturdays[index]
        chosen.add(saturday)
        chosen.add(saturday + timedelta(days=1))
    return chosen


def pick_release_weeks(rng: random.Random, start: date, end: date) -> dict[date, str]:
    """One release per set, each kicking off a visibly busier week."""
    span = (end - start).days
    releases: dict[date, str] = {}
    for i, (code, _name) in enumerate(CARD_SETS):
        # Spread the sets across the window, with a little jitter.
        offset = int(span * (i + 0.6) / (len(CARD_SETS) + 0.4)) + rng.randint(-10, 10)
        release_day = start + timedelta(days=max(20, min(span - 20, offset)))
        # Sets drop on Fridays.
        release_day += timedelta(days=(4 - release_day.weekday()) % 7)
        for d in range(7):
            releases[release_day + timedelta(days=d)] = code
    return releases


def simulate_sales(
    data: Dataset,
    rng: random.Random,
    variations: list[Variation],
    customers: list[dict],
) -> None:
    by_series: dict[tuple[str, int], Variation] = {}
    for v in variations:
        if v.series_key and v.series_index and v.name == "Paperback":
            by_series[(v.series_key, v.series_index)] = v
    supplies = [v for v in variations if v.category_key == "supplies" and v.weight > 0]
    impulse = [v for v in variations if v.category_key in IMPULSE_CATEGORIES and v.weight > 0]

    con_weekends = pick_con_weekends(rng, data.start_date, data.end_date)
    release_days = pick_release_weeks(rng, data.start_date, data.end_date)
    data.notes["con_weekends"] = sorted(str(d) for d in con_weekends)
    data.notes["release_weeks"] = sorted(
        {
            str(min(d for d, c in release_days.items() if c == code))
            for code in {c for c in release_days.values()}
        }
    )

    days = [data.start_date + timedelta(days=i) for i in range((data.end_date - data.start_date).days + 1)]

    # Expected orders per day, then normalised so the run lands near the target.
    lambdas: dict[date, float] = {}
    for day in days:
        weight = WEEKDAY_WEIGHT[day.weekday()] * MONTH_WEIGHT[day.month]
        if day in release_days:
            weight *= 1.8
        lambdas[day] = weight
    scale = TARGET_ORDERS / sum(lambdas.values())
    lambdas = {day: value * scale for day, value in lambdas.items()}

    # The week refunds spike is planted afterwards, in `scenarios`: a refund is
    # dated when the money goes back, not when the sale happened.
    data.notes["refund_spike_week"] = str(scenarios.refund_spike_week(data.end_date))

    order_counter = 0
    payment_counter = 0
    refund_counter = 0

    for day in days:
        plans: list[tuple[str, int]] = [("LOC_MAIN", _poisson(rng, lambdas[day]))]
        if day in con_weekends:
            # A booth weekend is a different, much busier shop.
            plans.append(("LOC_CON", _poisson(rng, 20 if day.weekday() == 5 else 14)))

        for location_id, count in plans:
            at_con = location_id == "LOC_CON"
            open_hour, close_hour = (9, 18) if at_con else OPEN_HOURS[day.weekday()]
            release_code = release_days.get(day)

            for _ in range(count):
                order_counter += 1
                order_id = f"ORD_{order_counter:06d}"
                placed = _local(
                    day,
                    rng.randint(open_hour, close_hour - 1),
                    rng.randint(0, 59),
                    rng.randint(0, 59),
                )

                lines = _build_basket(
                    rng,
                    by_series,
                    supplies,
                    impulse,
                    variations,
                    at_con,
                    release_code,
                    placed,
                    order_id,
                )
                if not lines:
                    order_counter -= 1
                    continue

                subtotal = sum(line["total_money"] for line in lines)
                discount_total = sum(line["total_discount_money"] for line in lines)
                tax = round(subtotal * TAX_RATE)
                tip = 0
                if rng.random() < 0.04:
                    tip = rng.choice([100, 200, 300, 500])

                canceled = rng.random() < SHARE_ORDERS_CANCELED
                state = "CANCELED" if canceled else "COMPLETED"
                if not canceled and rng.random() < 0.004:
                    state = "OPEN"

                total = subtotal + tax + tip
                customer_id = None
                if rng.random() > SHARE_ORDERS_WITHOUT_CUSTOMER:
                    customer_id = _pick_customer(rng, customers, placed)

                closed = None if state == "OPEN" else placed + timedelta(minutes=rng.randint(1, 12))
                updated = closed or placed

                data.orders.append(
                    {
                        "id": order_id,
                        "location_id": location_id,
                        "customer_id": customer_id,
                        "state": state,
                        "source": "ONLINE" if (not at_con and rng.random() < 0.06) else "POS",
                        "total_money": total,
                        "total_tax_money": tax,
                        "total_discount_money": discount_total,
                        "total_tip_money": tip,
                        "created_at": placed,
                        "updated_at": updated,
                        "closed_at": closed,
                        "version": 1,
                    }
                )
                data.line_items.extend(lines)

                if state == "CANCELED":
                    continue

                # --- payments, sometimes split across two tenders -----------
                paid = total - tip
                tenders: list[tuple[int, int]] = []
                if rng.random() < SHARE_SPLIT_PAYMENTS and paid > 500:
                    first = rng.randint(200, paid - 200)
                    tenders = [(first, 0), (paid - first, tip)]
                else:
                    tenders = [(paid, tip)]

                payment_ids: list[tuple[str, int]] = []
                for amount, tender_tip in tenders:
                    payment_counter += 1
                    payment_id = f"PAY_{payment_counter:06d}"
                    is_card = rng.random() < (0.72 if not at_con else 0.62)
                    data.payments.append(
                        {
                            "id": payment_id,
                            "order_id": order_id,
                            "amount_money": amount,
                            "tip_money": tender_tip,
                            "source_type": "CARD" if is_card else "CASH",
                            "card_brand": rng.choice(CARD_BRANDS) if is_card else None,
                            "status": "COMPLETED",
                            "created_at": closed or placed,
                        }
                    )
                    payment_ids.append((payment_id, amount))

                # --- partial refunds, days later ----------------------------
                if state == "COMPLETED" and rng.random() < SHARE_ORDERS_REFUNDED:
                    refund_counter += 1
                    payment_id, payment_amount = rng.choice(payment_ids)
                    amount = max(100, int(payment_amount * rng.uniform(0.3, 0.9)))
                    refunded_at = (closed or placed) + timedelta(days=rng.randint(1, 21))
                    if refunded_at > _local(data.end_date, 20, 0):
                        refunded_at = _local(data.end_date, 16, 0)
                    data.refunds.append(
                        {
                            "id": f"REF_{refund_counter:05d}",
                            "payment_id": payment_id,
                            "order_id": order_id,
                            "amount_money": min(amount, payment_amount),
                            "reason": rng.choice(
                                ["Damaged in box", "Wrong volume", "Changed mind", "Duplicate purchase"]
                            ),
                            "status": "COMPLETED",
                            "created_at": refunded_at,
                        }
                    )


def _pick_customer(rng: random.Random, customers: list[dict], at: datetime) -> str | None:
    """Favour customers who already exist and come back often."""
    eligible = [c for c in customers if c["created_at"] <= at]
    if not eligible:
        return None
    weights = [0.5 + c["_loyalty"] * 2.0 for c in eligible]
    return rng.choices(eligible, weights=weights, k=1)[0]["id"]


def _build_basket(
    rng: random.Random,
    by_series: dict[tuple[str, int], Variation],
    supplies: list[Variation],
    impulse: list[Variation],
    variations: list[Variation],
    at_con: bool,
    release_code: str | None,
    placed: datetime,
    order_id: str,
) -> list[dict]:
    sellable: list[Variation] = []
    weights: list[float] = []
    for v in variations:
        if v.weight <= 0:
            continue
        if v.retired_at is not None and placed >= v.retired_at:
            continue  # deleted upstream; it stops selling from then on
        weight = v.weight * (v.con_affinity if at_con else 1.0)
        if release_code and v.category_key in {"tcg_sealed", "tcg_singles"}:
            weight *= 4.0
        sellable.append(v)
        weights.append(weight)

    n_lines = rng.choices([1, 2, 3, 4, 5], weights=[46, 28, 15, 8, 3], k=1)[0]
    chosen: list[Variation] = []
    for _ in range(n_lines):
        pick = rng.choices(sellable, weights=weights, k=1)[0]
        if pick in chosen:
            continue
        chosen.append(pick)

        # Manga readers come back for the next volume in the same series.
        if pick.series_key and pick.series_index and rng.random() < 0.35:
            nxt = by_series.get((pick.series_key, pick.series_index + 1))
            if nxt and nxt.weight > 0 and nxt not in chosen:
                if nxt.retired_at is None or placed < nxt.retired_at:
                    chosen.append(nxt)

        # Singles go out the door with sleeves more often than not.
        if pick.category_key == "tcg_singles" and supplies and rng.random() < 0.30:
            sleeve = rng.choice([s for s in supplies if "Sleeve" in s.item_name] or supplies)
            if sleeve not in chosen:
                chosen.append(sleeve)

    # The counter: a ramune, a Pocky, a pin off the board. Roughly a third of
    # baskets pick one up, and it is the reason the average basket here has more
    # lines than its money would suggest.
    if impulse and rng.random() < (0.42 if at_con else 0.33):
        extra = rng.choices(impulse, weights=[v.weight for v in impulse], k=1)[0]
        if extra not in chosen:
            chosen.append(extra)

    lines: list[dict] = []
    for i, variation in enumerate(chosen):
        quantity = 1
        if rng.random() < MULTI_UNIT_CHANCE.get(variation.category_key, 0.0):
            quantity = rng.choice(
                [2, 2, 3, 3, 4] if variation.category_key in {"snacks", "drinks"} else [2, 2, 3]
            )

        if variation.pricing_type == "VARIABLE" or variation.price_amount is None:
            # Priced at the register, so the catalog cannot tell us the price.
            unit = rng.choice([1500, 2500, 4000, 7500, 12000, 20000])
        else:
            unit = variation.price_amount

        gross = unit * quantity
        discount = 0
        if rng.random() < 0.09:
            discount = int(gross * rng.choice([0.05, 0.10, 0.15, 0.20]))

        lines.append(
            {
                "uid": f"{order_id}:{i + 1}",
                "order_id": order_id,
                "catalog_object_id": variation.id,
                "name": variation.item_name,
                "variation_name": variation.name,
                "quantity": str(quantity),
                "base_price_money": unit,
                "total_discount_money": discount,
                "gross_sales_money": gross,
                "total_money": gross - discount,
                "note": None,
                "_variation_id": variation.id,
                "_quantity": quantity,
            }
        )

    # --- quirk: custom-amount lines with nothing behind them -----------------
    if rng.random() < SHARE_CUSTOM_AMOUNT_LINES * (n_lines + 1):
        amount = rng.choice([200, 350, 500, 750, 1000, 1500, 2000, 2500])
        lines.append(
            {
                "uid": f"{order_id}:{len(lines) + 1}",
                "order_id": order_id,
                "catalog_object_id": None,
                "name": rng.choice(["Misc singles", "Bulk commons", "Shop credit purchase"]),
                "variation_name": None,
                "quantity": "1",
                "base_price_money": amount,
                "total_discount_money": 0,
                "gross_sales_money": amount,
                "total_money": amount,
                "note": "Rung up at the register",
                "_variation_id": None,
                "_quantity": 1,
            }
        )

    return lines


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


def build_inventory(data: Dataset, rng: random.Random, variations: list[Variation]) -> None:
    """Receiving, waste and current counts, in that order.

    Constructed so that for every (variation, location):

        IN_STOCK = sum(received) - sum(sold) - sum(waste)

    which is the invariant the conformance suite gets to lean on.
    """
    by_id = {v.id: v for v in variations}
    order_by_id = {o["id"]: o for o in data.orders}

    # Sales per (variation, location), in time order.
    sales: dict[tuple[str, str], list[tuple[datetime, int]]] = {}
    for line in data.line_items:
        variation_id = line["_variation_id"]
        if variation_id is None:
            continue
        order = order_by_id[line["order_id"]]
        if order["state"] == "CANCELED":
            continue
        key = (variation_id, order["location_id"])
        sales.setdefault(key, []).append((order["created_at"], line["_quantity"]))
    for events in sales.values():
        events.sort()

    low_stock_ids = {
        v.id
        for v in rng.sample(
            [v for v in variations if not v.dead_stock],
            int(len(variations) * SHARE_VARIATIONS_LOW_STOCK),
        )
    }

    adjustment_counter = 0
    calculated_at = _local(data.end_date, 23, 0)

    keys = sorted({k for k in sales} | {(v.id, "LOC_MAIN") for v in variations if v.dead_stock})
    for variation_id, location_id in keys:
        variation = by_id[variation_id]
        if not variation.track_inventory:
            continue
        events = sales.get((variation_id, location_id), [])

        received_total = 0
        sold_total = 0
        waste_total = 0
        on_hand = 0
        adjustments: list[dict] = []

        if not events:
            # Dead stock: received once, never sold.
            batch = variation.shelf_depth
            received_at = _local(
                data.start_date + timedelta(days=rng.randint(10, HISTORY_DAYS - 90)),
                rng.randint(8, 11),
                rng.randint(0, 59),
            )
            adjustment_counter += 1
            adjustments.append(
                _adjustment(
                    adjustment_counter,
                    variation_id,
                    location_id,
                    None,
                    "IN_STOCK",
                    batch,
                    received_at,
                    "Shipment received",
                )
            )
            received_total = batch
            on_hand = batch
        else:
            # Walk the fortnightly receiving cycle, buying ahead of demand.
            first_sale = events[0][0]
            cycle_start = first_sale - timedelta(days=rng.randint(3, 14))
            cycle = timedelta(days=14)
            index = 0
            cycle_at = cycle_start
            end_at = _local(data.end_date, 20, 0)

            while cycle_at <= end_at:
                window_end = cycle_at + cycle * 2
                demand = sum(q for at, q in events[index:] if at < window_end)
                target = demand + variation.shelf_depth
                if on_hand < target:
                    batch = target - on_hand
                    adjustment_counter += 1
                    received_at = cycle_at.replace(
                        hour=rng.randint(8, 11), minute=rng.randint(0, 59), second=0
                    )
                    adjustments.append(
                        _adjustment(
                            adjustment_counter,
                            variation_id,
                            location_id,
                            None,
                            "IN_STOCK",
                            batch,
                            received_at,
                            "Con booth transfer" if location_id == "LOC_CON" else "Shipment received",
                        )
                    )
                    received_total += batch
                    on_hand += batch

                # Sales that fall inside this cycle.
                while index < len(events) and events[index][0] < cycle_at + cycle:
                    at, quantity = events[index]
                    adjustment_counter += 1
                    adjustments.append(
                        _adjustment(
                            adjustment_counter,
                            variation_id,
                            location_id,
                            "IN_STOCK",
                            "SOLD",
                            quantity,
                            at,
                            "Sale",
                        )
                    )
                    sold_total += quantity
                    on_hand -= quantity
                    index += 1

                # The occasional damaged box or miscount.
                if on_hand > 1 and rng.random() < 0.025:
                    quantity = 1
                    adjustment_counter += 1
                    damaged_at = cycle_at + timedelta(days=rng.randint(1, 13), hours=rng.randint(0, 9))
                    if damaged_at <= end_at:
                        adjustments.append(
                            _adjustment(
                                adjustment_counter,
                                variation_id,
                                location_id,
                                "IN_STOCK",
                                "WASTE",
                                quantity,
                                damaged_at,
                                rng.choice(["Damaged in shipping", "Shelf damage", "Cycle count correction"]),
                            )
                        )
                        waste_total += quantity
                        on_hand -= quantity

                cycle_at += cycle

            # Anything left in the tail (shouldn't happen, but be exact).
            while index < len(events):
                at, quantity = events[index]
                if on_hand < quantity:
                    adjustment_counter += 1
                    adjustments.append(
                        _adjustment(
                            adjustment_counter,
                            variation_id,
                            location_id,
                            None,
                            "IN_STOCK",
                            quantity - on_hand,
                            at - timedelta(days=1),
                            "Shipment received",
                        )
                    )
                    received_total += quantity - on_hand
                    on_hand = quantity
                adjustment_counter += 1
                adjustments.append(
                    _adjustment(
                        adjustment_counter,
                        variation_id,
                        location_id,
                        "IN_STOCK",
                        "SOLD",
                        quantity,
                        at,
                        "Sale",
                    )
                )
                sold_total += quantity
                on_hand -= quantity
                index += 1

            # Shops that are about to reorder sit at one or two on the shelf.
            if variation_id in low_stock_ids and on_hand > 3:
                wanted = on_hand - rng.choice([0, 1, 2, 3])
                # Shrink the most recent receipts first: taking units out of an
                # early shipment would send the running balance negative
                # halfway through the history.
                removed = 0
                for adjustment in reversed(adjustments):
                    if removed >= wanted:
                        break
                    if adjustment["to_state"] != "IN_STOCK" or adjustment["from_state"] is not None:
                        continue
                    have = int(adjustment["quantity"])
                    take = min(have - 1, wanted - removed)
                    if take > 0:
                        adjustment["quantity"] = str(have - take)
                        removed += take
                received_total -= removed
                on_hand -= removed

        data.adjustments.extend(adjustments)
        for state, quantity in (("IN_STOCK", on_hand), ("SOLD", sold_total), ("WASTE", waste_total)):
            if state != "IN_STOCK" and quantity == 0:
                continue
            data.counts.append(
                {
                    "variation_id": variation_id,
                    "location_id": location_id,
                    "state": state,
                    "quantity": str(quantity),
                    "calculated_at": calculated_at,
                }
            )


def _adjustment(
    counter: int,
    variation_id: str,
    location_id: str,
    from_state: str | None,
    to_state: str,
    quantity: int,
    occurred_at: datetime,
    reason: str,
) -> dict:
    return {
        "id": f"ADJ_{counter:06d}",
        "variation_id": variation_id,
        "location_id": location_id,
        "from_state": from_state,
        "to_state": to_state,
        "quantity": str(quantity),
        "occurred_at": occurred_at,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def generate(seed: int = 42, end_date: date | None = None) -> Dataset:
    rng = random.Random(seed)
    end = end_date or datetime.now(tz=UTC).date()
    data = Dataset(end_date=end, start_date=end - timedelta(days=HISTORY_DAYS))

    variations = build_catalog(data, rng)
    customers = build_customers(data, rng)
    simulate_sales(data, rng, variations, customers)
    # One Saturday loses most of its trade, before any stock history is derived
    # from those sales.
    data.notes["quiet_saturday"] = str(scenarios.trim_quiet_saturday(data, rng))
    build_inventory(data, rng, variations)
    # Shape the finished shelf so each detector has something specific to find.
    # After the inventory, because every one of these is a statement about what
    # is on the shelf now relative to what has been selling.
    scenarios.plant(data, rng, variations)

    # Strip the simulation-only keys before anything sees a row.
    for line in data.line_items:
        line.pop("_variation_id", None)
        line.pop("_quantity", None)

    return data
