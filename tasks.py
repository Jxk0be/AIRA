#!/usr/bin/env python3
"""AIRA task runner.

There is no `make` on Windows, so this stdlib-only script plays its part:

    python tasks.py db        # start the local Supabase stack (our canonical DB)
    python tasks.py db-stop
    python tasks.py db-reset
    python tasks.py migrate   # alembic upgrade head
    python tasks.py revision -m "message"
    python tasks.py sources    # start the fake customer systems (docker compose)
    python tasks.py sources-stop
    python tasks.py seed       # fill RegisterOne with 18 months of history
    python tasks.py export     # regenerate the Panel & Pawn spreadsheet export
    python tasks.py simulate-day
    python tasks.py sources-test
    python tasks.py backfill    # sync a tenant from its source system
    python tasks.py incremental
    python tasks.py conformance # the suite every adapter must pass
    python tasks.py ingest      # embed a tenant's catalog and documents
    python tasks.py documents   # upload the fake shops' policies and FAQs
    python tasks.py reembed
    python tasks.py eval        # golden questions through the agent, graded
    python tasks.py eval-retrieval
    python tasks.py ask animanga_knox "How did last December go?"
    python tasks.py worker      # the background worker: sync, detectors, digest
    python tasks.py round       # one worker round now, then stop
    python tasks.py detect      # run the detectors for a tenant and print what they found
    python tasks.py digest      # print next Monday's digest for a tenant
    python tasks.py api
    python tasks.py web
    python tasks.py build       # production build of the web app
    python tasks.py test
    python tasks.py lint        # ruff + eslint
    python tasks.py typecheck   # mypy + vue-tsc
    python tasks.py setup     # install api + web dependencies
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = ROOT / "api"
WEB = ROOT / "web"
REGISTERONE = ROOT / "sources" / "registerone"
SPREADSHEET_SHOP = ROOT / "sources" / "spreadsheet_shop"

# The Supabase CLI is not installed globally here; npx fetches the pinned binary.
SUPABASE = ["npx", "--yes", "supabase@latest"]


def run(cmd: list[str], cwd: Path = ROOT) -> int:
    printable = " ".join(cmd)
    print(f"\n$ {printable}   (in {cwd.relative_to(ROOT) or '.'})\n", flush=True)
    return subprocess.call(cmd, cwd=cwd, shell=(sys.platform == "win32"))


def uv(args: list[str], cwd: Path = API) -> int:
    if shutil.which("uv") is None:
        sys.exit("uv is not installed: https://docs.astral.sh/uv/getting-started/installation/")
    return run(["uv", *args], cwd=cwd)


def npm(args: list[str]) -> int:
    return run(["npm", *args], cwd=WEB)


def task_env(argv: list[str]) -> int:
    """Report what the .env holds, with secrets masked."""
    if not (ROOT / ".env").exists():
        example = ROOT / ".env.example"
        print(f"\n  No .env yet. Copying {example.name} -> .env\n")
        shutil.copy(example, ROOT / ".env")
    return uv(["run", "python", "-m", "app.env", *argv])


def task_setup(argv: list[str]) -> int:
    code = uv(["sync", "--all-groups"])
    if code:
        return code
    code = uv(["sync", "--all-groups"], cwd=REGISTERONE)
    if code:
        return code
    code = uv(["sync", "--all-groups"], cwd=SPREADSHEET_SHOP)
    if code:
        return code
    return npm(["install"])


def task_sources(argv: list[str]) -> int:
    """Fake CUSTOMER systems. Not Supabase, on purpose."""
    return run(["docker", "compose", "up", "-d", "--build", *argv])


def task_sources_stop(argv: list[str]) -> int:
    return run(["docker", "compose", "down", *argv])


def task_sources_logs(argv: list[str]) -> int:
    return run(["docker", "compose", "logs", "-f", *argv])


def task_seed(argv: list[str]) -> int:
    """Eighteen months of Animanga Knox history, plus a summary."""
    return uv(["run", "python", "-m", "registerone.seed", "--reset", *argv], cwd=REGISTERONE)


def task_export(argv: list[str]) -> int:
    """Regenerate Panel & Pawn's messy spreadsheet export."""
    return uv(["run", "python", "-m", "panelpawn.generate", *argv], cwd=SPREADSHEET_SHOP)


def task_simulate_day(argv: list[str]) -> int:
    """One more day of sales, so incremental sync has something to find."""
    return run(
        [
            "curl", "-s", "-X", "POST",
            "-H", "Authorization: Bearer ro_admin_9c3e77",
            "-H", "Content-Type: application/json",
            "-d", "{}",
            "http://localhost:8100/_simulate/day",
            *argv,
        ]
    )


def task_sources_test(argv: list[str]) -> int:
    return uv(["run", "pytest", "tests", *argv], cwd=REGISTERONE)


