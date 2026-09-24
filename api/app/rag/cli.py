"""Embed a tenant's catalogue and documents from the command line.

    python -m app.rag.cli --tenant tsundoku
    python -m app.rag.cli --tenant tsundoku --load sources/documents/tsundoku
    python -m app.rag.cli --tenant tsundoku --force

Ordinary runs only embed what changed, so this is cheap to repeat. `--force`
re-embeds everything and is what `app.rag.reembed` is.

A module of its own rather than a `__main__` inside `ingest`: `app.rag`'s
package import already pulls `ingest` in, and running an already-imported
module with `-m` makes Python warn about executing it twice.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import select

from app.canonical import tables as t
from app.db import dispose_engine, get_sessionmaker
from app.rag import documents as docs
from app.rag.embeddings import EmbeddingError
from app.rag.ingest import IngestReport, ingest_tenant

log = logging.getLogger(__name__)


def print_report(report: IngestReport) -> None:
    print(f"\n  {report.tenant} — ingest ({report.model})\n")
    for line in report.lines():
        print(f"    {line}")
    for error in report.errors:
        print(f"    FAILED {error}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Embed a tenant's catalogue and documents.")
    parser.add_argument("--tenant", required=True, help="tenant slug, e.g. tsundoku")
    parser.add_argument("--load", help="a folder of .md/.txt/.pdf files to upload first")
    parser.add_argument("--force", action="store_true", help="re-embed everything")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


async def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    try:
        async with get_sessionmaker()() as session:
            tenant = (
                await session.execute(select(t.Tenant).where(t.Tenant.slug == args.tenant))
            ).scalar_one_or_none()
            if tenant is None:
                print(f"  no tenant {args.tenant!r}")
                return 1

            if args.load:
                folder = Path(args.load)
                # Reading a handful of local files from a CLI: blocking on disk
                # here is the whole point, not an accident.
                try:
                    loaded = await asyncio.to_thread(docs.read_directory, folder)
                except (OSError, docs.UnsupportedDocument) as exc:
                    print(f"  cannot read {folder}: {exc}")
                    return 1
                for document in loaded:
                    _, created = await docs.store(session, tenant.id, document)
                    print(f"  {'added' if created else 'updated'}  {document.filename}")
                await session.flush()

            try:
                report = await ingest_tenant(session, tenant, force=args.force)
            except EmbeddingError as exc:
                await session.rollback()
                print(f"\n  embedding failed: {exc}")
                return 1

            await session.commit()
            print_report(report)
            return 0 if report.ok else 1
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
