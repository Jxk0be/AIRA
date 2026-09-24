"""The mapping language, and the spreadsheet it was built for.

The transform tests need no database and no file — they are the unit layer for
the pieces every future customer's mapping will lean on. The adapter tests read
the Panel & Pawn export and check the specific traps in
`sources/spreadsheet_shop/QUIRKS.md`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.canonical.enums import Channel, OrderStatus
from app.config import REPO_ROOT
from app.connectors.adapters.mapping import MappingAdapter, modal_row
from app.connectors.adapters.mapping import Row as SheetRow
from app.connectors.adapters.mapping_spec import (
    MappingError,
    apply_transforms,
    normalise_header,
)
from app.connectors.adapters.mapping_spec import load as load_spec

MAPPING = "mappings/panel_and_pawn.yaml"
CONFIG = {"mapping_file": MAPPING}


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$1,234.50", Decimal("1234.50")),
        ("$4.99", Decimal("4.99")),
        ("1234.5", Decimal("1234.5")),
        ("  $12.00  ", Decimal("12.00")),
        ("($5.00)", Decimal("-5.00")),
        (12.5, Decimal("12.5")),
        ("", None),
        (None, None),
    ],
)
def test_parse_money(raw: object, expected: Decimal | None) -> None:
    """Blank money stays None. A missing cost is information, not a zero."""
    assert apply_transforms(raw, ["parse_money"], "test") == expected


def test_parse_money_rejects_nonsense_rather_than_guessing() -> None:
    with pytest.raises(MappingError, match="does not look like money"):
        apply_transforms("about twelve dollars", ["parse_money"], "test")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3/7/25", date(2025, 3, 7)),
        ("12/25/2025", date(2025, 12, 25)),
        ("2026-01-05", date(2026, 1, 5)),
        (datetime(2026, 2, 3, 14, 30), date(2026, 2, 3)),
    ],
)
def test_parse_date(raw: object, expected: date) -> None:
    steps = [{"parse_date": {"formats": ["%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d"]}}]
    assert apply_transforms(raw, steps, "test") == expected


def test_parse_date_says_so_when_nothing_matches() -> None:
    with pytest.raises(MappingError, match="does not match any of the date formats"):
        apply_transforms("7 March 2025", [{"parse_date": {"formats": ["%m/%d/%y"]}}], "test")


def test_fold_key_makes_four_spellings_one_item() -> None:
    """The single most important transform for a hand-typed source."""
    spellings = ["Iron Gutter #3", " Iron Gutter #3", "IRON GUTTER #3", "iron  gutter  #3"]
    folded = {apply_transforms(s, ["fold_key"], "test") for s in spellings}
    assert len(folded) == 1


def test_hash_id_is_stable_and_case_insensitive() -> None:
    """Ids have to survive a re-export, or every sync duplicates the catalog."""
    first = apply_transforms("Iron Gutter #3", ["fold_key", {"hash_id": {}}], "test")
    again = apply_transforms("  IRON GUTTER  #3 ", ["fold_key", {"hash_id": {}}], "test")
    assert first == again
    assert apply_transforms("Iron Gutter #4", ["fold_key", {"hash_id": {}}], "test") != first

    prefixed = apply_transforms(
        "Iron Gutter #3", ["fold_key", {"hash_id": {"prefix": "var_"}}], "t"
    )
    assert str(prefixed).startswith("var_")


def test_hash_id_of_nothing_is_nothing() -> None:
    assert apply_transforms("", ["fold_key", {"hash_id": {}}], "test") is None


def test_at_time_puts_a_dateless_sale_on_the_right_local_day() -> None:
    """Midday local, not midnight: midnight sits on a boundary and moves."""
    result = apply_transforms(
        date(2026, 3, 7),
        [{"at_time": {"at": "12:00", "timezone": "America/New_York"}}],
        "test",
    )
    assert isinstance(result, datetime)
    assert result.tzinfo is not None
    assert result.utcoffset() == datetime.now(UTC).utcoffset()  # returned in UTC
    assert result.astimezone(UTC).hour in (16, 17)  # noon EST/EDT
    assert result.astimezone(UTC).date() == date(2026, 3, 7)


def test_split_name() -> None:
    assert apply_transforms("Avery Bell", [{"split_name": {"part": "first"}}], "t") == "Avery"
    assert apply_transforms("Avery Bell", [{"split_name": {"part": "last"}}], "t") == "Bell"
    assert apply_transforms("Bell, Avery", [{"split_name": {"part": "first"}}], "t") == "Avery"


def test_unknown_transform_names_the_alternatives() -> None:
    with pytest.raises(MappingError, match="unknown transform"):
        apply_transforms("x", ["parse_monies"], "test")


def test_headers_match_despite_stray_whitespace_and_case() -> None:
    """The export really does contain a header called `"Sale Amt "`."""
    assert normalise_header("Sale Amt ") == normalise_header("sale amt")
    assert normalise_header("  Item   Desc ") == normalise_header("Item Desc")


def test_modal_row_picks_the_most_common_spelling() -> None:
    rows = [
        SheetRow(sheet="s", number=n, cells={"item": value})
        for n, value in enumerate(["Iron Gutter #3", "IRON GUTTER #3", "Iron Gutter #3"])
    ]
    assert modal_row(rows).cells["item"] == "Iron Gutter #3"


def test_modal_row_never_lets_a_blank_win() -> None:
    rows = [
        SheetRow(sheet="s", number=1, cells={"category": ""}),
        SheetRow(sheet="s", number=2, cells={"category": ""}),
        SheetRow(sheet="s", number=3, cells={"category": "Comics"}),
    ]
    assert modal_row(rows).cells["category"] == "Comics"


# ---------------------------------------------------------------------------
# The adapter against the real export
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> MappingAdapter:
    spec = load_spec(MAPPING, REPO_ROOT)
    if not spec.workbook.exists():
        pytest.skip(f"no export at {spec.workbook}; run `python tasks.py export`")
    return MappingAdapter(CONFIG)


async def test_capabilities_come_from_the_yaml_not_from_code(adapter: MappingAdapter) -> None:
    """The whole point of this adapter: the customer's file decides."""
    info = adapter.describe()
    assert info.capabilities.has_customers is False
    assert info.capabilities.supports_incremental is False
    assert info.capabilities.has_costs is True
    assert info.notes, "a source with this many caveats should carry notes"


