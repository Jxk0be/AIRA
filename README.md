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

The web app asks you to sign in. Create an account on the sign-in screen — the
local stack confirms addresses automatically — then give it a shop:

```bash
python tasks.py invite you@example.com animanga_knox owner
```

Signing up on its own grants nothing, which is the point: until somebody adds you
to a shop, the app says so rather than showing you one. Once you have a
membership it opens on the first shop you may see, and if nothing is connected
yet it prints the commands that fix that too.

## Tasks

| Command | What it does |
| --- | --- |
| `python tasks.py env` | What the `.env` holds, with secrets masked |
| `python tasks.py export-online` | Regenerate Animanga Knox's CardNexus export |
| `python tasks.py members <tenant>` | Who may open a shop |
| `python tasks.py invite <email> <tenant> [role]` | Add somebody. `owner`, `manager` or `staff` |
| `python tasks.py revoke <email> <tenant>` | Remove somebody. Bites on their next request |
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

The web app reads nothing from that file. In development it calls `/api`, which
Vite proxies to the API on port 8000, and a deployed build points somewhere else
through `VITE_API_BASE` in `web/.env`. It needs two Supabase values of its own to
sign anybody in; a dev build falls back to the local stack's, so there is nothing
to fill in on a fresh clone, and `web/.env.example` documents what a hosted build
needs. Nothing secret belongs in either — anything Vite inlines is in the
bundle, which is why the publishable key is fine there and the secret key never
is.

## Databases

Two different things, kept apart on purpose:

- **Our canonical database** is Supabase Postgres. Locally that is the CLI
  stack on port 54322; the hosted deploy target is the Supabase project
  `AIRetailAssistant`.
- **Customer source systems** are foreign systems we only ever read. The fake
  ones live in `docker-compose.yml` and never go into Supabase, so "can we read
  a system we don't own?" stays an honest question.

## The test customer

**Animanga Knox** is a Knoxville anime, manga and hobby shop, and it runs **two
registers** — which is the whole reason this product exists.

**RegisterOne**, a fictional cloud POS, runs the counter and the convention
booth. Eighteen months of history and a long list of deliberate mess — missing
costs, duplicate customers, partial refunds, custom-amount lines, a category
renamed mid-history.

**CardNexus**, a fictional TCG marketplace, is the online storefront they opened
in July 2026. It has no API on any plan they can afford and hands them one
spreadsheet a month. It is awkward in the opposite direction to the POS:
machine-clean, perfectly consistent, and missing things on purpose — no costs, no
customers, no stock, no refunds, no payouts. Its listing titles are its own and
match nothing in the POS catalogue, which is true to life and an honest limit
worth showing: revenue consolidates across two systems, product identity does
not.

