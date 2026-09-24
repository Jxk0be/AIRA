"""The invented shop.

Tsundoku & Tabletop is an anime, manga and hobby store in Knoxville with a main
store and a convention booth. Every series, character, game and product name
below is made up; nothing here references real intellectual property.

This module is only names and shapes. The simulation that turns it into 18
months of messy transaction history lives in `generate.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SHOP_NAME = "Tsundoku & Tabletop"
SHOP_TIMEZONE = "America/New_York"
CURRENCY = "USD"
# Knoxville, TN: 7% state + 2.25% local.
TAX_RATE = 0.0925


@dataclass(frozen=True)
class Series:
    key: str
    title: str
    volumes: int
    price_cents: int
    # How reliably readers come back for the next volume. High for the long
    # shonen-style runs, low for the one-and-done literary ones.
    loyalty: float
    popularity: float


MANGA_SERIES: tuple[Series, ...] = (
    Series("hollow", "Hollow Lantern", 14, 1299, 0.55, 1.9),
    Series("petal", "Petal Circuit", 9, 1399, 0.45, 1.3),
    Series("saltwater", "Saltwater Samurai", 12, 1199, 0.50, 1.5),
    Series("ninetails", "Nine Tails of Ash", 15, 1299, 0.58, 2.1),
    Series("cinder", "Cinder & Clockwork", 8, 1499, 0.40, 1.0),
    Series("moonlit", "Moonlit Cram School", 11, 1199, 0.42, 1.1),
    Series("gravity", "Gravity Bento", 7, 1099, 0.35, 0.8),
    Series("kettle", "The Kettle Knight", 10, 1299, 0.44, 1.2),
    Series("vending", "Starlit Vending Machine", 6, 1599, 0.30, 0.6),
    Series("ronin", "Ronin Barista", 9, 1249, 0.46, 1.4),
    Series("crane", "Paper Crane Protocol", 12, 1349, 0.48, 1.0),
    Series("thunder", "Thunder Loom", 5, 1699, 0.33, 0.5),
)

# The shop's own house TCG. Sets release a few times a year and each release
# week is a visible spike in the data.
TCG_NAME = "Astral Clash"
TCG_SETS: tuple[tuple[str, str], ...] = (
    ("AC01", "Tidebreak"),
    ("AC02", "Ember Accord"),
    ("AC03", "Veil of Nine"),
    ("AC04", "Gilded Static"),
)

SEALED_PRODUCTS: tuple[tuple[str, int, float], ...] = (
    ("Booster Pack", 499, 3.2),
    ("Booster Box", 12999, 0.9),
    ("Starter Deck", 2199, 1.4),
    ("Collector Bundle", 5499, 0.7),
)

# Card names for the singles. Combined with a set and a condition grade.
CARD_NAMES: tuple[str, str] = (
    "Lanternkeeper",
    "Tidewalker Sage",
    "Ash Herald",
    "Clockwork Vandal",
    "Saltwind Duelist",
    "Paper Crane Envoy",
    "Vending Oracle",
    "Kettle Paladin",
    "Static Weaver",
    "Nine-Tailed Archivist",
    "Bento Alchemist",
    "Loomshadow",
    "Gilded Roost",
    "Veiled Cartographer",
    "Emberwake Ronin",
)
CARD_CONDITIONS: tuple[tuple[str, float], ...] = (
    ("NM", 1.00),  # near mint, full price
    ("LP", 0.80),
    ("MP", 0.60),
)

FIGURE_CHARACTERS: tuple[str, ...] = (
    "Yuzuha",
    "Tsukine",
    "Rei Amagasa",
    "Little Ash",
    "Captain Brine",
    "Mochi the Fox",
    "Clockwork Anna",
    "Barista Jin",
    "Crane Sister",
    "Loomkeeper Hana",
    "Vending Ghost",
    "Kettle Squire",
)

PLUSH_CHARACTERS: tuple[str, ...] = (
    "Mochi the Fox",
    "Little Ash",
    "Kettle Squire",
    "Vending Ghost",
    "Crane Sister",
    "Bento Cat",
    "Tidepool Slime",
)

MODEL_KIT_LINES: tuple[tuple[str, int], ...] = (
    ("Ashfall Frame", 3499),
    ("Tidebreaker Mk II", 4599),
    ("Lantern Sentinel", 2899),
    ("Static Crawler", 5299),
)

APPAREL_DESIGNS: tuple[tuple[str, int], ...] = (
    ("Hollow Lantern Crest Tee", 2499),
    ("Nine Tails of Ash Tee", 2499),
    ("Astral Clash Logo Hoodie", 5499),
    ("Ronin Barista Apron", 3299),
    ("Tsundoku & Tabletop House Tee", 2299),
)
APPAREL_SIZES: tuple[str, ...] = ("S", "M", "L", "XL")

SUPPLIES: tuple[tuple[str, int, float], ...] = (
    ("Matte Card Sleeves, 100ct", 799, 4.0),
    ("Perfect Fit Sleeves, 100ct", 499, 2.2),
    ("Deck Box, Magnetic", 1999, 1.1),
    ("9-Pocket Binder", 2499, 0.9),
    ("Toploaders, 25ct", 599, 1.5),
    ("Playmat, House Art", 2999, 0.6),
    ("Manga Shelf Dividers, 6ct", 1499, 0.4),
    ("Figure Display Case, Small", 3499, 0.3),
)

# name, account number, orders address, phone. A POS supplier record holds
# contact details; it does not hold a lead time or a case size, which is why
# those are entered by the owner on our side rather than synced.
VENDORS: tuple[tuple[str, str, str, str], ...] = (
    ("Blue Kettle Distribution", "BK-40118", "orders@bluekettledist.example", "865-555-0118"),
    ("Riverbend Hobby Supply", "RH-22907", "sales@riverbendhobby.example", "423-555-0229"),
    ("Neon Ward Imports", "NW-77310", "hello@neonward.example", "865-555-0773"),
    ("Southeast Game Wholesale", "SGW-10044", "orders@segamewholesale.example", "770-555-1004"),
    ("Paper Lantern Books", "PLB-58231", "trade@paperlanternbooks.example", "865-555-5823"),
)


@dataclass
class CategorySpec:
    key: str
    name: str
    parent: str | None = None


# One of these gets renamed partway through the shop's history: the category
# with key "tcg" was called "TCG" until the rename and is "Trading Cards" now.
# Same id throughout, which is exactly what makes it a trap for naive mapping.
CATEGORIES: tuple[CategorySpec, ...] = (
    CategorySpec("manga", "Manga"),
    CategorySpec("figures", "Figures"),
    CategorySpec("figures_prize", "Prize Figures", parent="figures"),
    CategorySpec("figures_scale", "Scale Figures", parent="figures"),
    CategorySpec("tcg", "Trading Cards"),
    CategorySpec("tcg_sealed", "Sealed Product", parent="tcg"),
    CategorySpec("tcg_singles", "Singles", parent="tcg"),
    CategorySpec("kits", "Model Kits"),
    CategorySpec("plush", "Plush"),
    CategorySpec("apparel", "Apparel"),
    CategorySpec("supplies", "Supplies"),
)

CATEGORY_RENAMED = {"key": "tcg", "old_name": "TCG", "new_name": "Trading Cards"}


@dataclass
class LocationSpec:
    key: str
    id: str
    name: str
    timezone: str
    status: str = "ACTIVE"
    # Con booths only trade on a handful of weekends a year.
    con_booth: bool = False


LOCATIONS: tuple[LocationSpec, ...] = (
    LocationSpec("main", "LOC_MAIN", "Tsundoku & Tabletop — Gay St", SHOP_TIMEZONE),
    LocationSpec("con", "LOC_CON", "Con Booth", SHOP_TIMEZONE, con_booth=True),
)


@dataclass
class ItemSpec:
    """One catalog item, before ids and history are attached."""

    name: str
    category_key: str
    product_type: str
    variations: list[VariationSpec] = field(default_factory=list)
    description: str | None = None
    custom_attributes: dict[str, str] = field(default_factory=dict)
    series_key: str | None = None
    # Position within a series, so "readers buy the next volume" is expressible.
    series_index: int | None = None
    popularity: float = 1.0
    # Multiplier on how well this sells at the con booth rather than the store.
    con_affinity: float = 1.0


@dataclass
class VariationSpec:
    name: str | None
    price_cents: int | None
    pricing_type: str = "FIXED"
    # Fraction of retail the shop pays. None means the shop never recorded one.
    cost_ratio: float | None = 0.55
    track_inventory: bool = True
    sku_hint: str = ""
    popularity: float = 1.0
