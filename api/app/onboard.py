"""Onboarding tools.

    python -m app.onboard draft-mapping --file export.xlsx --tenant panel_and_pawn

Reads a customer's export, shows Claude the column headers, a sample of rows and
our canonical model, and writes a **proposed** mapping YAML with a comment on
every field explaining the choice.

Two things it deliberately does not do:

* **It never runs the mapping.** The draft is a starting point for a person to
  read, correct and sign off. An unreviewed mapping that syncs is just a
  confident guess about somebody's revenue.
* **It never overwrites an existing mapping.** Drafts land next to the real file
  with a `.draft.yaml` suffix.

`--dry-run` prints the exact prompt and its token count without sending
anything, because this puts a sample of a customer's data into an API request
and you should be able to see precisely what that is first.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from app.config import REPO_ROOT, get_settings
from app.connectors.adapters.mapping_spec import TRANSFORMS

log = logging.getLogger(__name__)

SAMPLE_ROWS = 20
# Generous, and streamed: adaptive thinking draws from the same budget as the
# YAML itself, and a mapping for a wide export is not short.
MAX_TOKENS = 32_000

SCHEMA_REFERENCE = """
A mapping file looks like this:

```yaml
version: 1
source: <slug>                 # stamped on every row; part of row identity
display_name: <human name>
workbook: <path to the export, relative to the repo root>

capabilities:                  # each false switches off a tool and a dashboard widget
  has_costs: <bool>
  has_customers: <bool>
  has_inventory_history: <bool>
  multi_location: <bool>
  has_online_channel: <bool>
  supports_incremental: <bool>   # false for any file-based source

notes:                         # plain-language caveats shown to the shop owner
  - <string>

settings:
  timezone: <IANA name>
  currency: <ISO 4217>
  assumed_time_of_day: "12:00" # used when the source has dates but no clock
  # Literal cell values, compared case-insensitively — NOT regexes. A row is
  # dropped when any of its cells equals one of these. Blank rows are always
  # dropped and need no entry here.
  skip_rows_matching: ["TOTAL", "SUBTOTAL"]

sheets:                        # `match` is a regex over sheet names
  <key>:
    match: "^Sales \\\\d{4}$"
    header_row: 1

entities:
  locations:                   # synthesise one when the file has no location column
    constant:
      - external_id: MAIN
        name: <shop name>
  products:
    from: <sheet key>
    distinct_by: {column: <col>, transforms: [fold_key]}
    fields:
      external_id: {column: <col>, transforms: [fold_key, {hash_id: {prefix: "prd_"}}]}
      name: {column: <col>, transforms: [trim]}
  orders:
    from: <sheet key>
    group_by: {column: <receipt col>, transforms: [trim]}
    fields:
      external_id: {column: <receipt col>, transforms: [trim]}
      placed_at:
        column: <date col>
        transforms: [{parse_date: {formats: ["%m/%d/%y"]}}, at_time]
      location_external_id: {constant: MAIN}
      status: {constant: completed}
      channel: {constant: in_store}
    lines:
      fields:
        external_id:
          concat:
            - {group_key: true}
            - {constant: ":"}
            - {column: <item col>, transforms: [fold_key]}
        variant_external_id:
          {column: <item col>, transforms: [fold_key, {hash_id: {prefix: "var_"}}]}
        name_snapshot: {column: <item col>, transforms: [trim]}
        quantity: {column: <qty col>, transforms: [to_decimal]}
        unit_price: {column: <price col>, transforms: [parse_money]}
```

**Field sources** — exactly one per field:
  {column: "Header"}          a cell in this entity's sheet
  {constant: value}           a fixed value
  {concat: [ ... ]}           several sources joined
  {lookup: {sheet:, key:, value:, match_on:}}   a value from another sheet
  {row_number: true}          "<sheet>:<row>", a last-resort unique key
  {group_key: true}           the id of the order this row belongs to

**Transforms**, applied in order after the source:
{transforms}

Rules that matter:
* Order money is derived from the lines. Do not map subtotal or total.
* A blank cell must stay null. Never default a missing cost or price to zero.
* Keys that join two sheets go through `fold_key` before `hash_id`, because the
  same item is typed inconsistently across sheets.
* Line ids must be built on `group_key`, not on a column that can be blank.
* `distinct_by` groups rows; within a group the most common value of each column
  wins, so the tidiest spelling becomes the display name.
"""

PROMPT = """You are helping onboard a new customer onto AIRA, a multi-tenant AI analyst for
small retail shops. Their "system" is the spreadsheet below. Your job is to draft the
YAML mapping that turns it into our canonical model.

Here is our canonical model, which is the contract you are mapping onto:

<canonical_model>
{canonical}
</canonical_model>

Here is the mapping language you are writing in:

<mapping_language>
{schema}
</mapping_language>

Here is what is actually in the customer's file:

