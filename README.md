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

The web app's home screen is the API health check: it shows whether the
database is reachable and whether pgvector is enabled.

## Tasks

| Command | What it does |
| --- | --- |
| `python tasks.py env` | What the `.env` holds, with secrets masked |
| `python tasks.py setup` | Install Python and Node dependencies |
| `python tasks.py db` / `db-stop` / `db-reset` | Local Supabase stack |
| `python tasks.py sources` / `sources-stop` | Fake customer systems (docker compose) |
| `python tasks.py seed` | 18 months of RegisterOne history, with a summary |
| `python tasks.py simulate-day` | One more day of sales, for incremental sync |
| `python tasks.py sources-test` | RegisterOne's own API test suite |
| `python tasks.py backfill [tenant]` | Sync a tenant from its source system |
| `python tasks.py incremental [tenant]` | Sync only what changed |
| `python tasks.py conformance` | The suite every adapter must pass |
| `python tasks.py migrate` | `alembic upgrade head` |
| `python tasks.py revision -m "..."` | Autogenerate a migration |
| `python tasks.py api` / `web` | Dev servers |
| `python tasks.py test` / `lint` / `typecheck` | Checks |

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
| `api/app/agent/` | The Claude agent, behind our own Assistant interface |
| `web/` | Vue 3 + TypeScript + Vite + Tailwind v4 |
| `sources/registerone/` | A fictional cloud POS to test adapters against — deliberately *not* in Supabase |
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

## Build status

| Phase | State |
| --- | --- |
| 1. Repo scaffold | Done |
| 2. Canonical model | Done |
| 3. Test POS (RegisterOne) | Done |
| 4. Adapter framework + conformance suite | Done |
| 5. Mapping adapter (spreadsheet shop) | Not started |
| 6. Semantic layer | Not started |
| 7. Retrieval | Not started |
| 8. Agent | Not started |
| 9. Evals | Not started |
| 10. Vue dashboard | Not started |
| 11. Onboarding playbook | Not started |
