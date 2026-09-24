"""The mapping language: YAML in, canonical field values out.

This is what turns "my inventory is a spreadsheet" from a bespoke integration
into a config file somebody reviews over a coffee. It is deliberately small —
a handful of field sources and a fixed set of transforms — because every
feature added here is a feature the person reviewing a drafted mapping has to
understand before they can trust it.

A field is one of four **sources**:

    {column: "Sale Amt"}                 a cell in the current sheet
    {constant: "in_store"}               a fixed value
    {concat: [{column: A}, {constant: "-"}, {column: B}]}
    {lookup: {sheet:, key:, value:, match_on:}}   a value from another sheet

followed by an optional list of **transforms**, applied in order:

    {column: "Unit Price", transforms: [parse_money]}
    {column: "Date", transforms: [{parse_date: {formats: ["%m/%d/%y"]}}]}
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

WHITESPACE = re.compile(r"\s+")
MONEY_STRIP = re.compile(r"[^\d.\-]")

# What people type in a spreadsheet when they mean "nothing". Treating these as
# blank is right; treating actual prose as blank is how a mis-mapped column
# becomes a table of silent nulls.
BLANK_MARKERS = {"-", "--", "n/a", "n\\a", "na", "none", "nil", "tbd", "?"}


class MappingError(ValueError):
    """The mapping file asks for something impossible. Message names the field."""


# ---------------------------------------------------------------------------
# Header handling
# ---------------------------------------------------------------------------


def normalise_header(value: Any) -> str:
    """Headers are matched case- and whitespace-insensitively.

    Real exports contain `"Sale Amt "`. A mapping keyed on the exact string
    works until someone re-exports and the trailing space moves.
    """
    return WHITESPACE.sub(" ", str(value or "").strip()).casefold()


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def t_trim(value: Any) -> str:
    """Strip the ends and collapse runs of internal whitespace."""
    return WHITESPACE.sub(" ", _text(value).strip())


def t_lower(value: Any) -> str:
    """Trim, then lowercase."""
    return t_trim(value).lower()


def t_upper(value: Any) -> str:
    """Trim, then uppercase."""
    return t_trim(value).upper()


def t_fold_key(value: Any) -> str:
    """The matching key: trimmed, collapsed, case-folded.

    `" IRON GUTTER  #3"` and `"Iron Gutter #3"` are the same item, and a
    spreadsheet will contain both.
    """
    return WHITESPACE.sub(" ", _text(value).strip()).casefold()


def t_parse_money(value: Any) -> Decimal | None:
    """`"$1,234.50"` -> `Decimal("1234.50")`. Blank stays None, not zero."""
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))
    text = _text(value).strip()
    if not text or text.casefold() in BLANK_MARKERS:
        # Genuinely empty, or one of the things people type to mean empty.
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = MONEY_STRIP.sub("", text)
    if cleaned in ("", "-", "."):
        # Not blank, but no number in it either. Returning None here would turn
        # a whole mis-mapped column into silent nulls; better to say so now.
        raise MappingError(f"{value!r} does not look like money")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise MappingError(f"{value!r} does not look like money") from exc
    return -amount if negative else amount


def t_cents_to_dollars(value: Any) -> Decimal | None:
    """Integer cents to Decimal dollars, for sources that store whole cents."""
    if value is None or value == "":
        return None
    return Decimal(int(value)) / 100


def t_to_decimal(value: Any) -> Decimal | None:
    """A plain number, as an exact Decimal. Blank stays None."""
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(_text(value).strip())
    except InvalidOperation as exc:
        raise MappingError(f"{value!r} is not a number") from exc


def t_to_int(value: Any) -> int | None:
    """A whole number. Blank stays None."""
    parsed = t_to_decimal(value)
    return None if parsed is None else int(parsed)


def t_hash_id(value: Any, prefix: str = "") -> str | None:
    """A stable synthetic id for a source that has none.

    The same input always produces the same id, which is what lets a re-import
    update rows instead of duplicating them. Derived from the folded value, so
    a change of capitalisation upstream does not mint a new product.
    """
    key = t_fold_key(value)
    if not key:
        return None
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}{digest}" if prefix else digest


def t_parse_date(value: Any, formats: list[str] | None = None) -> date | None:
    """Spreadsheet dates are sometimes real dates and usually strings."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value).strip()
    if not text:
        return None
    for pattern in formats or ["%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"]:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise MappingError(f"{text!r} does not match any of the date formats {formats or '(defaults)'}")


def t_split_name(value: Any, part: str = "first") -> str | None:
    """`"Avery Bell"` -> first `"Avery"`, last `"Bell"`."""
    cleaned = t_trim(value)
    if not cleaned:
        return None
    if "," in cleaned:  # "Bell, Avery"
        last, _, first = cleaned.partition(",")
        return t_trim(first) if part == "first" else t_trim(last)
    pieces = cleaned.split(" ")
    if len(pieces) == 1:
        return pieces[0] if part == "first" else None
    return pieces[0] if part == "first" else " ".join(pieces[1:])


