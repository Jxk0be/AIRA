"""Uploaded prose: policies, FAQs, event schedules.

The half of a shop's knowledge that never appears in a POS export. "Do you buy
back singles?" and "when is the next draft night?" are the questions an owner
gets asked most and the ones no sales table can answer, so they arrive as files
and live in `documents`.

Re-uploading the same filename updates that document rather than adding a
second copy. A shop that fixes a typo in its returns policy and uploads it
again should end up with one policy, not two that disagree.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.db import table_of

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text"}
PDF_SUFFIXES = {".pdf"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | PDF_SUFFIXES

CONTENT_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".text": "text/plain",
    ".pdf": "application/pdf",
}


class UnsupportedDocument(ValueError):
    """A file type we cannot read. Carries a message fit to show a shop owner."""


@dataclass
class LoadedDocument:
    title: str
    filename: str
    content_type: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _title_from(text: str, filename: str) -> str:
    """The document's own first heading, or a tidied-up filename."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
        if line:
            return line[:200]
    return Path(filename).stem.replace("-", " ").replace("_", " ").title()


def _pdf_text(data: bytes) -> str:
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()


def read_bytes(filename: str, data: bytes) -> LoadedDocument:
    """Parse an upload. The caller has already decided it may be stored."""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise UnsupportedDocument(f"cannot read {filename!r}. Supported: {supported}")

    text = _pdf_text(data) if suffix in PDF_SUFFIXES else data.decode("utf-8", errors="replace")
    text = text.strip()
    if not text:
        raise UnsupportedDocument(
            f"{filename!r} has no readable text in it. A scanned PDF needs OCR first."
        )

    return LoadedDocument(
        title=_title_from(text, filename),
        filename=Path(filename).name,
        content_type=CONTENT_TYPES.get(suffix, "text/plain"),
        content=text,
    )


def read_file(path: Path) -> LoadedDocument:
    return read_bytes(path.name, path.read_bytes())


def read_directory(directory: Path) -> list[LoadedDocument]:
    """Every readable file in a folder, in a stable order."""
    return [
        read_file(path)
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    ]


async def store(
    session: AsyncSession, tenant_id: uuid.UUID, document: LoadedDocument
) -> tuple[uuid.UUID, bool]:
    """Insert or update by filename. Returns the id and whether it is new."""
    table = table_of(t.Document)
    existing = (
        await session.execute(
            select(table.c.id).where(
                table.c.tenant_id == tenant_id,
                table.c.filename == document.filename,
                table.c.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(tz=UTC)
    if existing is not None:
        await session.execute(
            update(table)
            .where(table.c.id == existing)
            .values(
                title=document.title,
                content_type=document.content_type,
                content=document.content,
                metadata=document.metadata,
                uploaded_at=now,
                updated_at=now,
            )
        )
        return existing, False

    new_id = uuid.uuid4()
    session.add(
        t.Document(
            id=new_id,
            tenant_id=tenant_id,
            title=document.title,
            filename=document.filename,
            content_type=document.content_type,
            content=document.content,
            doc_metadata=document.metadata,
            uploaded_at=now,
        )
    )
    return new_id, True