async def test_healthcheck_reports_what_it_found(adapter: MappingAdapter) -> None:
    health = await adapter.healthcheck()
    assert health.ok
    assert "sales" in health.detail and "inventory" in health.detail


async def test_the_totals_row_is_not_a_sale(adapter: MappingAdapter) -> None:
    """Every export ends with one, and counting it roughly doubles revenue."""
    names = [
        line.name_snapshot.lower()
        async for fetched in adapter.iter_orders()
        for line in fetched.record.lines
    ]
    assert names, "no orders came back at all"
    assert not [name for name in names if "total" in name]


async def test_one_product_per_item_however_it_was_typed(adapter: MappingAdapter) -> None:
    products = [fetched.record async for fetched in adapter.iter_products()]
    assert products

    ids = [product.external_id for product in products]
    assert len(ids) == len(set(ids)), "the same item became two products"

    folded = {product.name.strip().casefold() for product in products}
    assert len(folded) == len(products), "two products differ only by capitalisation"

    # And the display name is a tidy spelling, not a shouted one.
    assert not [p for p in products if p.name.isupper() and len(p.name) > 4]


async def test_orders_reconcile_and_carry_the_shops_vocabulary(adapter: MappingAdapter) -> None:
    seen = 0
    async for fetched in adapter.iter_orders():
        order = fetched.record
        seen += 1
        assert order.status is OrderStatus.COMPLETED
        assert order.channel is Channel.IN_STORE
        assert order.placed_at.tzinfo is not None
        expected = sum((line.quantity * line.unit_price for line in order.lines), Decimal("0"))
        assert order.subtotal == expected
        assert order.total == order.subtotal - order.discount_total
        if seen > 200:
            break
    assert seen > 100


async def test_line_ids_are_unique_even_without_receipt_numbers(
    adapter: MappingAdapter,
) -> None:
    """2% of rows have no receipt number. Two of those selling the same item
    would collide on one id, and one line would silently vanish."""
    ids: list[str] = []
    async for fetched in adapter.iter_orders():
        ids.extend(line.external_id for line in fetched.record.lines)
    assert len(ids) == len(set(ids)), f"{len(ids) - len(set(ids))} line ids collided"


async def test_missing_costs_stay_missing(adapter: MappingAdapter) -> None:
    variants = [fetched.record async for fetched in adapter.iter_variants()]
    with_cost = [v for v in variants if v.cost is not None]
    assert with_cost, "the cost lookup across sheets found nothing at all"
    assert len(with_cost) < len(variants), "every item has a cost; the fixture lost a quirk"
    assert not [v for v in variants if v.cost == 0], "a missing cost became a zero cost"


async def test_a_source_without_customers_yields_none(adapter: MappingAdapter) -> None:
    """The base class default, doing its job: no entity, no records, no guesses."""
    assert [record async for record in adapter.iter_customers()] == []
    assert [record async for record in adapter.iter_refunds()] == []
    assert [record async for record in adapter.iter_inventory_movements()] == []


async def test_every_record_carries_the_row_it_came_from(adapter: MappingAdapter) -> None:
    """`raw_records` is what makes a number arguable against the source."""
    async for fetched in adapter.iter_orders():
        assert fetched.raw, "an order arrived with no raw payload"
        assert "_rows" in fetched.raw
        break
