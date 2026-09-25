"""The shop.

Animanga Knox is an anime, manga and hobby store on Market Square in Knoxville,
with a main store and a convention booth. The catalog is deliberately made of
real products at real US retail prices — actual manga runs and their publishers,
actual figure lines, actual Bandai kit numbers, actual Japanese snack and soda
imports, actual card games and sealed configurations — because a fixture that
quotes $9.99 for a Viz volume and $161.64 for a Pokemon booster box is one an
owner can sanity-check against their own shelf.

The shop itself, its staff, its customers, the vendor account numbers and
eighteen months of transactions are invented. Product names and prices belong to
their respective rights holders and are used here the way a shop's own inventory
list uses them.

This module is only names, prices and shapes. The simulation that turns it into
eighteen months of messy transaction history lives in `generate.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SHOP_NAME = "Animanga Knox"
SHOP_TIMEZONE = "America/New_York"
CURRENCY = "USD"
# Knoxville, TN: 7% state + 2.25% local.
TAX_RATE = 0.0925


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Series:
    """A manga run the shop shelves, and how deep it shelves it."""

    key: str
    title: str
    publisher: str
    # Volumes carried on the shelf, counting from one. A shop does not keep a
    # hundred volumes of One Piece facing out; it keeps the front of the run and
    # orders the rest in.
    volumes: int
    price_cents: int
    # How reliably readers come back for the next volume. High for the long
    # shonen runs, low for the one-and-done literary ones.
    loyalty: float
    popularity: float


@dataclass(frozen=True)
class Product:
    """One shelf product: a real item at a real price."""

    name: str
    price_cents: int
    popularity: float = 1.0
    # Multiplier on how well this sells at the con booth rather than the store.
    con_affinity: float = 1.0
    brand: str | None = None
    franchise: str | None = None


@dataclass(frozen=True)
class CardGame:
    key: str
    name: str
    # (set code, set name). Release weeks are drawn from these.
    sets: tuple[tuple[str, str], ...]
    # (configuration, price in cents, popularity)
    sealed: tuple[tuple[str, int, float], ...]


@dataclass(frozen=True)
class Single:
    """A graded single card, priced at what it books for in near mint."""

    name: str
    set_name: str
    game_key: str
    price_cents: int


# ---------------------------------------------------------------------------
# Manga
# ---------------------------------------------------------------------------

MANGA_SERIES: tuple[Series, ...] = (
    Series("onepiece", "One Piece", "VIZ Media", 16, 999, 0.58, 2.2),
    Series("jjk", "Jujutsu Kaisen", "VIZ Media", 14, 999, 0.56, 2.1),
    Series("demonslayer", "Demon Slayer: Kimetsu no Yaiba", "VIZ Media", 14, 999, 0.54, 2.0),
    Series("chainsawman", "Chainsaw Man", "VIZ Media", 15, 1199, 0.55, 1.9),
    Series("mha", "My Hero Academia", "VIZ Media", 14, 999, 0.50, 1.6),
    Series("spyfamily", "Spy x Family", "VIZ Media", 12, 999, 0.52, 1.7),
    Series("aot", "Attack on Titan", "Kodansha", 12, 1099, 0.49, 1.5),
    Series("dandadan", "Dandadan", "VIZ Media", 10, 1299, 0.51, 1.6),
    Series("frieren", "Frieren: Beyond Journey's End", "VIZ Media", 11, 999, 0.50, 1.5),
    Series("kaiju8", "Kaiju No. 8", "VIZ Media", 10, 999, 0.48, 1.4),
    Series("sakamoto", "Sakamoto Days", "VIZ Media", 10, 999, 0.46, 1.3),
    Series("bluelock", "Blue Lock", "Kodansha", 12, 1299, 0.47, 1.3),
    Series("oshinoko", "Oshi no Ko", "Yen Press", 10, 1500, 0.45, 1.2),
    Series("tokyorev", "Tokyo Revengers", "Seven Seas", 10, 1399, 0.42, 1.0),
    Series("hellsparadise", "Hell's Paradise: Jigokuraku", "VIZ Media", 9, 1299, 0.40, 0.9),
    Series("dungeonmeshi", "Delicious in Dungeon", "Yen Press", 10, 1500, 0.44, 1.1),
    Series("witchhat", "Witch Hat Atelier", "Kodansha", 9, 1299, 0.41, 0.9),
    Series("vinland", "Vinland Saga", "Kodansha", 8, 2299, 0.38, 0.7),
    Series("berserk", "Berserk Deluxe Edition", "Dark Horse", 6, 4999, 0.34, 0.6),
    Series("fmaful", "Fullmetal Alchemist: Fullmetal Edition", "VIZ Media", 6, 1999, 0.36, 0.7),
    Series("deathnote", "Death Note Black Edition", "VIZ Media", 6, 1499, 0.35, 0.8),
    Series("naruto3in1", "Naruto 3-in-1 Edition", "VIZ Media", 8, 1499, 0.44, 1.1),
)

# Volume one of the bigger runs is also shelved in the publisher's special
# edition, which is a second variation on the same item.
MANGA_SPECIAL_EDITION_SURCHARGE = 800


# ---------------------------------------------------------------------------
# Trading cards
# ---------------------------------------------------------------------------

CARD_GAMES: tuple[CardGame, ...] = (
    CardGame(
        key="ptcg",
        name="Pokemon TCG",
        sets=(
            ("SV08", "Surging Sparks"),
            ("SVPE", "Prismatic Evolutions"),
            ("SV09", "Journey Together"),
            ("SV10", "Destined Rivals"),
        ),
        sealed=(
            ("Booster Pack", 449, 3.6),
            ("Booster Bundle", 2699, 1.5),
            ("Elite Trainer Box", 4999, 1.2),
            ("Booster Box", 16164, 0.7),
        ),
    ),
    CardGame(
        key="optcg",
        name="One Piece Card Game",
        sets=(
            ("OP09", "Emperors in the New World"),
            ("OP10", "Royal Blood"),
            ("OP11", "A Fist of Divine Speed"),
        ),
        sealed=(
            ("Booster Pack", 429, 2.4),
            ("Starter Deck", 1599, 1.1),
            ("Booster Box", 10799, 0.6),
        ),
    ),
    CardGame(
        key="dbsfw",
        name="Dragon Ball Super Card Game Fusion World",
        sets=(
            ("FB03", "Raging Roar"),
            ("FB04", "Blazing Aura"),
        ),
        sealed=(
            ("Booster Pack", 449, 1.6),
            ("Starter Deck", 2199, 0.8),
            ("Booster Box", 9599, 0.4),
        ),
    ),
    CardGame(
        key="weiss",
        name="Weiss Schwarz",
        sets=(
            ("WS-HOL", "hololive production"),
            ("WS-AOT", "Attack on Titan"),
        ),
        sealed=(
            ("Booster Pack", 699, 1.0),
            ("Trial Deck", 2499, 0.5),
            ("Booster Box", 9999, 0.3),
        ),
    ),
)

# Every set, flat. Release weeks are drawn from this.
CARD_SETS: tuple[tuple[str, str], ...] = tuple(
    (code, name) for game in CARD_GAMES for code, name in game.sets
)

GAME_NAMES: dict[str, str] = {game.key: game.name for game in CARD_GAMES}

SINGLES: tuple[Single, ...] = (
    # Pokemon: the chase cards people walk in and ask for by name.
    Single("Umbreon ex (SIR)", "Prismatic Evolutions", "ptcg", 42000),
    Single("Espeon ex (SIR)", "Prismatic Evolutions", "ptcg", 11500),
    Single("Sylveon ex (SIR)", "Prismatic Evolutions", "ptcg", 9500),
    Single("Budew (Reverse Holo)", "Prismatic Evolutions", "ptcg", 900),
    Single("Pikachu ex (SIR)", "Surging Sparks", "ptcg", 24000),
    Single("Latias ex (SIR)", "Surging Sparks", "ptcg", 3200),
    Single("Milotic ex (SIR)", "Surging Sparks", "ptcg", 2600),
    Single("Charizard ex (SIR)", "Obsidian Flames", "ptcg", 12000),
    Single("Iono (SIR)", "Paldean Fates", "ptcg", 3800),
    Single("Gardevoir ex (Double Rare)", "Paldea Evolved", "ptcg", 1400),
    Single("Terapagos ex (SIR)", "Stellar Crown", "ptcg", 5600),
    Single("Lillie's Clefairy ex (SIR)", "Journey Together", "ptcg", 8800),
    Single("N's Zoroark ex (SIR)", "Journey Together", "ptcg", 6400),
    Single("Team Rocket's Mewtwo ex (SIR)", "Destined Rivals", "ptcg", 7200),
    Single("Cynthia's Garchomp ex (Double Rare)", "Destined Rivals", "ptcg", 1200),
    # One Piece: secret rares, and the leaders that hold a deck together.
    Single("Shanks (SEC)", "Emperors in the New World", "optcg", 8500),
    Single("Monkey D. Luffy (Leader Parallel)", "Emperors in the New World", "optcg", 2400),
    Single("Kaido (SR)", "Emperors in the New World", "optcg", 1800),
    Single("Boa Hancock (SR)", "Royal Blood", "optcg", 2200),
    Single("Roronoa Zoro (SR Alt Art)", "Royal Blood", "optcg", 3400),
    Single("Gol D. Roger (SEC)", "Royal Blood", "optcg", 6800),
    Single("Sabo (Leader)", "A Fist of Divine Speed", "optcg", 900),
    Single("Portgas D. Ace (SR Alt Art)", "A Fist of Divine Speed", "optcg", 4200),
    Single("Nami (R Parallel)", "A Fist of Divine Speed", "optcg", 700),
    # Dragon Ball Fusion World.
    Single("Son Goku (SCR)", "Raging Roar", "dbsfw", 4500),
    Single("Vegeta (SR)", "Raging Roar", "dbsfw", 1600),
    Single("Broly (SCR)", "Blazing Aura", "dbsfw", 5200),
    Single("Son Gohan (SR)", "Blazing Aura", "dbsfw", 1100),
    Single("Frieza (R)", "Blazing Aura", "dbsfw", 500),
    # Weiss Schwarz.
    Single("Gawr Gura (SP Signed)", "hololive production", "weiss", 12000),
    Single("Hoshimachi Suisei (SSP)", "hololive production", "weiss", 6600),
    Single("Usada Pekora (RRR)", "hololive production", "weiss", 1900),
    Single("Mikasa Ackerman (SP)", "Attack on Titan", "weiss", 5400),
    Single("Levi (RRR)", "Attack on Titan", "weiss", 2300),
    Single("Eren Yeager (RR)", "Attack on Titan", "weiss", 800),
)

CARD_CONDITIONS: tuple[tuple[str, float], ...] = (
    ("NM", 1.00),  # near mint, full book price
    ("LP", 0.80),
    ("MP", 0.60),
)
CARD_CONDITION_WEIGHT = {"NM": 1.0, "LP": 0.6, "MP": 0.35}


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

PRIZE_FIGURES: tuple[Product, ...] = (
    Product("Banpresto Grandista Nero - Uzumaki Naruto", 4499, 1.0, 1.8, "Banpresto", "Naruto"),
    Product("Banpresto Q Posket - Nezuko Kamado", 2799, 1.3, 1.9, "Banpresto", "Demon Slayer"),
    Product(
        "Ichiban Kuji Jujutsu Kaisen - Satoru Gojo (Prize A)",
        3999,
        1.2,
        1.9,
        "Bandai Spirits",
        "Jujutsu Kaisen",
    ),
    Product("Banpresto Match Makers - Son Goku", 2999, 1.0, 1.7, "Banpresto", "Dragon Ball Z"),
    Product("Banpresto DXF Grandline Men - Roronoa Zoro", 2499, 1.1, 1.8, "Banpresto", "One Piece"),
    Product("Banpresto Vibration Stars - Power", 2799, 1.2, 1.8, "Banpresto", "Chainsaw Man"),
    Product("Banpresto Exceed Creative - Anya Forger", 3499, 1.1, 1.7, "Banpresto", "Spy x Family"),
    Product("Banpresto Amazing Heroes - Shoto Todoroki", 2499, 0.9, 1.6, "Banpresto", "My Hero Academia"),
    Product("Funko Pop! Animation: Kakashi Hatake", 1299, 1.4, 2.1, "Funko", "Naruto"),
    Product("Funko Pop! Animation: Zenitsu Agatsuma", 1299, 1.3, 2.1, "Funko", "Demon Slayer"),
    Product("Funko Pop! Animation: Denji", 1299, 1.2, 2.0, "Funko", "Chainsaw Man"),
)

CHIBI_FIGURES: tuple[Product, ...] = (
    Product("Nendoroid Satoru Gojo", 6499, 1.0, 1.5, "Good Smile Company", "Jujutsu Kaisen"),
    Product("Nendoroid Tanjiro Kamado", 5999, 0.9, 1.5, "Good Smile Company", "Demon Slayer"),
    Product("Nendoroid Anya Forger", 6499, 1.0, 1.5, "Good Smile Company", "Spy x Family"),
    Product("Nendoroid Hatsune Miku: Symphony 2023", 6999, 0.7, 1.4, "Good Smile Company", "Vocaloid"),
    Product("figma Levi Ackerman", 8999, 0.6, 1.2, "Max Factory", "Attack on Titan"),
    Product("POP UP PARADE Frieren", 4999, 1.1, 1.6, "Good Smile Company", "Frieren"),
    Product("POP UP PARADE Denji", 4499, 0.9, 1.6, "Good Smile Company", "Chainsaw Man"),
    Product("POP UP PARADE Makima", 4999, 1.0, 1.6, "Good Smile Company", "Chainsaw Man"),
)

SCALE_FIGURES: tuple[Product, ...] = (
    Product("Good Smile Company 1/7 Scale - Frieren", 21999, 0.34, 1.3, "Good Smile Company", "Frieren"),
    Product(
        "Good Smile Company 1/7 Scale - Makima",
        24999,
        0.32,
        1.3,
        "Good Smile Company",
        "Chainsaw Man",
    ),
    Product(
        "Kotobukiya ARTFX J 1/8 Scale - Tanjiro Kamado",
        16999,
        0.33,
        1.3,
        "Kotobukiya",
        "Demon Slayer",
    ),
    Product(
        "Kotobukiya Bishoujo 1/7 Scale - Mikasa Ackerman",
        17999,
        0.28,
        1.2,
        "Kotobukiya",
        "Attack on Titan",
    ),
    Product("Alter 1/7 Scale - Rem", 27999, 0.26, 1.2, "Alter", "Re:ZERO"),
    Product(
        "Megahouse Portrait.Of.Pirates - Monkey D. Luffy Gear 5",
        29999,
        0.30,
        1.4,
        "Megahouse",
        "One Piece",
    ),
    Product(
        "Good Smile Company 1/7 Scale - Hatsune Miku Racing 2024",
        23999,
        0.22,
        1.1,
        "Good Smile Company",
        "Vocaloid",
    ),
    Product("Aniplex+ 1/7 Scale - Satoru Gojo", 31999, 0.24, 1.1, "Aniplex", "Jujutsu Kaisen"),
)


# ---------------------------------------------------------------------------
# Model kits
# ---------------------------------------------------------------------------

MODEL_KITS: tuple[Product, ...] = (
    Product("Entry Grade 1/144 RX-78-2 Gundam", 999, 1.3, 1.2, "Bandai", "Gundam"),
    Product("HG 1/144 RX-78-2 Gundam (Revive)", 1899, 1.2, 1.1, "Bandai", "Gundam"),
    Product("HG 1/144 Gundam Barbatos Lupus Rex", 2799, 1.0, 1.0, "Bandai", "Gundam"),
    Product("HG 1/144 Char's Zaku II", 1899, 0.9, 1.0, "Bandai", "Gundam"),
    Product("HG 1/144 Gundam Aerial", 2299, 1.0, 1.0, "Bandai", "Gundam"),
    Product("HG 1/144 Wing Gundam Zero EW", 2499, 0.8, 0.9, "Bandai", "Gundam"),
    Product("RG 1/144 RX-78-2 Gundam Ver.2.0", 3999, 0.8, 0.8, "Bandai", "Gundam"),
    Product("RG 1/144 MSN-04 Sazabi", 4499, 0.6, 0.7, "Bandai", "Gundam"),
    Product("MG 1/100 Sazabi Ver.Ka", 8999, 0.3, 0.5, "Bandai", "Gundam"),
    Product("MG 1/100 Freedom Gundam Ver.2.0", 6499, 0.4, 0.5, "Bandai", "Gundam"),
    Product("PG Unleashed 1/60 RX-78-2 Gundam", 27999, 0.12, 0.3, "Bandai", "Gundam"),
    Product("Figure-rise Standard Son Gohan", 2999, 0.7, 0.9, "Bandai", "Dragon Ball Z"),
    Product("Figure-rise Standard Nezuko Kamado", 3499, 0.7, 0.9, "Bandai", "Demon Slayer"),
    Product("30MM bEXM-15 Portanova", 1499, 0.5, 0.7, "Bandai", "30 Minutes Missions"),
)


# ---------------------------------------------------------------------------
# Plush
# ---------------------------------------------------------------------------

PLUSH: tuple[Product, ...] = (
    Product("Pochita Plush 12in", 2999, 1.3, 2.0, "Great Eastern", "Chainsaw Man"),
    Product("Nezuko Kamado Plush 8in", 2499, 1.2, 1.9, "Great Eastern", "Demon Slayer"),
    Product("Totoro Plush 13in", 3499, 1.1, 1.6, "Studio Ghibli", "My Neighbor Totoro"),
    Product("Kodama Plush 6in", 1899, 0.8, 1.6, "Studio Ghibli", "Princess Mononoke"),
    Product("Calcifer Plush 9in", 2799, 0.9, 1.6, "Studio Ghibli", "Howl's Moving Castle"),
    Product("Jiji Plush 8in", 2299, 0.9, 1.6, "Studio Ghibli", "Kiki's Delivery Service"),
    Product("Pikachu Plush 8in", 1999, 1.4, 2.1, "Jazwares", "Pokemon"),
    Product("Eevee Plush 8in", 1999, 1.3, 2.1, "Jazwares", "Pokemon"),
    Product("Snorlax Plush 14in", 3999, 0.8, 1.7, "Jazwares", "Pokemon"),
    Product("Panda Plush 10in", 2799, 0.7, 1.7, "Great Eastern", "Jujutsu Kaisen"),
    Product("Anya Forger Plush 9in", 2699, 1.0, 1.8, "Great Eastern", "Spy x Family"),
    Product("Tony Tony Chopper Plush 8in", 2399, 0.9, 1.8, "Great Eastern", "One Piece"),
    Product("Kirby Plush 10in", 2499, 1.0, 1.7, "Sanei", "Kirby"),
    Product("Gudetama Plush 9in", 2199, 0.7, 1.6, "Sanrio", "Sanrio"),
)


# ---------------------------------------------------------------------------
# Apparel
# ---------------------------------------------------------------------------

APPAREL: tuple[Product, ...] = (
    Product("Naruto Shippuden Akatsuki Cloud Tee", 2499, 1.0, 1.6, None, "Naruto"),
    Product("Dragon Ball Z Goku Kanji Tee", 2499, 0.9, 1.6, None, "Dragon Ball Z"),
    Product("Attack on Titan Survey Corps Tee", 2499, 0.9, 1.6, None, "Attack on Titan"),
    Product("Studio Ghibli Totoro Tee", 2799, 0.8, 1.4, None, "My Neighbor Totoro"),
    Product("Sailor Moon Crystal Tee", 2699, 0.6, 1.4, None, "Sailor Moon"),
    Product("Animanga Knox House Logo Tee", 2299, 0.7, 1.5, None, None),
    Product("One Piece Straw Hat Crew Hoodie", 5499, 0.5, 1.2, None, "One Piece"),
    Product("Jujutsu Kaisen Sukuna Hoodie", 5999, 0.5, 1.2, None, "Jujutsu Kaisen"),
)
APPAREL_SIZES: tuple[str, ...] = ("S", "M", "L", "XL", "2XL")
APPAREL_SIZE_WEIGHT = {"S": 0.5, "M": 1.0, "L": 1.0, "XL": 0.7, "2XL": 0.4}


# ---------------------------------------------------------------------------
# Wall art, desk mats, pins, magnets
# ---------------------------------------------------------------------------

WALL_ART: tuple[Product, ...] = (
    Product("Attack on Titan Survey Corps Poster (24x36)", 999, 1.2, 1.5, "Trends", "Attack on Titan"),
    Product("Chainsaw Man Key Art Poster (24x36)", 999, 1.3, 1.5, "Trends", "Chainsaw Man"),
    Product("Jujutsu Kaisen Gojo Poster (24x36)", 999, 1.4, 1.6, "Trends", "Jujutsu Kaisen"),
    Product("Spirited Away No-Face Poster (24x36)", 1199, 1.0, 1.3, "Trends", "Spirited Away"),
    Product("My Neighbor Totoro Poster (18x24)", 899, 0.9, 1.3, "Trends", "My Neighbor Totoro"),
    Product("Berserk Guts Poster (24x36)", 1199, 0.7, 1.3, "Trends", "Berserk"),
    Product("One Piece Wanted Poster Set (5pc)", 1499, 1.1, 1.8, "Great Eastern", "One Piece"),
    Product(
        "Demon Slayer Tanjiro & Nezuko Wall Scroll (33x44)",
        2999,
        0.8,
        1.6,
        "Great Eastern",
        "Demon Slayer",
    ),
    Product("Naruto Hidden Leaf Wall Scroll (33x44)", 2799, 0.7, 1.6, "Great Eastern", "Naruto"),
    Product("Hatsune Miku Wall Scroll (33x44)", 2899, 0.6, 1.5, "Great Eastern", "Vocaloid"),
)

DESK_MATS: tuple[Product, ...] = (
    Product("Gojo Satoru Desk Mat (XL 35x16)", 2999, 0.9, 1.4, None, "Jujutsu Kaisen"),
    Product("Hatsune Miku Desk Mat (XL 35x16)", 3499, 0.7, 1.3, None, "Vocaloid"),
    Product("Totoro Desk Mat (Large 24x12)", 2499, 0.6, 1.2, None, "My Neighbor Totoro"),
    Product("Nezuko Mousepad (Standard 10x8)", 1499, 1.0, 1.5, None, "Demon Slayer"),
    Product("Pochita Mousepad (Standard 10x8)", 1499, 1.0, 1.5, None, "Chainsaw Man"),
    Product("Pikachu Mousepad (Standard 10x8)", 1699, 0.9, 1.5, None, "Pokemon"),
)

PINS: tuple[Product, ...] = (
    Product("Enamel Pin - Pochita", 899, 1.5, 2.2, None, "Chainsaw Man"),
    Product("Enamel Pin - Gojo Satoru", 999, 1.6, 2.2, None, "Jujutsu Kaisen"),
    Product("Enamel Pin - Totoro", 899, 1.2, 1.9, None, "My Neighbor Totoro"),
    Product("Enamel Pin - Hidden Leaf Headband", 999, 1.1, 2.0, None, "Naruto"),
    Product("Enamel Pin Set - Akatsuki Rings (3pc)", 1499, 0.8, 1.9, None, "Naruto"),
    Product("Blind Box Pin - Pokemon Series 1", 699, 1.7, 2.3, None, "Pokemon"),
    Product("Acrylic Keychain - Anya Forger", 799, 1.4, 2.2, None, "Spy x Family"),
    Product("Rubber Keychain - Monkey D. Luffy", 699, 1.3, 2.2, None, "One Piece"),
)

MAGNETS: tuple[Product, ...] = (
    Product("Die-Cut Magnet - Pikachu", 449, 1.5, 2.1, None, "Pokemon"),
    Product("Die-Cut Magnet - Nezuko Chibi", 499, 1.3, 2.0, None, "Demon Slayer"),
    Product("Die-Cut Magnet - Calcifer", 499, 1.1, 1.9, None, "Howl's Moving Castle"),
    Product("Magnet - Totoro Acorn", 499, 1.0, 1.9, None, "My Neighbor Totoro"),
    Product("Magnet Set - One Piece Jolly Rogers (4pc)", 999, 0.9, 1.9, None, "One Piece"),
    Product("Magnet Set - Demon Slayer Hashira (6pc)", 1299, 0.7, 1.8, None, "Demon Slayer"),
)


# ---------------------------------------------------------------------------
# Snacks and drinks
# ---------------------------------------------------------------------------

SNACKS: tuple[Product, ...] = (
    Product("Glico Pocky - Chocolate", 249, 3.4, 2.4, "Glico", None),
    Product("Glico Pocky - Strawberry", 249, 3.1, 2.4, "Glico", None),
    Product("Glico Pocky - Matcha", 299, 2.6, 2.3, "Glico", None),
    Product("Glico Pocky - Cookies & Cream", 249, 2.2, 2.2, "Glico", None),
    Product("Glico Pretz - Salad", 249, 1.4, 2.0, "Glico", None),
    Product("Morinaga Hi-Chew - Original Mix (Bag)", 449, 2.4, 2.2, "Morinaga", None),
    Product("Morinaga Hi-Chew - Tropical Mix (Bag)", 449, 2.0, 2.2, "Morinaga", None),
    Product("Morinaga Ramune Candy", 199, 1.8, 2.1, "Morinaga", None),
    Product("KitKat Japan - Matcha (Mini 12pc)", 999, 1.6, 1.9, "Nestle Japan", None),
    Product("KitKat Japan - Strawberry (Mini 12pc)", 999, 1.3, 1.9, "Nestle Japan", None),
    Product("Meiji Hello Panda - Chocolate", 299, 2.0, 2.1, "Meiji", None),
    Product("Meiji Apollo Strawberry Chocolate", 299, 1.7, 2.0, "Meiji", None),
    Product("Yaokin Umaibo - Corn Potage", 99, 2.8, 2.5, "Yaokin", None),
    Product("Calbee Kappa Ebisen Shrimp Chips", 349, 1.5, 1.9, "Calbee", None),
    Product("Nissin Raoh Tonkotsu Ramen (Bowl)", 449, 1.2, 1.1, "Nissin", None),
)

DRINKS: tuple[Product, ...] = (
    Product("Ramune Soda - Original (200ml)", 299, 3.6, 2.6, "Hata Kosen", None),
    Product("Ramune Soda - Strawberry (200ml)", 299, 3.0, 2.6, "Hata Kosen", None),
    Product("Ramune Soda - Melon (200ml)", 299, 2.8, 2.5, "Hata Kosen", None),
    Product("Ramune Soda - Lychee (200ml)", 299, 2.3, 2.5, "Hata Kosen", None),
    Product("Ramune Soda - Blue Hawaii (200ml)", 299, 2.1, 2.5, "Hata Kosen", None),
    Product("Calpico - Original (500ml)", 349, 2.0, 2.2, "Calpis", None),
    Product("Pocari Sweat (500ml)", 329, 1.6, 2.1, "Otsuka", None),
    Product("Fanta Melon Soda - Japan (500ml)", 349, 1.8, 2.2, "Coca-Cola Japan", None),
    Product("Ito En Oi Ocha Green Tea (500ml)", 299, 1.4, 2.0, "Ito En", None),
    Product("Suntory Boss Coffee - Latte (Can)", 349, 1.5, 1.8, "Suntory", None),
    Product("Sangaria Ramu Cola (Can)", 249, 1.2, 1.9, "Sangaria", None),
    Product("Kirin Gogo no Kocha Milk Tea (Can)", 299, 1.3, 1.9, "Kirin", None),
)


# ---------------------------------------------------------------------------
# Card supplies
# ---------------------------------------------------------------------------

SUPPLIES: tuple[Product, ...] = (
    Product("Dragon Shield Matte Sleeves, 100ct", 1199, 3.4, 1.4, "Dragon Shield", None),
    Product("Ultra Pro Eclipse Matte Sleeves, 100ct", 999, 2.6, 1.3, "Ultra Pro", None),
    Product("Ultra Pro Perfect Fit Sleeves, 100ct", 499, 2.2, 1.3, "Ultra Pro", None),
    Product("Ultra Pro Toploaders, 25ct", 699, 1.6, 1.3, "Ultra Pro", None),
    Product("BCW Card Saver I, 50ct", 899, 0.9, 1.1, "BCW", None),
    Product("Ultimate Guard Deck Case 100+", 1999, 1.1, 1.2, "Ultimate Guard", None),
    Product("Vault X 9-Pocket Exo-Tec Binder", 2499, 1.0, 1.0, "Vault X", None),
    Product("Ultra Pro Playmat - Shop Art", 2999, 0.6, 1.2, "Ultra Pro", None),
    Product("Manga Shelf Dividers, 6ct", 1499, 0.4, 0.4, None, None),
    Product("Acrylic Figure Display Case, Small", 3499, 0.3, 0.6, None, None),
)


# ---------------------------------------------------------------------------
# Vendors
# ---------------------------------------------------------------------------

# name, account number, orders address, phone. A POS supplier record holds
# contact details; it does not hold a lead time or a case size, which is why
# those are entered by the owner on our side rather than synced. The account
# numbers, addresses and phone numbers are invented — `.example` is reserved by
# RFC 2606 and cannot be delivered to.
VENDORS: tuple[tuple[str, str, str, str], ...] = (
    (
        "Penguin Random House Publisher Services",
        "PRHPS-40118",
        "orders@prhps.example",
        "800-555-0118",
    ),
    ("Diamond Comic Distributors", "DCD-22907", "newaccounts@diamondcomics.example", "410-555-0229"),
    ("GTS Distribution", "GTS-77310", "orders@gtsdistribution.example", "425-555-0773"),
    ("Alliance Game Distributors", "AGD-10044", "orders@alliancegames.example", "410-555-1004"),
    ("Bluefin Brands", "BLU-58231", "trade@bluefinbrands.example", "760-555-5823"),
    ("Great Eastern Entertainment", "GEE-31145", "sales@geanimation.example", "626-555-3114"),
    ("JFC International", "JFC-66920", "knoxville@jfc.example", "615-555-6692"),
)

# Which vendors take stock back, by position in VENDORS above. That is what
# makes "ask them to take it back" a real dead-stock play for some items and not
# for others.
VENDORS_ACCEPTING_RETURNS = (1, 4)


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


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
    CategorySpec("figures_chibi", "Nendoroids & Figma", parent="figures"),
    CategorySpec("figures_scale", "Scale Figures", parent="figures"),
    CategorySpec("tcg", "Trading Cards"),
    CategorySpec("tcg_sealed", "Sealed Product", parent="tcg"),
    CategorySpec("tcg_singles", "Singles", parent="tcg"),
    CategorySpec("kits", "Model Kits"),
    CategorySpec("plush", "Plush"),
    CategorySpec("apparel", "Apparel"),
    CategorySpec("wall_art", "Posters & Wall Scrolls"),
    CategorySpec("desk", "Desk Mats & Mousepads"),
    CategorySpec("pins", "Pins & Keychains"),
    CategorySpec("magnets", "Magnets"),
    CategorySpec("snacks", "Snacks & Candy"),
    CategorySpec("drinks", "Drinks"),
    CategorySpec("supplies", "Card Supplies"),
)

CATEGORY_RENAMED = {"key": "tcg", "old_name": "TCG", "new_name": "Trading Cards"}

# What the shop pays as a fraction of retail, by category. Sealed trading card
# product is the thin one on purpose: that is the real shape of the trade, and
# it is what makes "you are busiest on the product you make least on" a true
# statement about this shop rather than an invented one.
COST_RATIO: dict[str, float] = {
    "manga": 0.60,
    "figures_prize": 0.62,
    "figures_chibi": 0.64,
    "figures_scale": 0.66,
    "tcg_sealed": 0.76,
    "tcg_singles": 0.55,
    "kits": 0.65,
    "plush": 0.50,
    "apparel": 0.45,
    "wall_art": 0.45,
    "desk": 0.50,
    "pins": 0.40,
    "magnets": 0.40,
    "snacks": 0.58,
    "drinks": 0.58,
    "supplies": 0.55,
}


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


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
    LocationSpec("main", "LOC_MAIN", "Animanga Knox - Market Square", SHOP_TIMEZONE),
    LocationSpec("con", "LOC_CON", "Animanga Knox - Con Booth", SHOP_TIMEZONE, con_booth=True),
)


# ---------------------------------------------------------------------------
# Item shapes the generator fills in
# ---------------------------------------------------------------------------


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