The shop is a two-source tenant by default, so every multi-register path in the
codebase is exercised by simply running the fixtures. It was not, until recently,
and three things were quietly broken as a result — see
[All your registers, one P&L](#all-your-registers-one-pl).

The shop is invented; its catalog is not. Real manga runs at their publishers'
real prices, real figure lines, real Gunpla kit numbers, real card sets and
sealed configurations, real Japanese snacks and soda — 434 products across 546
variations, from a $0.99 Umaibo to a $319.99 scale figure. That range is the
point: a fixture where everything costs about the same hides every bug that
only shows up at the ends.

See [SCHEMA.md](sources/registerone/SCHEMA.md) for the shape and
[QUIRKS.md](sources/registerone/QUIRKS.md) for every trap and how to find it.

```bash
python tasks.py sources   # registerone_db on :5433, mock API on :8100
python tasks.py seed
```

The adapter talks to the mock API at `http://localhost:8100`, never to
`registerone_db`. The API paginates with opaque cursors, rate-limits at 10
requests a second, and fails about 1% of calls with a retryable 503 — because
the real ones do.

The storefront needs no server: it is a file, regenerated on demand. See
[QUIRKS.md](sources/marketplace/QUIRKS.md) for every trap in it and why it is
deliberately small.

```bash
python tasks.py export-online   # writes the CardNexus export
```

```bash
python tasks.py backfill      # both registers, ~30s for 18 months
python tasks.py backfill animanga_knox --source registerone   # just the POS
python tasks.py simulate-day
python tasks.py incremental   # ~5s, only the new day
python tasks.py conformance   # three targets now: two adapters, three registers
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
ctx = await load_context(session, "animanga_knox")          # timezone + capabilities
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

    animanga_knox — ingest (voyage-4@1024)
      products 230   documents 3
      embedded 0   unchanged 236   removed 0
      0 embedding requests, 0 tokens

Search fuses two rankers with reciprocal rank fusion inside one SQL function,
so the dashboard, the agent and the evals cannot drift apart on what "search"
means. The two halves fail in opposite directions, which is the whole argument
for having both: an embedding finds "cosy manga set in a coffee shop" and
returns Delicious in Dungeon, which never mentions cooking; full-text finds
`MNG-JJK-04`,
which an embedding turns to mush.

Measured on the golden sets in `scripts/evals/retrieval/`, hit@5:

| Shop | vector | text | hybrid |
| --- | --- | --- | --- |
| Animanga Knox | 95% | 95% | 95% |
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
python tasks.py ask animanga_knox "How did last December go?"
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
python tasks.py eval --tenant animanga_knox --csv out.csv
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
| Animanga Knox | 34 | 97% | $0.013 | 7.0s |

About **1.1 cents a question**, so a shop asking twenty questions a day costs
roughly **$6.50 a month** to answer. That figure is the reason this eval writes
a CSV: pricing a per-shop subscription is guesswork until questions have a
measured cost.

The one Animanga Knox failure is the grader's, not the assistant's — on the hardest
question in the set ("how did the con booth do against a normal weekend?") it
called a correct answer invented. Every figure in that answer was checked by
hand against RegisterOne and was exact. It is left in the set, and left failing,
rather than reworded until it passes.

## All your registers, one P&L

The thing neither incumbent can ship. Square AI needs Square, Sidekick needs
Shopify, and neither will ever add a competitor's takings to their own. A shop
running a till on the floor and a marketplace online has two dashboards that never
meet, and adds them up by hand on the first of the month.

Every sourced row has always carried a `source`, and row identity is
`(tenant_id, source, external_id)` — so two registers can both call something
SKU-1 without colliding. What was missing was everything above that:

- **`source` is a dimension** in the semantic layer, so `source_breakdown` splits
  net sales per register, and `Filters(sources=...)` narrows any metric to one.
  Exact rather than approximate, unlike a product split: a refund belongs to an
  order and an order came out of exactly one system, so the rows subtract refunds
  properly and **sum to consolidated net sales to the cent**.
- **A register has a name.** `integrations.display_name` — "Front counter",
  "Etsy shop" — written by the platform-aware layer and read as a plain string by
  `app.analytics`, which is not allowed to know what a platform is
  (CLAUDE.md rule 1).
- **`AnalyticsContext.sources`** carries the registers, each with *its own*
  capabilities. The union on the context decides whether a metric can be
  attempted; the per-register capability decides which half of the shop it covers.

Three things were quietly broken, all of them only visible with two registers:

1. **The sync CLI, the Data screen and the worker each stopped at the first
   integration they found.** A shop's second register was never synced. The loop
   now lives once, in `app.sync.sync_tenant`; three copies of it is how that
   happens.
2. **The month-end packet reconciled one register's takings against both
   registers' sales.** `has_payments` is a union, so a till that reports payments
   plus a marketplace export that does not produced takings covering half the shop,
   compared against net sales covering all of it — and told a bookkeeper their
   books were short by the entire marketplace. Takings are now scoped to the
   registers that report them, and reconciled against the matching subset of sales.
3. **A missing margin failed the whole packet.** `has_costs` is a union too, so a
   month whose sales all came through a costless marketplace export raised
   `CapabilityUnavailable` out of `build` and lost every other section with it. It
   now degrades to a note.

Animanga Knox runs two registers out of the box, so
`/animanga_knox/reports?tab=sales` leads with "All your registers" as soon as the
fixtures are synced. The card is hidden for a shop that runs one till, because a
permanent "connect another register" panel is an advert rather than a number.

## Signing in, and who may see what

Authentication is Supabase Auth. The browser signs in against GoTrue and sends
its access token as a bearer; the API verifies that token itself against the
project's published JWKS rather than calling the auth server once per request.
Asymmetric signatures only — a verifier that also accepted HS256 could be handed
a token an attacker signed with the JWKS public key as an HMAC secret.

**A token proves identity and nothing else.** Every authorisation decision is a
row in our own `memberships` table, read on every request. That is deliberate: a
token is refreshed at most once an hour, so a membership carried inside one would
mean revoking somebody's access took up to an hour to bite. Here it takes effect
on their next request.

Three settings, documented in `.env.example`. Only the first needs thought:
`SUPABASE_URL` is the local stack's `http://127.0.0.1:54321` in development and
`https://<project-ref>.supabase.co` when hosted, and it is **not** the same value
in both — a deploy that keeps the loopback address authenticates nobody.
`SUPABASE_JWT_ISSUER` stays blank unless auth moves to a custom domain, and
`SUPABASE_SERVICE_KEY` is only needed by `tasks.py invite --send`.

`GET /health` reports whether auth is configured and whether the JWKS is
reachable, and calls the service degraded when it is not. That is the check a
deploy should read: the alternative symptom is every request answering 401 with
"could not reach the sign-in service", visible only to the one person who cannot
fix it.

`app.accounts.guard` is installed as an application-wide dependency, so **every**
route is private unless its path template is on a short public list (`/`,
`/health`, and the unsubscribe link in our own emails). A route added later is
protected because nobody did anything.
`tests/test_accounts_auth.py` walks the app's routing table and asserts exactly
that, method by method, so forgetting to think about auth fails the suite.

Asking for a shop you are not a member of returns **404, not 403** — the same
answer a shop that does not exist gives, so the difference cannot be used to find
out which slugs are taken.

Three roles, ordered. `owner` does billing, registers and people; `manager` does
the day job — approving a reorder, raising a purchase order, changing who gets
alerted; `staff` reads every screen and asks the assistant, and writes nothing.
`staff` exists because Shopify charges for staff accounts on a $105 plan and we
do not. Every write route carries its requirement in its decorator, and a test
asserts that any write not on an explicit "harmless" list has one.

The first owner of a shop cannot be added through the API, because adding a
member is owner-only and a new shop has no owner:

```bash
python tasks.py members animanga_knox
python tasks.py invite you@shop.com animanga_knox owner
python tasks.py revoke them@shop.com animanga_knox
```

This is also the hand-held half of onboarding. It never creates an account — an
account made from a terminal would have no password and no confirmed address,
and confirming an address is what the auth service is for. It resolves an email
against our `users` table, then against `auth.users`, and says which it found.

### The Data API is closed

PostgREST serves the `public` schema, and Supabase grants `anon` and
`authenticated` on every table `postgres` creates there. So before the
`close_the_data_api` migration, `GET /rest/v1/orders` with the project's
*publishable* key returned other shops' orders — past FastAPI and past every
`tenant_id` filter in `app.analytics`. That key is meant to be public and is in
the web app's bundle.

It is closed in two layers: row-level security on every table with no policies at
all (the correct policy set, since nothing is supposed to reach these tables that
way), and the grants themselves revoked, including the default privileges that
handed them out. Our own connection is `postgres`, which bypasses RLS, so nothing
in the app changed. `tests/test_data_api_is_closed.py` walks the tables we declare
and fails if a new one is missing RLS.

## The dashboard

Four screens, and every figure on them comes out of `api/app/analytics` — the
same functions the assistant's tools call, so a number in a chart and the same
number in a sentence cannot disagree.

```bash
python tasks.py api
python tasks.py web      # http://localhost:5173
```

A dev-only shop switcher sits at the top of the rail, and the shop is part of
the URL (`/animanga_knox/dashboard`), so a link to a screen is a link to that shop's
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
Pawn has one location and one channel, so where Animanga Knox shows two splits it
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
| 12. Auth and accounts | Done |
| 13. Multi-source consolidation | Done |