def _sync(mode: str, argv: list[str]) -> int:
    tenant = argv[0] if argv and not argv[0].startswith("-") else "animanga_knox"
    rest = argv[1:] if argv and not argv[0].startswith("-") else argv
    return uv(["run", "python", "-m", "app.sync", "--tenant", tenant, "--mode", mode, *rest])


def task_backfill(argv: list[str]) -> int:
    return _sync("backfill", argv)


def task_incremental(argv: list[str]) -> int:
    return _sync("incremental", argv)


def task_ingest(argv: list[str]) -> int:
    """Embed a tenant's catalog and documents. Cheap to repeat: unchanged
    chunks are skipped and cost nothing."""
    tenant = argv[0] if argv and not argv[0].startswith("-") else "animanga_knox"
    rest = argv[1:] if argv and not argv[0].startswith("-") else argv
    return uv(["run", "python", "-m", "app.rag.cli", "--tenant", tenant, *rest])


def task_documents(argv: list[str]) -> int:
    """Upload the fake shops' policies and FAQs, then index them."""
    for slug in ("animanga_knox", "panel_and_pawn"):
        folder = ROOT / "sources" / "documents" / slug
        if not folder.is_dir():
            continue
        code = uv(
            ["run", "python", "-m", "app.rag.cli", "--tenant", slug, "--load", str(folder), *argv]
        )
        if code:
            return code
    return 0


def task_reembed(argv: list[str]) -> int:
    """Re-embed everything, for when the model or the dimension changes."""
    tenant = argv[0] if argv and not argv[0].startswith("-") else "animanga_knox"
    rest = argv[1:] if argv and not argv[0].startswith("-") else argv
    return uv(["run", "python", "-m", "app.rag.reembed", "--tenant", tenant, *rest])


def task_ask(argv: list[str]) -> int:
    """Ask a shop's assistant a question, printed to the terminal.

        python tasks.py ask animanga_knox "How did last December go?"
    """
    tenant = argv[0] if argv and not argv[0].startswith("-") else "animanga_knox"
    rest = argv[1:] if argv and not argv[0].startswith("-") else argv
    return uv(["run", "python", "-m", "app.agent.ask", "--tenant", tenant, *rest])


def task_worker(argv: list[str]) -> int:
    """The background worker. Sync, detectors, digest, outcomes, month end.

    Schedules are evaluated in each shop's own timezone, so this is one process
    for every tenant rather than one per tenant.
    """
    return uv(["run", "python", "-m", "app.jobs.worker", *argv])


def task_round(argv: list[str]) -> int:
    """One round of the worker, right now, then stop. What cron would call."""
    return uv(["run", "python", "-m", "app.jobs.worker", "--once", *argv])


def task_detect(argv: list[str]) -> int:
    """Run every detector for a shop and print what they found."""
    tenant = argv[0] if argv and not argv[0].startswith("-") else "animanga_knox"
    rest = argv[1:] if argv and not argv[0].startswith("-") else argv
    return uv(["run", "python", "-m", "app.insights.cli", "--tenant", tenant, *rest])


def task_digest(argv: list[str]) -> int:
    """Print the digest this shop would get on Monday, without sending it."""
    tenant = argv[0] if argv and not argv[0].startswith("-") else "animanga_knox"
    rest = argv[1:] if argv and not argv[0].startswith("-") else argv
    return uv(["run", "python", "-m", "app.digest.cli", "--tenant", tenant, *rest])


def task_eval(argv: list[str]) -> int:
    """The golden questions, end to end, graded. Costs real money."""
    return uv(["run", "python", str(ROOT / "scripts" / "evals" / "run.py"), *argv])


def task_eval_retrieval(argv: list[str]) -> int:
    """hit@5 for vector-only, text-only and hybrid, on every fake shop."""
    return uv(["run", "python", str(ROOT / "scripts" / "eval_retrieval.py"), *argv])


def task_conformance(argv: list[str]) -> int:
    """The objective answer to "is this adapter done?"."""
    return uv(["run", "pytest", "tests/conformance", "-v", *argv])


def task_db(argv: list[str]) -> int:
    return run([*SUPABASE, "start", *argv])


def task_db_stop(argv: list[str]) -> int:
    return run([*SUPABASE, "stop", *argv])


def task_db_reset(argv: list[str]) -> int:
    return run([*SUPABASE, "db", "reset", *argv])


def task_migrate(argv: list[str]) -> int:
    return uv(["run", "alembic", "upgrade", "head", *argv])


def task_revision(argv: list[str]) -> int:
    return uv(["run", "alembic", "revision", "--autogenerate", *argv])


def task_api(argv: list[str]) -> int:
    return uv(["run", "uvicorn", "app.main:app", "--reload", *argv])


def task_web(argv: list[str]) -> int:
    return npm(["run", "dev", *argv])


def task_test(argv: list[str]) -> int:
    """Both sides. The UI checks run too, so a token that fails AA fails here."""
    code = uv(["run", "pytest", *argv])
    return task_ui_check([]) or code


