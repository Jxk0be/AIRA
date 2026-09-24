"""Does retrieval actually find the right thing, and is hybrid worth it?

    python tasks.py eval-retrieval
    python tasks.py eval-retrieval --tenant tsundoku --csv out.csv

Runs every golden question through vector-only, text-only and hybrid search and
reports hit@k for each. The point is not the absolute number. It is the
comparison: hybrid has to be at least as good as either half it is built from,
everywhere, and meaningfully better where the data is thin. It exits non-zero
only when hybrid loses to one of its own halves, which is the regression worth
being told about.

Each question is embedded exactly once and the vector is reused across the
modes that need it, so the whole run costs one query embedding per question.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.db import dispose_engine, get_sessionmaker
from app.rag.embeddings import Embedder, EmbeddingError, get_embedder
from app.rag.search import SearchMode, search

GOLDEN = Path(__file__).resolve().parent / "evals" / "retrieval"
MODES = (SearchMode.VECTOR, SearchMode.TEXT, SearchMode.HYBRID)


@dataclass(frozen=True)
class Question:
    query: str
    expect: tuple[str, ...]
    note: str = ""

    def hit(self, titles: list[str]) -> bool:
        """Any expected string inside any returned title.

        Substring rather than equality because a hit is "the right thing came
        back", and a product's title carries its variant names with it.
        """
        haystack = " | ".join(titles).lower()
        return any(want.lower() in haystack for want in self.expect)


@dataclass
class ModeScore:
    hits: int = 0
    asked: int = 0
    misses: list[str] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return self.hits / self.asked if self.asked else 0.0


def load_questions(path: Path) -> list[Question]:
    raw: list[dict[str, Any]] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        Question(
            query=str(item["query"]),
            expect=tuple(str(x) for x in item["expect"]),
            note=str(item.get("note", "")),
        )
        for item in raw
    ]


async def run_tenant(
    session: AsyncSession,
    tenant: t.Tenant,
    questions: list[Question],
    embedder: Embedder,
    k: int,
    rows: list[dict[str, Any]],
) -> dict[SearchMode, ModeScore]:
    # One request for the whole set, rather than one per question per mode.
    vectors = await embedder.embed_queries([q.query for q in questions])
    scores = {mode: ModeScore() for mode in MODES}

    for question, vector in zip(questions, vectors, strict=True):
        row: dict[str, Any] = {"tenant": tenant.slug, "query": question.query}
        for mode in MODES:
            hits = await search(
                session,
                tenant.id,
                question.query,
                embedder,
                k=k,
                mode=mode,
                query_vector=vector,
            )
            titles = [hit.title for hit in hits]
            found = question.hit(titles)
            scores[mode].asked += 1
            scores[mode].hits += int(found)
            if not found:
                scores[mode].misses.append(question.query)
            row[f"{mode.value}_hit"] = int(found)
            row[f"{mode.value}_top"] = titles[0] if titles else ""
        rows.append(row)

    return scores


def report(tenant: str, k: int, scores: dict[SearchMode, ModeScore]) -> None:
    print(f"\n  {tenant} — hit@{k} over {scores[SearchMode.HYBRID].asked} questions\n")
    for mode in MODES:
        score = scores[mode]
        bar = "#" * round(score.rate * 30)
        print(f"    {mode.value:8} {score.rate:>6.0%}  {score.hits:>2}/{score.asked:<2} {bar}")

    hybrid = scores[SearchMode.HYBRID]
    best_half = max(scores[SearchMode.VECTOR].rate, scores[SearchMode.TEXT].rate)
    if hybrid.rate > best_half:
        verdict = "beats the better of its two halves"
    elif hybrid.rate == best_half:
        verdict = "matches the better of its two halves"
    else:
        verdict = "LOSES to one of its own halves"
    print(f"\n    hybrid {verdict}")

    if hybrid.misses:
        print("\n    hybrid missed:")
        for miss in hybrid.misses:
            print(f"      - {miss}")


def write_csv(out: Path, rows: list[dict[str, Any]]) -> None:
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", help="one tenant slug; default is every golden file")
    parser.add_argument("--k", type=int, default=5, help="hit@k (default 5)")
    parser.add_argument("--csv", help="write per-question results here")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    files = [GOLDEN / f"{args.tenant}.yaml"] if args.tenant else sorted(GOLDEN.glob("*.yaml"))
    missing = [path for path in files if not path.exists()]
    if missing:
        print(f"  no golden file at {missing[0]}")
        return 1

    embedder = get_embedder()
    rows: list[dict[str, Any]] = []
    failures = 0

    try:
        async with get_sessionmaker()() as session:
            for path in files:
                slug = path.stem
                tenant = (
                    await session.execute(select(t.Tenant).where(t.Tenant.slug == slug))
                ).scalar_one_or_none()
                if tenant is None:
                    print(f"  skipping {slug}: no such tenant")
                    continue

                questions = load_questions(path)
                try:
                    scores = await run_tenant(session, tenant, questions, embedder, args.k, rows)
                except EmbeddingError as exc:
                    print(f"  {slug}: embedding failed — {exc}")
                    failures += 1
                    continue

                report(slug, args.k, scores)
                # Failing only on a regression, not on a tie. Hybrid earns its
                # keep where content is thin; on a shop with good descriptions
                # vector-only can legitimately match it, and demanding a strict
                # win there would only push someone to weaken the golden set.
                if scores[SearchMode.HYBRID].rate < max(
                    scores[SearchMode.VECTOR].rate, scores[SearchMode.TEXT].rate
                ):
                    failures += 1
    finally:
        await embedder.aclose()
        await dispose_engine()

    print(
        f"\n  {embedder.usage.requests} embedding requests, "
        f"{embedder.usage.tokens:,} tokens, model {embedder.name}\n"
    )

    if args.csv and rows:
        out = Path(args.csv)
        await asyncio.to_thread(write_csv, out, rows)
        print(f"  per-question results: {out}\n")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