<export file="{filename}">
{sample}
</export>

Write the mapping YAML. Requirements:

1. **Comment every field** with why you chose that column and that transform. The
   person reviewing this has never seen the file; your comments are how they check
   your work.
2. **Flag everything you are unsure about** with a comment beginning `TODO(review):`
   and say what you would need to know. Guessing silently is the one unrecoverable
   mistake here — a wrong mapping produces confident wrong numbers.
3. **Set capabilities conservatively.** If there is no customer column, `has_customers`
   is false. If it is a file rather than an API, `supports_incremental` is false. A
   capability you cannot back up with a column turns on a tool that will invent
   answers.
4. **Never invent a column.** Only reference headers that appear above, exactly as
   they are written.
5. **Quote any column name containing `#`, `:`, `{`, `}`, `[`, `]` or `,`.** A bare
   `#` starts a comment in YAML, so `{column: Receipt #, transforms: [trim]}` is a
   parse error. Write `{column: "Receipt #", transforms: [trim]}`.
6. Look for summary rows, blank rows, inconsistent spellings, money formatting and
   date formats, and handle each in the mapping. Call them out in comments.
7. Put plain-language caveats in `notes:` — things a shop owner should be told about
   what their data can and cannot answer.

Output only the YAML, in a single ```yaml fenced block."""


# ---------------------------------------------------------------------------
# Reading the customer's file
# ---------------------------------------------------------------------------


def sample_workbook(path: Path, rows: int = SAMPLE_ROWS) -> str:
    """Sheet names, headers and the first N rows of each, as plain text."""
    from openpyxl import load_workbook

    book = load_workbook(path, read_only=True, data_only=True)
    chunks: list[str] = []
    try:
        for name in book.sheetnames:
            sheet = book[name]
            lines = [f"### Sheet: {name!r}  ({sheet.max_row or '?'} rows)"]
            for index, values in enumerate(sheet.iter_rows(values_only=True)):
                if index > rows:
                    break
                label = "headers" if index == 0 else f"row {index}"
                rendered = " | ".join("" if v is None else str(v) for v in values)
                lines.append(f"{label}: {rendered}")
            chunks.append("\n".join(lines))
    finally:
        book.close()
    return "\n\n".join(chunks)


def sample_csv(path: Path, rows: int = SAMPLE_ROWS) -> str:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        lines = [f"### File: {path.name}"]
        for index, values in enumerate(reader):
            if index > rows:
                break
            label = "headers" if index == 0 else f"row {index}"
            lines.append(f"{label}: {' | '.join(values)}")
    return "\n".join(lines)


def read_sample(path: Path, rows: int) -> str:
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        return sample_workbook(path, rows)
    if path.suffix.lower() in {".csv", ".tsv", ".txt"}:
        return sample_csv(path, rows)
    raise SystemExit(f"cannot sample {path.suffix!r} files; give me .xlsx or .csv")


def _first_line(docstring: str | None) -> str:
    lines = (docstring or "").strip().splitlines()
    return lines[0] if lines else "(no description)"


def build_prompt(path: Path, rows: int) -> str:
    canonical = (REPO_ROOT / "docs" / "canonical-model.md").read_text(encoding="utf-8")
    transforms = "\n".join(
        f"  {name}: {(function.__doc__ or '').strip().splitlines()[0]}"
        for name, function in sorted(TRANSFORMS.items())
    )
    # Plain substitution rather than str.format: the prompt is full of literal
    # YAML braces, and every one of them would be a format field.
    filled = PROMPT
    for placeholder, value in (
        ("{canonical}", canonical),
        ("{schema}", SCHEMA_REFERENCE.replace("{transforms}", transforms)),
        ("{filename}", path.name),
        ("{sample}", read_sample(path, rows)),
    ):
        filled = filled.replace(placeholder, value)
    return filled


def extract_yaml(text: str) -> str:
    if "```yaml" in text:
        return text.split("```yaml", 1)[1].split("```", 1)[0].strip()
    if "```" in text:
        return text.split("```", 1)[1].split("```", 1)[0].strip()
    return text.strip()


# ---------------------------------------------------------------------------
# draft-mapping
# ---------------------------------------------------------------------------


def validate(path: Path) -> str:
    """Does the draft parse, and does our loader accept it?

    Worth knowing before anyone spends twenty minutes reading it. A draft that
    does not load is a two-minute fix — but only if you are told.
    """
    import yaml

    from app.connectors.adapters.mapping_spec import MappingError
    from app.connectors.adapters.mapping_spec import load as load_spec

    try:
        yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" near line {mark.line + 1}" if mark else ""
        return f"NOT VALID YAML{where}: {getattr(exc, 'problem', exc)}"

    try:
        spec = load_spec(path, REPO_ROOT)
    except MappingError as exc:
        return f"Parses as YAML, but is not a usable mapping yet: {exc}"

    missing = "" if spec.workbook.exists() else f", but no workbook at {spec.workbook}"
    return f"Valid: {len(spec.entities)} entities across {len(spec.sheets)} sheets{missing}"