def task_ui_check(argv: list[str]) -> int:
    """The whole UI gate: contrast, then raw colors, then Playwright and axe.

    Every check runs even when an earlier one fails, for the same reason `lint`
    does it: one command should tell you everything that is wrong rather than
    the first thing.

    Needs no database. The API is replayed from `web/tests/fixtures`, recorded
    by `ui-fixtures`, so this runs on a machine with nothing else started.
    """
    code = run(["node", "scripts/check-contrast.ts"], cwd=WEB)
    code = _check_no_raw_colors() or code
    return npm(["run", "test:e2e", "--", *argv]) or code


def task_ui_shots(argv: list[str]) -> int:
    """Full-page screenshots of every screen into docs/ui/after/.

    Separate from `ui-check` on purpose: writing 40 PNGs into the repo is
    something you ask for, not a side effect of running the tests.
    """
    os.environ["UI_SHOTS"] = "1"
    print("\n  writing screenshots into docs/ui/after", flush=True)
    return npm(["run", "test:e2e", "--", "shots.spec.ts", *argv])


def task_ui_fixtures(argv: list[str]) -> int:
    """Re-record the API responses the UI tests replay. Needs `tasks.py api`."""
    return run(["node", "scripts/record-fixtures.ts", *argv], cwd=WEB)


# Hex, rgb()/hsl() and Tailwind's own palette are all ways of smuggling a color
# past the gate. Components get semantic tokens; style.css holds the palette.
_COLOR_PATTERNS = (
    re.compile(r"#[0-9a-fA-F]{3,8}"),
    re.compile(r"(?:rgb|rgba|hsl|hsla)\("),
    re.compile(
        r"(?:bg|text|border|ring|from|to|via|fill|stroke|divide|outline|shadow)-"
        r"(?:slate|gray|grey|zinc|neutral|stone|red|orange|amber|yellow|lime|green|"
        r"emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}"
    ),
)


def _check_no_raw_colors() -> int:
    """A color written into a component is a color no theme can reach."""
    offenders: list[str] = []
    for path in sorted((WEB / "src").rglob("*")):
        if path.suffix not in {".vue", ".ts"} or not path.is_file():
            continue
        # Three files are color machinery rather than components:
        # `ChartRenderer.vue` carries the fallbacks that keep a chart drawable
        # before the CSS has loaded, and `lib/color.ts` and `lib/brand.ts` are
        # the maths that decides whether a shop's chosen color is readable —
        # they have to name real values to do it. Everything else gets tokens.
        if path.name in {"ChartRenderer.vue", "color.ts", "brand.ts"}:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(pattern.search(line) for pattern in _COLOR_PATTERNS):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:96]}")

    if offenders:
        print("\nRaw colors in components — use a semantic token instead:\n", flush=True)
        for line in offenders:
            print(f"  {line}", flush=True)
        print(f"\n{len(offenders)} to fix.", flush=True)
        return 1
    print("\n  ok   no raw colors in components", flush=True)
    return 0


def task_build(argv: list[str]) -> int:
    return npm(["run", "build", *argv])


def task_lint(argv: list[str]) -> int:
    """Both sides, and report the worst of them.

    Every linter runs even when an earlier one fails: one command should tell
    you everything that is wrong, not the first thing.
    """
    code = uv(["run", "ruff", "check", "."])
    code = uv(["run", "ruff", "format", "--check", "."]) or code
    return npm(["run", "lint"]) or code


def task_typecheck(argv: list[str]) -> int:
    code = uv(["run", "mypy", "app"])
    return npm(["run", "typecheck"]) or code


TASKS = {
    "env": task_env,
    "setup": task_setup,
    "sources": task_sources,
    "sources-stop": task_sources_stop,
    "sources-logs": task_sources_logs,
    "sources-test": task_sources_test,
    "seed": task_seed,
    "export": task_export,
    "backfill": task_backfill,
    "incremental": task_incremental,
    "conformance": task_conformance,
    "ingest": task_ingest,
    "documents": task_documents,
    "reembed": task_reembed,
    "eval": task_eval,
    "eval-retrieval": task_eval_retrieval,
    "ask": task_ask,
    "worker": task_worker,
    "round": task_round,
    "detect": task_detect,
    "digest": task_digest,
    "simulate-day": task_simulate_day,
    "db": task_db,
    "db-stop": task_db_stop,
    "db-reset": task_db_reset,
    "migrate": task_migrate,
    "revision": task_revision,
    "api": task_api,
    "web": task_web,
    "build": task_build,
    "test": task_test,
    "ui-check": task_ui_check,
    "ui-shots": task_ui_shots,
    "ui-fixtures": task_ui_fixtures,
    "lint": task_lint,
    "typecheck": task_typecheck,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help", "help"}:
        print(__doc__)
        return 0
    name = sys.argv[1]
    if name not in TASKS:
        print(f"unknown task {name!r}. known tasks: {', '.join(TASKS)}", file=sys.stderr)
        return 2
    return TASKS[name](sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
