"""Report what the environment is actually holding.

    python tasks.py env

Reads the same `.env` the app reads, and says which variables are set, which
are missing, and which are still on their default. Secrets are masked — the
point is to answer "did I paste that in correctly?" without putting a key into
your scrollback, a screenshot, or a bug report.

Exits non-zero only when something needed *right now* is missing. A key the
agent will want in phase 8 is reported as a note, not a failure.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from app.config import REPO_ROOT, get_settings

# Phases that have actually been built. A variable a later phase needs is
# listed, explained, and not treated as a problem yet.
CURRENT_PHASE = 4


@dataclass(frozen=True)
class EnvVar:
    name: str
    group: str
    secret: bool = False
    # None means it ships with a working default and never has to be set.
    needed_from_phase: int | None = None
    hint: str = ""
    expected_prefix: str | None = None


SPEC: tuple[EnvVar, ...] = (
    EnvVar(
        "DATABASE_URL",
        "Canonical database",
        secret=True,
        needed_from_phase=1,
        hint="`python tasks.py db` starts the local Supabase stack this points at",
    ),
    EnvVar(
        "ANTHROPIC_API_KEY",
        "Anthropic",
        secret=True,
        needed_from_phase=8,
        hint="console.anthropic.com/settings/keys",
        expected_prefix="sk-ant-",
    ),
    EnvVar("AGENT_MODEL", "Anthropic"),
    EnvVar("UTILITY_MODEL", "Anthropic"),
    EnvVar(
        "VOYAGE_API_KEY",
        "Embeddings",
        secret=True,
        needed_from_phase=7,
        hint="dashboard.voyageai.com/api-keys — not needed if EMBEDDING_PROVIDER=local",
    ),
    EnvVar("EMBEDDING_PROVIDER", "Embeddings"),
    EnvVar("EMBEDDING_MODEL", "Embeddings"),
    EnvVar("EMBEDDING_DIM", "Embeddings", hint="must stay 1024; the chunks table is migrated"),
    EnvVar("EMBEDDING_MAX_RPM", "Embeddings", hint="0 = no pacing; 3 suits a free Voyage account"),
    EnvVar("API_HOST", "API server"),
    EnvVar("API_PORT", "API server"),
    EnvVar("CORS_ORIGINS", "API server"),
    EnvVar(
        "REGISTERONE_TOKEN",
        "Fake source systems (dev)",
        needed_from_phase=4,
        hint="the integration's secret_ref is env:REGISTERONE_TOKEN",
    ),
    EnvVar("REGISTERONE_ADMIN_TOKEN", "Fake source systems (dev)", needed_from_phase=4),
    EnvVar("REGISTERONE_BASE_URL", "Fake source systems (dev)", needed_from_phase=4),
    EnvVar("REGISTERONE_DB_DSN", "Fake source systems (dev)", hint="ground truth for tests only"),
)

PLACEHOLDERS = {
    "changeme",
    "your-key-here",
    "your_key_here",
    "todo",
    "xxx",
    "<password>",
}


def mask(value: str) -> str:
    if len(value) <= 10:
        return "*" * len(value)
    return f"{value[:6]}{'*' * 8}{value[-4:]}"


def mask_url(value: str) -> str:
    """Hide the password in a connection string, keep everything else."""
    try:
        parts = urlsplit(value)
    except ValueError:
        return mask(value)
    if not parts.hostname or parts.password is None:
        return value
    userinfo = f"{parts.username}:{'*' * 6}" if parts.username else "*" * 6
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit(
        (parts.scheme, f"{userinfo}@{parts.hostname}{port}", parts.path, parts.query, "")
    )


def problems_with(var: EnvVar, value: str) -> list[str]:
    """The paste mistakes that cost an hour each."""
    found: list[str] = []
    if value != value.strip():
        found.append("has leading or trailing whitespace")
    stripped = value.strip()
    if len(stripped) > 1 and stripped[0] == stripped[-1] and stripped[0] in "\"'":
        found.append("is wrapped in quotes — .env values are literal, drop them")
    if stripped.lower() in PLACEHOLDERS:
        found.append("is still a placeholder")
    if var.expected_prefix and stripped and not stripped.startswith(var.expected_prefix):
        found.append(
            f"does not start with {var.expected_prefix!r} — check you copied the whole key"
        )
    return found


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    env_path = REPO_ROOT / ".env"
    print(f"\n  Environment  {env_path}")
    if not env_path.exists():
        print("\n  There is no .env yet. Start from the template:\n")
        print("    cp .env.example .env\n")
        return 1

    # Importing config already loaded the file into os.environ.
    get_settings()

    missing_now: list[EnvVar] = []
    missing_later: list[EnvVar] = []
    warnings: list[tuple[str, str]] = []
    current_group = ""

    for var in SPEC:
        if var.group != current_group:
            current_group = var.group
            print(f"\n  {current_group}")

        raw = os.environ.get(var.name, "")
        value = raw.strip()
        needed_now = var.needed_from_phase is not None and var.needed_from_phase <= CURRENT_PHASE

        if not value:
            if needed_now:
                status, shown = "MISSING", ""
                missing_now.append(var)
            elif var.needed_from_phase is not None:
                status, shown = "later", f"needed from phase {var.needed_from_phase}"
                missing_later.append(var)
            else:
                status, shown = "unset", "(using the built-in default)"
        else:
            status = "ok"
            if var.name.endswith(("_URL", "_DSN")) and "://" in value:
                shown = mask_url(value)
            elif var.secret:
                shown = mask(value)
            else:
                shown = value
            for problem in problems_with(var, raw):
                warnings.append((var.name, problem))

        print(f"    {var.name:26} {status:8} {shown}")
        if status in {"MISSING", "later"} and var.hint:
            print(f"    {'':26} {'':8} {var.hint}")

    if warnings:
        print("\n  Worth a look")
        for name, problem in warnings:
            print(f"    {name} {problem}")

    print()
    if missing_now:
        names = ", ".join(v.name for v in missing_now)
        print(f"  Not ready: {names} must be set for what is already built.\n")
        return 1

    if missing_later:
        upcoming = ", ".join(
            f"{v.name} (phase {v.needed_from_phase})"
            for v in sorted(missing_later, key=lambda v: v.needed_from_phase or 0)
        )
        print(f"  Ready for everything built so far. Still to fill in: {upcoming}.\n")
    else:
        print("  Everything the app looks for is set.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
