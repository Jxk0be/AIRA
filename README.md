# AIRA

A multi-tenant AI analyst for small retail shops that works with any POS or
store platform.

The trick that makes "works with whatever you already use" true is that the AI
never knows which platform it is talking to. Every source — a Square-style cloud
API, Shopify, a spreadsheet export, a legacy SQL database — is translated into
one canonical schema by an adapter. Analytics, retrieval, the agent, the
dashboard and the evals are written once against that schema. Onboarding a
customer means getting their data into canonical shape, not touching the AI.

```
customer system  ->  adapter  ->  canonical schema  ->  analytics / RAG / agent  ->  UI
                     ^^^^^^^
            the only code that knows a platform exists
```

## Getting started

Prerequisites: [uv](https://docs.astral.sh/uv/), Node 20+, Docker Desktop.

```bash
cp .env.example .env      # then fill in the blanks; `tasks.py env` checks your work
python tasks.py setup     # uv sync + npm install
python tasks.py db        # start the local Supabase stack (first run pulls images)
python tasks.py migrate   # create the canonical schema
python tasks.py test
```

Then, in two terminals:

```bash
python tasks.py api       # http://127.0.0.1:8000/health
python tasks.py web       # http://localhost:5173
```

The web app opens on the first shop it finds. If nothing is connected yet, it
says so and prints the commands that fix it.

## Tasks

| Command | What it does |
| --- | --- |
| `python tasks.py env` | What the `.env` holds, with secrets masked |
| `python tasks.py setup` | Install Python and Node dependencies |
| `python tasks.py db` / `db-stop` / `db-reset` | Local Supabase stack |
| `python tasks.py sources` / `sources-stop` | Fake customer systems (docker compose) |
| `python tasks.py seed` | 18 months of RegisterOne history, with a summary |
| `python tasks.py export` | Regenerate the Panel & Pawn spreadsheet export |
| `python tasks.py simulate-day` | One more day of sales, for incremental sync |
| `python tasks.py sources-test` | RegisterOne's own API test suite |
| `python tasks.py backfill [tenant]` | Sync a tenant from its source system |
| `python tasks.py incremental [tenant]` | Sync only what changed |
| `python tasks.py conformance` | The suite every adapter must pass |
| `python tasks.py migrate` | `alembic upgrade head` |
| `python tasks.py revision -m "..."` | Autogenerate a migration |
| `python tasks.py api` / `web` | Dev servers |
| `python tasks.py build` | Production build of the web app |
| `python tasks.py test` | pytest |
| `python tasks.py lint` / `typecheck` | ruff + eslint, mypy + vue-tsc |

There is no `make` on Windows, so `tasks.py` — stdlib only, no venv needed —
plays its part. The Supabase CLI is fetched by `npx` rather than installed
globally.

## Layout

| Path | What lives there |
| --- | --- |
| `api/app/canonical/` | The adapter contract and the canonical tables |
| `api/app/connectors/` | Adapters and the sync engine — the only platform-aware code |
| `api/app/analytics/` | The semantic layer: one definition per metric |
| `api/app/rag/` | Embeddings, ingest, hybrid search |
| `api/app/agent/` | The assistant: tools, prompts, the loop, behind our own interface |
| `web/` | Vue 3 + TypeScript + Vite + Tailwind v4: the four screens |
| `api/app/dashboard/` | What those screens read — thin HTTP over the semantic layer |
| `sources/registerone/` | A fictional cloud POS to test adapters against — deliberately *not* in Supabase |
| `sources/spreadsheet_shop/` | A second fake customer whose whole system is a messy Excel export |
| `mappings/` | One YAML per table-shaped customer. This *is* their integration |
| `sources/documents/` | The policies and FAQs the fake shops uploaded — not from any POS |
| `scripts/evals/` | Golden sets: the right answer to every question, per shop |
| `docs/canonical-model.md` | Read this before writing an adapter |

`CLAUDE.md` holds the standing rules that keep the AI core platform-blind.

## Configuration

One `.env` at the repo root, shared by the API and the task runner.
`.env.example` is the committed template; `.env` is yours and is gitignored
(along with every other `.env.*` variant, so a stray backup cannot be committed).

`api/app/config.py` reads it two ways: into a typed `Settings` object, and into
the process environment — because an integration's `secret_ref = "env:NAME"`
resolves against `os.environ`, which is where a secret manager will have put it
in a hosted deploy.

```bash
python tasks.py env
```

lists every variable the code looks for, masks the secrets, flags the usual
paste mistakes (stray quotes, trailing whitespace, a truncated key), and says
which keys are not needed until a later phase. It exits non-zero only when
something already-built is actually missing.

The web app is the exception: it reads nothing from that file. In development
it calls `/api`, which Vite proxies to the API on port 8000, and a deployed
build points somewhere else through `VITE_API_BASE` in `web/.env`. Nothing
secret belongs there — anything Vite inlines is in the bundle.

## Databases

Two different things, kept apart on purpose:

- **Our canonical database** is Supabase Postgres. Locally that is the CLI
  stack on port 54322; the hosted deploy target is the Supabase project
  `AIRetailAssistant`.
- **Customer source systems** are foreign systems we only ever read. The fake
  ones live in `docker-compose.yml` and never go into Supabase, so "can we read
  a system we don't own?" stays an honest question.

## The test customer

**Tsundoku & Tabletop** is a Knoxville anime, manga and hobby shop running
**RegisterOne**, a fictional cloud POS. Eighteen months of history, a main store
and a convention booth, and a long list of deliberate mess — missing costs,
duplicate customers, partial refunds, custom-amount lines, a category renamed
mid-history. See [SCHEMA.md](sources/registerone/SCHEMA.md) for the shape and
[QUIRKS.md](sources/registerone/QUIRKS.md) for every trap and how to find it.

```bash
python tasks.py sources   # registerone_db on :5433, mock API on :8100
python tasks.py seed
```

The adapter talks to the mock API at `http://localhost:8100`, never to
`registerone_db`. The API paginates with opaque cursors, rate-limits at 10
requests a second, and fails about 1% of calls with a retryable 503 — because
the real ones do.

```bash
python tasks.py backfill      # ~30s for 18 months
python tasks.py simulate-day
python tasks.py incremental   # ~5s, only the new day
python tasks.py conformance
```

## The second test customer

**Panel & Pawn** is a comics and board game shop whose entire system is a
spreadsheet somebody exports from the register. It exists to prove the core is
genuinely platform-blind: no API, no ids, no cents, no cursors, no customers, no
history. The same conformance suite runs against it unchanged — the checks its
capabilities rule out are skipped, not failed.

Nothing was written in code for this customer.
[`mappings/panel_and_pawn.yaml`](mappings/panel_and_pawn.yaml) is the entire
integration; [QUIRKS.md](sources/spreadsheet_shop/QUIRKS.md) lists the sixteen
traps in the file and how each is handled in config.

```bash
python tasks.py export
python tasks.py backfill panel_and_pawn
python tasks.py conformance          # runs against both adapters
```

And for a customer you have never seen, Claude writes the first draft of that
YAML — with a comment on every field and a `TODO(review):` on everything it is
unsure about:

```bash
python -m app.onboard draft-mapping --file their-export.xlsx --tenant their_slug --dry-run
```

It never runs the mapping and never overwrites a reviewed one. `--dry-run`
prints the exact prompt first, because this puts a sample of a customer's data
into an API request.

## The semantic layer

Every number in this product comes out of `api/app/analytics`. The dashboard
endpoints and the agent's tools will both be thin wrappers over these
functions, so "net sales" cannot come to mean one thing in a chart and another
in a sentence.

```python
ctx = await load_context(session, "tsundoku")          # timezone + capabilities
summary = await sales_summary(session, ctx, ctx.month(2025, 12))
summary.net_sales                                      # Decimal("18534.57")
```

Three rules hold across all of it:

* **Periods are shop days.** A range is calendar days in the tenant's own
  timezone, converted to UTC instants in one place. The day the clocks go back
  is twenty-five hours long and a day's takings are all of them.
* **Results carry caveats.** Margin says what share of sales it covers, a
  product breakdown says it is before refunds, a total says how much of it was
  rung up as a custom amount with no product attached.
* **A missing capability refuses.** Ask Panel & Pawn for a repeat rate and you
  get `CapabilityUnavailable` with a sentence for the owner — never a 0% that
  reads as "nobody comes back".

Each metric's plain-English definition lives in
[`definitions.py`](api/app/analytics/definitions.py); the agent puts those
strings in its tool descriptions, so it can explain any number it reports.

```bash
python tasks.py test -k analytics   # every metric, recomputed against registerone_db
```

## Retrieval

Numbers come from the semantic layer. Everything else — what a product is, what
the returns policy says, when the next draft night is — comes from
`api/app/rag`, which indexes canonical products and uploaded documents into one
`chunks` table and searches it with pgvector and Postgres full-text together.

```bash
python tasks.py documents        # upload the fake shops' policies and FAQs
python tasks.py ingest           # embed a tenant's catalogue and documents
python tasks.py eval-retrieval   # hit@5 for vector-only, text-only and hybrid
```

Ingest runs automatically after every sync, and that is only affordable because
it costs nothing when nothing changed: each chunk stores a hash of its text plus
the model that embedded it, so a sync that changed three prices embeds three
chunks and a sync that changed nothing embeds none.

    tsundoku — ingest (voyage-4@1024)
      products 230   documents 3
      embedded 0   unchanged 236   removed 0
      0 embedding requests, 0 tokens

Search fuses two rankers with reciprocal rank fusion inside one SQL function,
so the dashboard, the agent and the evals cannot drift apart on what "search"
means. The two halves fail in opposite directions, which is the whole argument
for having both: an embedding finds "cosy manga set in a coffee shop" and
returns Ronin Barista, which never mentions coffee; full-text finds `MNG-SS-04`,
which an embedding turns to mush.

Measured on the golden sets in `scripts/evals/retrieval/`, hit@5:

| Shop | vector | text | hybrid |
| --- | --- | --- | --- |
| Tsundoku & Tabletop | 95% | 95% | 95% |
| Panel & Pawn | 75% | 80% | **95%** |

Hybrid earns its keep where the data is thin. Panel & Pawn's export has no
description column at all, so every catalogue chunk is a name, a category, a
price and a shelf count — and that is where fusion adds twenty points. On a shop
with real descriptions, vector search alone is already good, and hybrid matching
it is the honest result rather than a disappointing one.

Two details worth knowing:

* **Vectors from different models are not comparable.** Every chunk records
  `voyage-4@1024`, search filters on it, and changing the model or the
  dimension makes every chunk stale by hash — so `python tasks.py reembed`
  rebuilds rather than leaving a corpus half in one vector space and half in
  another.
* **A rate-limited account still works.** A Voyage key with no payment method
  on it is capped at 3 requests and 10,000 tokens a minute. Set
  `EMBEDDING_MAX_RPM=3` and the embedder paces itself, and halves a batch
  whenever the provider says it was too large.

## The assistant

`api/app/agent` is a shop's analyst. It answers in plain language, every number
it gives comes from a tool, and the tools are thin wrappers over the semantic
layer — so a figure in a sentence and the same figure on a dashboard cannot
disagree.

```bash
python tasks.py ask tsundoku "How did last December go?"
```

    [sales_summary {'start_date': '2025-12-01', 'end_date': '2025-12-31'}]
    [top_products ...]  [location_channel_breakdown ...]

    December 2025 (Dec 1-31) net sales were $18,534.57 across 348 orders ...
    One thing to flag: 2.6% of December sales ($481.50) were rung up as custom
    amounts with no product attached ...

    10.5s   $0.031   in 1,939 (+7,402 cached) out 759

Four things hold it together:

* **The shop briefs it, per request.** Name, timezone, locations, channels,
  categories, how far the data goes back, what the source system does and does
  not record, and the warnings from the last sync. Nothing is hardcoded and
  nothing is shared between tenants.
* **Capabilities decide which tools exist.** Panel & Pawn is never given
  `customer_stats`, so it does not call it and apologise — it knows from the
  briefing that the answer is not there, and says so in a sentence.
* **`tenant_id` is not an argument.** No tool takes one; it comes from the
  request's context. Filters are given as names ("Manga", "Con Booth") and
  resolved here, and an unknown name comes back listing the real ones.
* **Charts cannot contain invented numbers.** `make_chart` revalidates every
  value against what the tools actually returned this turn, and rejects the
  chart otherwise — rounding included.

Asked for a margin it cannot compute, it does this instead:

    I can't give you a margin on Board Games - none of the items that sold in
    this category have a cost recorded against them.
    Action: Add cost prices for your Board Games SKUs in the POS - until that's
    done, this category's margin is invisible, not zero.

Every run is written to `agent_runs` with its tool calls, timings, tokens and
cost, which is what makes "what does a question cost" and "what does a shop cost
per month" answerable rather than a guess. `POST /tenants/{slug}/chat` streams
the same thing as server-sent events: `token`, `tool_start`, `tool_end`,
`chart`, then one `done` or one `error`.

A note on the build: the plan called for LangChain here. It is built directly on
the Anthropic SDK instead, behind the `Assistant` interface the plan asked for —
prompt caching placement, per-call token accounting and the chart validator all
live in the seams of the loop, and each is a line of code here and an argument
with a framework otherwise. Swapping the implementation is one file.

## Evals

The regression net, and the only check that covers all four layers at once.
`scripts/evals/golden/` holds 54 questions a shop owner would actually ask, and
every expected answer is computed from the customer's own system — SQL against
RegisterOne's database, arithmetic over Panel & Pawn's raw spreadsheet — never
from our canonical copy.

```bash
python tasks.py eval                                    # both shops, graded
python tasks.py eval --tenant tsundoku --csv out.csv
python tasks.py eval --id net_sales_december            # just one
```

That choice is the whole point. If the truth came from our own tables, an
adapter that silently dropped every con-booth sale would agree with itself and
the eval would stay green. Computing it from the source means one question
tests the adapter, the semantic layer, the tools and the assistant together.

Haiku grades three things separately — is the number right within tolerance,
were the right tools called, was the answer honest about what it could not do —
and a question passes only if all three hold. It is shown what each tool
actually returned, because "did it invent that product name" is not a question
anyone can answer from the answer alone.

About a third of the set is questions that *should* be refused: margin on a
category with no costs, repeat customers for a shop with no customer column,
what time of day is busiest for an export that records no clock. Those are as
important as the arithmetic — a confident wrong answer is the failure mode this
product has to avoid, and it is the one a naive eval never measures.

Last measured run:

| Shop | Questions | Accuracy | Avg cost | p95 latency |
| --- | --- | --- | --- | --- |
| Panel & Pawn | 20 | 100% | $0.007 | 8.4s |
| Tsundoku & Tabletop | 34 | 97% | $0.013 | 7.0s |

About **1.1 cents a question**, so a shop asking twenty questions a day costs
roughly **$6.50 a month** to answer. That figure is the reason this eval writes
a CSV: pricing a per-shop subscription is guesswork until questions have a
measured cost.

The one Tsundoku failure is the grader's, not the assistant's — on the hardest
question in the set ("how did the con booth do against a normal weekend?") it
called a correct answer invented. Every figure in that answer was checked by
hand against RegisterOne and was exact. It is left in the set, and left failing,
rather than reworded until it passes.

## The dashboard

Four screens, and every figure on them comes out of `api/app/analytics` — the
same functions the assistant's tools call, so a number in a chart and the same
number in a sentence cannot disagree.

```bash
python tasks.py api
python tasks.py web      # http://localhost:5173
```

A dev-only shop switcher sits at the top of the rail, and the shop is part of
the URL (`/tsundoku/dashboard`), so a link to a screen is a link to that shop's
screen.

* **Dashboard** — net sales, orders, average order value and inventory value
  against the period before; net sales by week for a year; top products,
  category mix, the split by location and channel; what is running out and what
  is sitting still; and any charts pinned from a conversation.
* **Assistant** — the same answers `tasks.py ask` gives, streamed, with each
  tool call showing its own working and every chart pinnable to the dashboard.
* **Inventory** — the catalogue, searched, sorted and paged in Postgres, with
  stock per location and unit margin where a cost exists.
* **Data & sync** — which system this shop runs, what the last sync brought in,
  the data quality report in the owner's words, a "Sync now" button, and
  document upload.

What makes it this product's dashboard rather than a generic one is that
**widgets a shop cannot support do not render**. Each one arrives from the API
wrapped: either available with data, or unavailable with a sentence. Panel &
Pawn has one location and one channel, so where Tsundoku shows two splits it
shows

    — Panel & Pawn has a single location, so there is nothing to split by location.

An empty chart with a zero axis would read as "you sold nothing", which is a
different claim from "your system doesn't record that". The same rule runs
through the rest: an item with no cost shows a dash in the margin column rather
than 0%, a caveat from a metric prints under the widget it qualifies, and each
KPI carries its own definition from the semantic layer, shown on hover, so the
owner can check that "net sales" means what their POS means by it.

One `ChartRenderer.vue` draws a `ChartSpec`, whether the spec came from the
assistant mid-answer or from a pinned chart, because they are the same object.
Pinning stores the spec that was validated during that conversation rather than
re-running the question: a pinned chart is a record of what was said.

The chat stream is read off `fetch` rather than `EventSource` — asking a
question is a POST with a body — and the answer's markdown is parsed and then
put through DOMPurify with a tag allowlist before it reaches the page. A model's
output is not trusted input, least of all in a product whose answers quote the
shop's own uploaded documents.

## Build status

| Phase | State |
| --- | --- |
| 1. Repo scaffold | Done |
| 2. Canonical model | Done |
| 3. Test POS (RegisterOne) | Done |
| 4. Adapter framework + conformance suite | Done |
| 5. Mapping adapter (spreadsheet shop) | Done |
| 6. Semantic layer | Done |
| 7. Retrieval | Done |
| 8. Agent | Done |
| 9. Evals | Done |
| 10. Vue dashboard | Done |
| 11. Onboarding playbook | Not started |