def t_default(value: Any, to: Any = None) -> Any:
    """Substitute a value only when the cell is blank. Use sparingly."""
    return to if value in (None, "") else value


def t_at_time(value: Any, at: str = "12:00", timezone: str = "UTC") -> datetime | None:
    """A date becomes an instant.

    A file export records the day but not the clock. Placing the sale at midday
    *in the shop's timezone* keeps it on the right local day, which is the day
    every report buckets by. Midnight would sit on a boundary and move.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        hour, _, minute = at.partition(":")
        moment = datetime.combine(value, time(int(hour), int(minute or 0)))
    else:
        raise MappingError(f"at_time needs a date, got {type(value).__name__}")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo(timezone))
    return moment.astimezone(ZoneInfo("UTC"))


TRANSFORMS: dict[str, Callable[..., Any]] = {
    "trim": t_trim,
    "lower": t_lower,
    "upper": t_upper,
    "fold_key": t_fold_key,
    "parse_money": t_parse_money,
    "cents_to_dollars": t_cents_to_dollars,
    "to_decimal": t_to_decimal,
    "to_int": t_to_int,
    "hash_id": t_hash_id,
    "parse_date": t_parse_date,
    "split_name": t_split_name,
    "default": t_default,
    "at_time": t_at_time,
}


def apply_transforms(value: Any, steps: list[Any], where: str) -> Any:
    for step in steps:
        name: str
        options: dict[str, Any]
        if isinstance(step, str):
            name, options = step, {}
        elif isinstance(step, dict) and len(step) == 1:
            key, raw_options = next(iter(step.items()))
            name, options = str(key), dict(raw_options or {})
        else:
            raise MappingError(f"{where}: {step!r} is not a transform")
        function = TRANSFORMS.get(name)
        if function is None:
            raise MappingError(
                f"{where}: unknown transform {name!r}. Known: {', '.join(sorted(TRANSFORMS))}"
            )
        try:
            value = function(value, **options)
        except MappingError:
            raise
        except TypeError as exc:
            raise MappingError(f"{where}: {name} does not take {options!r}") from exc
    return value


# ---------------------------------------------------------------------------
# The spec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SheetSpec:
    name: str
    match: str
    header_row: int = 1


@dataclass(frozen=True)
class EntitySpec:
    name: str
    source_sheet: str | None = None
    constants: list[dict[str, Any]] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)
    lines: dict[str, Any] | None = None
    distinct_by: Any | None = None
    group_by: Any | None = None
    skip_when_blank: list[str] = field(default_factory=list)


@dataclass
class MappingSpec:
    path: Path
    version: int
    source: str
    display_name: str
    workbook: Path
    capabilities: dict[str, bool]
    settings: dict[str, Any]
    sheets: dict[str, SheetSpec]
    entities: dict[str, EntitySpec]
    notes: list[str] = field(default_factory=list)

    @property
    def timezone(self) -> str:
        return str(self.settings.get("timezone", "UTC"))

    @property
    def assumed_time(self) -> str:
        return str(self.settings.get("assumed_time_of_day", "12:00"))

    @property
    def skip_rows_matching(self) -> tuple[str, ...]:
        return tuple(str(value).casefold() for value in self.settings.get("skip_rows_matching", []))


def load(path: str | Path, repo_root: Path | None = None) -> MappingSpec:
    location = Path(path)
    if repo_root and not location.is_absolute():
        location = repo_root / location
    if not location.exists():
        raise MappingError(f"no mapping file at {location}")

    raw = yaml.safe_load(location.read_text(encoding="utf-8")) or {}
    for required in ("source", "workbook", "capabilities", "entities"):
        if required not in raw:
            raise MappingError(f"{location.name} is missing the {required!r} section")

    workbook = Path(raw["workbook"])
    if repo_root and not workbook.is_absolute():
        workbook = repo_root / workbook

    sheets = {
        name: SheetSpec(name=name, match=spec["match"], header_row=int(spec.get("header_row", 1)))
        for name, spec in (raw.get("sheets") or {}).items()
    }

    entities: dict[str, EntitySpec] = {}
    for name, spec in raw["entities"].items():
        spec = spec or {}
        entities[name] = EntitySpec(
            name=name,
            source_sheet=spec.get("from"),
            constants=list(spec.get("constant") or []),
            fields=spec.get("fields") or {},
            lines=spec.get("lines"),
            distinct_by=spec.get("distinct_by"),
            group_by=spec.get("group_by"),
            skip_when_blank=list(spec.get("skip_when_blank") or []),
        )
        if entities[name].source_sheet and entities[name].source_sheet not in sheets:
            raise MappingError(
                f"entity {name!r} reads sheet {entities[name].source_sheet!r}, "
                f"which is not declared under `sheets`"
            )

    return MappingSpec(
        path=location,
        version=int(raw.get("version", 1)),
        source=raw["source"],
        display_name=raw.get("display_name", raw["source"]),
        workbook=workbook,
        capabilities=dict(raw["capabilities"]),
        settings=dict(raw.get("settings") or {}),
        sheets=sheets,
        entities=entities,
        notes=list(raw.get("notes") or []),
    )
