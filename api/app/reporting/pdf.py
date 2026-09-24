"""A small PDF writer, for purchase orders and month-end packets.

Both documents are the same shape: a heading, a few key/value lines, one or
more tables of numbers, and a footer. That is little enough that a dependency
with a C extension and its own font machinery would be the larger part of the
feature, so this builds the file directly.

What it supports is exactly what those two documents need — the fourteen
standard PDF fonts, left- and right-aligned text, rules, and automatic page
breaks. What it does not support is images, colour beyond greys, wrapping
inside a table cell, or anything a browser would do better.
When a document needs more than this, it should be HTML that the owner prints.

The output is deterministic for the same input: no timestamps, no ids, no
dictionary ordering. That is what lets a test assert on the bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

# US Letter in points, which is what a shop's printer and their accountant's
# both expect. A4 would be one number change, but not both at once.
PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0
MARGIN = 54.0

Align = Literal["left", "right"]

# Widths of the core-font glyphs, in 1/1000 em, for the two faces used here.
# Only enough of the table to measure the characters a report can contain:
# anything outside it falls back to the average, which is close enough for
# right-alignment of numbers and never used for layout decisions.
_HELVETICA_AVERAGE = 0.5
_WIDE = set("MW@%")
_NARROW = set("iljtfIr.,:;'`|!()[]{} ")


def text_width(text: str, size: float, bold: bool = False) -> float:
    """Roughly how wide a string will print.

    Approximate on purpose. It decides where a right-aligned number starts and
    whether a heading needs a smaller size; being a few points out moves a
    column, it does not corrupt a file.
    """
    total = 0.0
    for char in text:
        if char in _WIDE:
            total += 0.9
        elif char in _NARROW:
            total += 0.28
        elif char.isupper() or char.isdigit():
            total += 0.6
        else:
            total += _HELVETICA_AVERAGE
    return total * size * (1.06 if bold else 1.0)


@dataclass(slots=True)
class Column:
    """One column of a table."""

    heading: str
    width: float
    align: Align = "left"


@dataclass(slots=True)
class _Op:
    """One drawing instruction, resolved to a page later."""

    kind: Literal["text", "rule", "space", "break"]
    text: str = ""
    size: float = 10.0
    bold: bool = False
    x: float = 0.0
    height: float = 0.0
    align: Align = "left"
    right_edge: float = 0.0
    grey: float = 0.0


class Document:
    """A page of text you append to, then ask for bytes.

    Deliberately stateful and linear — `heading`, `field`, `table`, `note` in
    the order they should appear — because that is how both documents read and
    there is no layout to solve.
    """

    def __init__(self, title: str, subtitle: str = "") -> None:
        self.title = title
        self.subtitle = subtitle
        self._ops: list[_Op] = []
        self._footer = ""
        self.heading(title, size=18, bold=True)
        if subtitle:
            self.text(subtitle, size=10, grey=0.35)
        self.rule()

    # -- content ----------------------------------------------------------

    def heading(self, text: str, *, size: float = 13, bold: bool = True) -> None:
        self.space(6)
        self._ops.append(_Op("text", text=text, size=size, bold=bold, height=size * 1.3))

    def text(self, text: str, *, size: float = 10, bold: bool = False, grey: float = 0.0) -> None:
        self._ops.append(
            _Op("text", text=text, size=size, bold=bold, height=size * 1.45, grey=grey)
        )

    def field(self, label: str, value: str) -> None:
        """A label and its value on one line, the label in grey."""
        self._ops.append(_Op("text", text=f"{label}:", size=10, grey=0.4, height=0))
        self._ops.append(
            _Op(
                "text",
                text=value,
                size=10,
                x=MARGIN + 110,
                height=14,
            )
        )

    def rule(self, *, grey: float = 0.75) -> None:
        self._ops.append(_Op("rule", height=10, grey=grey))

    def space(self, height: float = 10) -> None:
        self._ops.append(_Op("space", height=height))

    def page_break(self) -> None:
        self._ops.append(_Op("break"))

    def note(self, text: str) -> None:
        for line in _wrap(text, PAGE_WIDTH - 2 * MARGIN, 9):
            self.text(line, size=9, grey=0.35)

    def footer(self, text: str) -> None:
        self._footer = text

    def table(
        self,
        columns: list[Column],
        rows: list[list[str]],
        *,
        total: list[str] | None = None,
    ) -> None:
        """A table with a header and an optional bold total row."""
        self.space(4)
        self._table_header(columns)
        for row in rows:
            self._table_row(columns, row, size=9.5)
        if total is not None:
            self.rule(grey=0.6)
            self._table_row(columns, total, size=10, bold=True)
        self.space(6)

    def _table_header(self, columns: list[Column]) -> None:
        self._table_row(columns, [c.heading for c in columns], size=9, bold=True, grey=0.35)
        self.rule(grey=0.6)

    def _table_row(
        self,
        columns: list[Column],
        row: list[str],
        *,
        size: float,
        bold: bool = False,
        grey: float = 0.0,
    ) -> None:
        x = MARGIN
        for index, column in enumerate(columns):
            value = row[index] if index < len(row) else ""
            self._ops.append(
                _Op(
                    "text",
                    text=value,
                    size=size,
                    bold=bold,
                    x=x,
                    height=0,
                    align=column.align,
                    right_edge=x + column.width,
                    grey=grey,
                )
            )
            x += column.width
        self._ops.append(_Op("space", height=size * 1.5))

    # -- output -----------------------------------------------------------

    def render(self) -> bytes:
        pages: list[list[tuple[float, float, _Op]]] = [[]]
        y = PAGE_HEIGHT - MARGIN
        bottom = MARGIN + 30

        for op in self._ops:
            if op.kind == "break" or (op.height and y - op.height < bottom):
                pages.append([])
                y = PAGE_HEIGHT - MARGIN
                if op.kind == "break":
                    continue
            if op.kind in ("text", "rule"):
                pages[-1].append((op.x or MARGIN, y, op))
            y -= op.height

        return _serialise(
            [
                _page_stream(page, self._footer, number + 1, len(pages))
                for number, page in enumerate(pages)
            ]
        )


def _page_stream(
    page: list[tuple[float, float, _Op]], footer: str, number: int, total: int
) -> str:
    parts: list[str] = []
    grey = 0.0
    for x, y, op in page:
        if op.grey != grey:
            # 0 is black and 1 is white in PDF's grey space, which is the same
            # way `grey` reads here: 0.4 is a muted label, 0.75 is a hairline.
            parts.append(f"{op.grey:.2f} {op.grey:.2f} {op.grey:.2f} rg")
            parts.append(f"{op.grey:.2f} {op.grey:.2f} {op.grey:.2f} RG")
            grey = op.grey
        if op.kind == "rule":
            parts.append(
                f"0.6 w {MARGIN:.1f} {y + 4:.1f} m {PAGE_WIDTH - MARGIN:.1f} {y + 4:.1f} l S"
            )
            continue
        start = x
        if op.align == "right":
            start = op.right_edge - text_width(op.text, op.size, op.bold) - 4
        font = "F2" if op.bold else "F1"
        parts.append(
            f"BT /{font} {op.size:.1f} Tf {start:.1f} {y:.1f} Td ({_escape(op.text)}) Tj ET"
        )

    parts.append("0.6 0.6 0.6 rg")
    tail = f"{footer}    page {number} of {total}" if footer else f"page {number} of {total}"
    parts.append(f"BT /F1 8.0 Tf {MARGIN:.1f} {MARGIN - 12:.1f} Td ({_escape(tail)}) Tj ET")
    return "\n".join(parts)


def _escape(text: str) -> str:
    """PDF string escaping, plus a fallback for anything outside Latin-1.

    The core fonts are single-byte, so a character they cannot show is
    transliterated rather than written as a byte the reader would render as
    something else entirely. An em dash becoming a hyphen on a purchase order
    is fine; a mojibake product name is not.
    """
    swaps = {
        "—": "-",
        "–": "-",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "…": "...",
        " ": " ",
    }
    out = []
    for char in text:
        char = swaps.get(char, char)
        if char in "()\\":
            out.append("\\" + char)
        elif ord(char) < 128:
            out.append(char)
        else:
            try:
                char.encode("latin-1")
            except UnicodeEncodeError:
                out.append("?")
            else:
                out.append(f"\\{ord(char):03o}")
    return "".join(out)


def _wrap(text: str, width: float, size: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if text_width(candidate, size) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _serialise(streams: list[str]) -> bytes:
    """Assemble the objects and the xref table.

    Object numbering: 1 catalog, 2 pages, 3 and 4 the two fonts, then one page
    object and one content stream per page.
    """
    objects: list[bytes] = []

    def add(body: str) -> int:
        objects.append(body.encode("latin-1", errors="replace"))
        return len(objects)

    page_count = len(streams)
    first_page_obj = 5
    kids = " ".join(f"{first_page_obj + 2 * i} 0 R" for i in range(page_count))

    add("<< /Type /Catalog /Pages 2 0 R >>")
    add(f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>")
    add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")

    for index, stream in enumerate(streams):
        content_obj = first_page_obj + 2 * index + 1
        add(
            f"<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 {PAGE_WIDTH:.0f} {PAGE_HEIGHT:.0f}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
            f"/Contents {content_obj} 0 R >>"
        )
        encoded = stream.encode("latin-1", errors="replace")
        add(f"<< /Length {len(encoded)} >>\nstream\n{stream}\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("latin-1")
        out += body
        out += b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)


def money(value: Decimal | None, currency_symbol: str = "$") -> str:
    """A money cell. Blank, not zero, when there is no figure."""
    if value is None:
        return "—"
    return f"{currency_symbol}{value:,.2f}"


def quantity(value: Decimal) -> str:
    return format(value.normalize(), "f")
