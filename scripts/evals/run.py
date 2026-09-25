"""Run the golden questions through the assistant and grade the answers.

    python tasks.py eval
    python tasks.py eval --tenant panel_and_pawn --csv out.csv

Every question goes through the same path a shop owner's would: the agent, its
tools, the semantic layer, the adapter's data. The expected answer comes from
the customer's own system instead, so a failure here means one of those four
layers is wrong — which is the only kind of eval worth having for a product
whose selling point is that it works on top of somebody else's POS.

Grading is done by Haiku, which is asked three separate things: is the number
right within tolerance, were the right tools used, and was the answer honest
about what it could not do. A question is only a pass if all three hold.

This costs real money — roughly a penny or two a question — and it hits the
live model. Nothing here runs in the test suite.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

import yaml
from anthropic import APIError, AsyncAnthropic
from pydantic import BaseModel

# A sibling module, not a package: this script is run by path, so its own
# directory is what Python searches first.
from sources import OracleError, SpreadsheetOracle, SqlOracle
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent.assistant import ClaudeAssistant
from app.agent.context import build_context
from app.agent.events import EventType
from app.config import get_settings
from app.db import dispose_engine, get_sessionmaker

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).resolve().parent / "golden"

REGISTERONE_DSN = os.environ.get(
    "REGISTERONE_DB_DSN",
    "postgresql+asyncpg://registerone:registerone@127.0.0.1:5433/registerone",
)
SPREADSHEET = ROOT / "sources" / "spreadsheet_shop" / "export" / "panel_and_pawn.xlsx"

# What a shop might reasonably ask in a day, for the monthly projection.
QUESTIONS_PER_DAY = (5, 20)

NUMBER = re.compile(r"-?\$?\d[\d,]*\.?\d*%?")

# How much of each tool result the grader is shown. Unbounded is worse, not
# better: six daily breakdowns of thirty buckets each buried the one figure
# that mattered and the grader called a correct answer fabricated.
EVIDENCE_CHARS = 2_500


class Grade(BaseModel):
    """What the grader has to decide, one judgement at a time."""

    number_correct: Literal["yes", "no", "not applicable"]
    tools_correct: Literal["yes", "no"]
    honest: Literal["yes", "no"]
    verdict: Literal["pass", "fail"]
    reason: str


GRADER_PROMPT = """\
You are marking one answer from a retail analytics assistant. Be strict and be
brief. The shop is {shop}.

Question asked: {question}
Kind of question: {kind}
{expectation}
Tools the assistant actually called: {tools}
{requirements}

What those tools returned:
---
{evidence}
---

The assistant's answer:
---
{answer}
---

Mark three things separately.

number_correct: "yes" if the answer states a figure matching the expected value
within the stated tolerance (ignore formatting, currency symbols and rounding
inside the tolerance). "no" if it states a different figure or none at all.
"not applicable" when no expected value was given.

tools_correct: "yes" if the assistant called at least one of the tools listed as
appropriate, or if no particular tool was required. "no" if it answered without
calling anything, or used something that could not answer the question.

honest: "yes" unless the answer does one of these three things.

  - States a figure or names a product that contradicts the tool output. Some
    tool output above is marked [truncated]; a figure you cannot find in a
    truncated result is not evidence of invention, so only mark this when the
    output actually contradicts the answer.
  - Drops a caveat that changes how *this particular* figure should be read —
    a margin quoted without its coverage, a product total that leaves out
    custom-amount sales. A caveat that has no bearing on the question asked
    does not have to be repeated; an assistant that recites every caveat on
    every answer is one nobody reads.
  - Produces a number for something the shop's data cannot support, instead of
    saying so.

verdict: "pass" only if none of the three is "no".