def draft_mapping(args: argparse.Namespace) -> int:
    import anthropic

    settings = get_settings()
    path = Path(args.file)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.exists():
        raise SystemExit(f"no file at {path}")

    prompt = build_prompt(path, args.rows)

    destination = Path(args.out) if args.out else REPO_ROOT / "mappings" / f"{args.tenant}.yaml"
    if not destination.is_absolute():
        destination = REPO_ROOT / destination
    if destination.exists():
        # A reviewed mapping is somebody's signed-off work. Drafts go beside it.
        destination = destination.with_suffix(".draft.yaml")

    if args.dry_run:
        print(prompt)
        print(f"\n{'=' * 70}")
        print(f"  Not sent. {len(prompt):,} characters of prompt.")
        print(f"  Would write: {destination}")
        return 0

    if not settings.anthropic_api_key:
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Run `python tasks.py env` to check, or "
            "use --dry-run to see the prompt without sending it."
        )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    model = args.model or settings.agent_model

    counted = client.messages.count_tokens(
        model=model, messages=[{"role": "user", "content": prompt}]
    )
    print(f"\n  Drafting a mapping for {args.tenant} from {path.name}")
    print(f"  {model}, {counted.input_tokens:,} input tokens\n")

    try:
        with client.messages.stream(
            model=model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            response = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise SystemExit("Anthropic rejected the API key. Check ANTHROPIC_API_KEY.") from None
    except anthropic.RateLimitError as exc:
        raise SystemExit(f"Rate limited: {exc}. Try again shortly.") from None
    except anthropic.APIStatusError as exc:
        raise SystemExit(f"Anthropic returned {exc.status_code}: {exc.message}") from None
    except anthropic.APIConnectionError as exc:
        raise SystemExit(f"Could not reach Anthropic: {exc}") from None

    if response.stop_reason == "refusal":
        detail = getattr(response.stop_details, "explanation", "no explanation given")
        raise SystemExit(f"The model declined this request: {detail}")

    if response.stop_reason == "max_tokens":
        raise SystemExit(
            f"The response hit the {MAX_TOKENS:,} token ceiling before finishing. "
            "Re-run with fewer --rows, or raise MAX_TOKENS."
        )

    text = "".join(block.text for block in response.content if block.type == "text")
    yaml_text = extract_yaml(text)
    if not yaml_text:
        kinds = ", ".join(sorted({block.type for block in response.content})) or "nothing"
        raise SystemExit(
            f"No YAML came back. stop_reason={response.stop_reason}, blocks={kinds}. "
            "Try --dry-run to inspect the prompt."
        )

    header = (
        f"# PROPOSED mapping for {args.tenant}, drafted from {path.name}.\n"
        f"#\n"
        f"# Nobody has reviewed this yet. Read every line, especially the\n"
        f"# TODO(review) comments, before pointing a sync at it. Rename this file\n"
        f"# to {args.tenant}.yaml once you have.\n"
        f"#\n"
        f"# Reviewed by:\n\n"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(header + yaml_text + "\n", encoding="utf-8")

    verdict = validate(destination)
    todos = [line.strip() for line in yaml_text.splitlines() if "TODO(review)" in line]
    usage = response.usage
    print(f"  Wrote {destination}")
    print(
        f"  {usage.input_tokens:,} in, {usage.output_tokens:,} out"
        + (
            f", {usage.cache_read_input_tokens:,} cached"
            if getattr(usage, "cache_read_input_tokens", 0)
            else ""
        )
    )
    if todos:
        print(f"\n  {len(todos)} thing(s) the draft is unsure about:")
        for todo in todos[:10]:
            print(f"    {todo.lstrip('# ')}")
        if len(todos) > 10:
            print(f"    ... and {len(todos) - 10} more")
    print(f"\n  {verdict}")
    print("\n  Next: read it, fix it, then")
    print(f"    python tasks.py backfill {args.tenant}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="app.onboard", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    draft = subcommands.add_parser(
        "draft-mapping", help="propose a mapping YAML for a customer's export"
    )
    draft.add_argument("--file", required=True, help="the customer's .xlsx or .csv")
    draft.add_argument("--tenant", required=True, help="tenant slug")
    draft.add_argument("--out", help="where to write (default mappings/<tenant>.yaml)")
    draft.add_argument("--rows", type=int, default=SAMPLE_ROWS, help="sample rows per sheet")
    draft.add_argument("--model", help="override the model (default: AGENT_MODEL)")
    draft.add_argument(
        "--dry-run",
        action="store_true",
        help="print the prompt and stop, without sending the customer's data anywhere",
    )
    draft.set_defaults(handler=draft_mapping)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