reason: one sentence, naming the specific thing that was wrong if anything was.
"""


@dataclass
class Result:
    tenant: str
    id: str
    kind: str
    question: str
    expected: Decimal | None
    answer: str
    tools: list[str]
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    cost: Decimal
    latency_ms: int
    grade: Grade | None = None
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.grade is not None and self.grade.verdict == "pass"

    def number_in_answer(self) -> bool | None:
        """A mechanical second opinion on the grader.

        Not authoritative — an answer may legitimately say "about $18.5k" — but
        a column that disagrees with the grader across many rows is a sign the
        grader has gone soft.
        """
        if self.expected is None:
            return None
        for token in NUMBER.findall(self.answer):
            try:
                found = Decimal(token.replace("$", "").replace(",", "").rstrip("%"))
            except InvalidOperation:
                continue
            if token.endswith("%"):
                found = found / 100
            if abs(found - self.expected) <= abs(self.expected) * Decimal("0.01"):
                return True
        return False

    def row(self) -> dict[str, Any]:
        grade = self.grade
        return {
            "tenant": self.tenant,
            "id": self.id,
            "kind": self.kind,
            "question": self.question,
            "verdict": grade.verdict if grade else "error",
            "number_correct": grade.number_correct if grade else "",
            "tools_correct": grade.tools_correct if grade else "",
            "honest": grade.honest if grade else "",
            "expected": str(self.expected) if self.expected is not None else "",
            "number_seen_in_answer": self.number_in_answer(),
            "tools": " ".join(self.tools),
            "input_tokens": self.input_tokens,
            "cached_tokens": self.cached_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": str(self.cost),
            "latency_ms": self.latency_ms,
            "reason": grade.reason if grade else (self.error or ""),
            "answer": self.answer,
        }


@dataclass
class TenantRun:
    slug: str
    results: list[Result] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        graded = [r for r in self.results if r.grade is not None]
        return sum(r.passed for r in graded) / len(graded) if graded else 0.0

    @property
    def average_cost(self) -> Decimal:
        if not self.results:
            return Decimal("0")
        return sum((r.cost for r in self.results), Decimal("0")) / len(self.results)

    @property
    def p95_latency(self) -> float:
        if not self.results:
            return 0.0
        latencies = sorted(r.latency_ms for r in self.results)
        index = max(0, round(0.95 * len(latencies)) - 1)
        return latencies[index] / 1000


@dataclass
class Turn:
    answer: str = ""
    tools: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    failure: str | None = None


async def ask(assistant: ClaudeAssistant, session: AsyncSession, slug: str, question: str) -> Turn:
    """One question, collected rather than streamed.

    The tool results are collected too. Without them the grader is being asked
    whether a reorder list invented its product names while being shown only
    the list — which it answers by guessing, and it guessed wrong.
    """
    shop = await build_context(session, slug)
    turn = Turn()
    spoken: list[str] = []

    async for event in assistant.stream(session, shop, question):
        if event.type is EventType.TOKEN:
            spoken.append(event.data["text"])
        elif event.type is EventType.TOOL_START:
            turn.tools.append(event.data["tool"])
        elif event.type is EventType.TOOL_END and event.data.get("result"):
            output = event.data["result"]
            clipped = output[:EVIDENCE_CHARS]
            if len(output) > EVIDENCE_CHARS:
                clipped += " …[truncated]"
            turn.evidence.append(f"{event.data['tool']} returned: {clipped}")
        elif event.type is EventType.DONE:
            turn.summary = event.data
        elif event.type is EventType.ERROR:
            turn.failure = event.data["message"]

    turn.answer = "".join(spoken).strip()
    return turn


async def grade(
    client: AsyncAnthropic,
    model: str,
    shop: str,
    question: dict[str, Any],
    result: Result,
    evidence: list[str],
) -> Grade:
    expectation = (
        f"Expected value from the shop's own system: {result.expected} "
        f"({question.get('unit', 'number')}), tolerance "
        f"{float(question.get('tolerance', 0.01)) * 100:.0f}%."
        if result.expected is not None
        else "No expected value: this question has no single right number."
    )
    requirements = []
    if question.get("must_use"):
        requirements.append(f"Appropriate tools: {', '.join(question['must_use'])}")
    if question.get("must_mention"):
        requirements.append(
            "The answer must mention: " + ", ".join(repr(m) for m in question["must_mention"])
        )
    if question.get("notes"):
        requirements.append(f"What a good answer looks like: {question['notes'].strip()}")

    response = await client.messages.parse(
        model=model,
        max_tokens=600,
        output_format=Grade,
        messages=[
            {
                "role": "user",
                "content": GRADER_PROMPT.format(
                    shop=shop,
                    question=question["question"],
                    kind=question.get("kind", "number"),
                    expectation=expectation,
                    tools=", ".join(result.tools) or "none",
                    requirements="\n".join(requirements) or "No particular tool is required.",
                    evidence="\n\n".join(evidence) or "(no tool returned anything)",
                    answer=result.answer or "(the assistant said nothing)",
                ),
            }
        ],
    )
    parsed = response.parsed_output
    if parsed is None:
        raise OracleError("the grader returned nothing")
    return parsed


def load(slug: str) -> list[dict[str, Any]]:
    path = GOLDEN / f"{slug}.yaml"
    if not path.exists():
        raise SystemExit(f"  no golden file at {path}")
    return list(yaml.safe_load(path.read_text(encoding="utf-8")))


async def freshness(session: AsyncSession, slug: str) -> str | None:
    """Warn when the source has moved on since the last sync.

    The conformance and incremental tests add days to RegisterOne. Stock
    questions are asked of the present tense, so an unsynced day turns a correct
    answer into a wrong one for a reason that has nothing to do with the agent.
    """
    if slug != "animanga_knox":
        return None
    engine = create_async_engine(REGISTERONE_DSN, poolclass=NullPool)
    try:
        async with engine.connect() as source:
            theirs = (await source.execute(text("select max(created_at) from orders"))).scalar_one()
    finally:
        await engine.dispose()

    ours = (
        await session.execute(
            text(
                "select max(o.placed_at) from orders o join tenants t on t.id = o.tenant_id "
                "where t.slug = :slug"
            ),
            {"slug": slug},
        )
    ).scalar_one()
    if ours is None or theirs is None or theirs <= ours:
        return None
    return (
        f"RegisterOne has sales up to {theirs:%Y-%m-%d} but the last sync brought us to "
        f"{ours:%Y-%m-%d}. Run `python tasks.py incremental` first, or the stock questions "
        "will fail for the wrong reason."
    )


async def run_tenant(
    slug: str,
    questions: list[dict[str, Any]],
    session: AsyncSession,
    assistant: ClaudeAssistant,
    client: AsyncAnthropic,
    grader_model: str,
) -> TenantRun:
    shop = await build_context(session, slug)
    run = TenantRun(slug=slug)

    sql_oracle = None
    sheet_oracle = None
    if any(q.get("sql") for q in questions):
        engine = create_async_engine(REGISTERONE_DSN, poolclass=NullPool)
        sql_oracle = SqlOracle(async_sessionmaker(engine, expire_on_commit=False)())
    if any(q.get("expect") for q in questions):
        sheet_oracle = SpreadsheetOracle(SPREADSHEET)

    try:
        for position, question in enumerate(questions, start=1):
            expected: Decimal | None = None
            try:
                if question.get("sql") and sql_oracle is not None:
                    expected = await sql_oracle.value(question)
                elif question.get("expect") and sheet_oracle is not None:
                    expected = sheet_oracle.value(question)
            except OracleError as exc:
                print(f"    {question['id']}: ground truth failed — {exc}")

            turn = await ask(assistant, session, slug, question["question"])
            result = Result(
                tenant=slug,
                id=question["id"],
                kind=question.get("kind", "number"),
                question=question["question"],
                expected=expected,
                answer=turn.answer,
                tools=turn.tools,
                input_tokens=int(turn.summary.get("input_tokens", 0)),
                cached_tokens=int(turn.summary.get("cache_read_tokens", 0)),
                output_tokens=int(turn.summary.get("output_tokens", 0)),
                cost=Decimal(str(turn.summary.get("cost_usd", "0"))),
                latency_ms=int(turn.summary.get("latency_ms", 0)),
                error=turn.failure,
            )
            try:
                result.grade = await grade(
                    client, grader_model, shop.name, question, result, turn.evidence
                )
            except (APIError, OracleError) as exc:
                result.error = f"grading failed: {exc}"

            run.results.append(result)
            mark = "ok  " if result.passed else "FAIL"
            print(
                f"    {position:>2}/{len(questions)}  {mark}  {question['id']:<34} "
                f"{result.latency_ms / 1000:>5.1f}s  ${result.cost}"
            )
            if not result.passed and result.grade is not None:
                print(f"           {result.grade.reason}")
    finally:
        if sql_oracle is not None:
            await sql_oracle.session.close()

    return run


def report(runs: list[TenantRun]) -> None:
    print("\n  Results\n")
    print(f"    {'shop':<18} {'questions':>9} {'accuracy':>9} {'avg cost':>10} {'p95':>7}")
    for run in runs:
        print(
            f"    {run.slug:<18} {len(run.results):>9} {run.accuracy:>8.0%} "
            f"{'$' + str(round(run.average_cost, 4)):>10} {run.p95_latency:>6.1f}s"
        )

    everything = [result for run in runs for result in run.results]
    if not everything:
        return
    average = sum((r.cost for r in everything), Decimal("0")) / len(everything)
    print(f"\n    ${round(average, 4)} per question on average")
    for per_day in QUESTIONS_PER_DAY:
        monthly = average * per_day * 30
        print(f"    ${round(monthly, 2):>6} per shop per month at {per_day} questions a day")

    failures = [result for result in everything if not result.passed]
    if failures:
        print(f"\n  {len(failures)} failed\n")
        for result in failures:
            reason = result.grade.reason if result.grade else (result.error or "no grade")
            print(f"    {result.tenant}/{result.id}: {reason}")


def write_csv(path: Path, runs: list[TenantRun]) -> None:
    rows = [result.row() for run in runs for result in run.results]
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", help="one shop; default is every golden file")
    parser.add_argument("--id", help="run a single question by id")
    parser.add_argument("--limit", type=int, help="stop after this many questions per shop")
    parser.add_argument("--csv", help="write per-question results here")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    settings = get_settings()
    slugs = [args.tenant] if args.tenant else sorted(path.stem for path in GOLDEN.glob("*.yaml"))
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    assistant = ClaudeAssistant(settings=settings, client=client)
    runs: list[TenantRun] = []

    try:
        async with get_sessionmaker()() as session:
            for slug in slugs:
                questions = load(slug)
                if args.id:
                    questions = [q for q in questions if q["id"] == args.id]
                if args.limit:
                    questions = questions[: args.limit]
                if not questions:
                    continue

                print(f"\n  {slug} — {len(questions)} questions")
                warning = await freshness(session, slug)
                if warning:
                    print(f"    warning: {warning}")

                runs.append(
                    await run_tenant(
                        slug, questions, session, assistant, client, settings.utility_model
                    )
                )
    finally:
        await dispose_engine()

    report(runs)
    if args.csv:
        out = Path(args.csv)
        await asyncio.to_thread(write_csv, out, runs)
        print(f"\n  per-question results: {out}")

    worst = min((run.accuracy for run in runs), default=0.0)
    return 0 if worst >= 0.9 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
