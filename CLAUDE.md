# AIRA — standing rules

AIRA is a multi-tenant AI analyst for small retail shops. It works with any POS or
store platform because the AI core only ever sees one canonical data model;
each new customer means writing or configuring an adapter, not touching the AI.

## Architecture in one line

    customer system -> adapter -> canonical schema -> analytics / RAG / agent -> UI

`api/app/connectors/` is the only code that knows a platform exists.

## Standing rules

1. **The AI layer is platform-blind.** `api/app/agent`, `api/app/rag` and
   `api/app/analytics` read the canonical schema only. They must never import
   from `api/app/connectors` or know which platform a tenant uses.
2. **Customer source systems are read-only.** We never write to them.
3. **Tenant isolation.** Every canonical table holding shop data has a non-null
   `tenant_id`, and every query filters by it. `users` is the one exception and
   has a comment saying why; it holds no shop data and reaches none except
   through `memberships`, which is scoped.
4. **The LLM never writes raw SQL.** Numbers come from typed analytics functions
   in `api/app/analytics`.
5. **Money and time.** Money is `Decimal` in dollars inside our system (sources
   may use integer cents; adapters convert). Timestamps are tz-aware UTC. Each
   tenant has a timezone used for display and date bucketing.
6. **Database.** Supabase Postgres, accessed from FastAPI via SQLAlchemy 2 async
   over `DATABASE_URL` (not supabase-py). Alembic owns our tables in the
   `public` schema and must ignore Supabase-managed schemas (auth, storage,
   realtime, graphql, extensions, vault, ...). Development runs against the
   local CLI stack; the hosted deploy target is the Supabase project
   **AIRetailAssistant** (`tshakhmvyxqnoytlfklw`, us-west-2). Never point a
   seeding or test script at the hosted project.
7. **Embeddings.** Voyage `voyage-4` at 1024 dims. Documents are embedded with
   `input_type="document"`, search queries with `input_type="query"` — never mix
   these up. Every stored vector records its `embedding_model`, and search
   filters by it: vectors from different models are not comparable.
8. **Pin dependency versions.** Check the installed version's docs before using
   LangChain, LangGraph, the voyageai SDK or any vendor API.
9. **Tooling.** Python: ruff, mypy, pytest. TypeScript: strict mode, eslint.
10. **Capabilities, not assumptions.** Adapters declare what they can provide
    (costs, customers, inventory history, multi-location, online channel,
    incremental sync). Tools degrade honestly instead of inventing numbers.
11. **Identity is a token; permission is a row.** Supabase Auth issues the token
    and `app/accounts/tokens.py` verifies it (asymmetric only, issuer and
    audience checked, `user_metadata` never read). Everything after that is a
    `memberships` row read on the request — never a claim in the token, so
    revoking access bites immediately. `app/accounts/guard.py` is installed
    app-wide: every route is private unless its path is on its public list, and
    a shop you are not a member of answers 404, not 403.
12. **Nothing reaches our tables except through us.** The Supabase Data API is
    closed on every table we own: RLS on with no policies, and the `anon` and
    `authenticated` grants revoked. A new table needs the same, and
    `tests/test_data_api_is_closed.py` fails until it has it.
13. **A tenant has registers, plural.** Never `.first()` an integration —
    `app.sync.sync_tenant` owns the loop over them. A capability on the context
    is the **union** across registers, which says whether a metric can be
    attempted; `ctx.sources_with(cap)` says which registers it actually covers,
    and any figure being reconciled needs the second one. Consolidated net sales
    must equal the sum of `source_breakdown`'s rows to the cent.

## Layout

    api/          FastAPI + SQLAlchemy 2 async, managed by uv
      app/accounts/     who is asking, and which shops they may ask about.
                        `guard.py` is the only place a request is let in
      app/canonical/    the adapter contract: Pydantic models + SQLAlchemy tables
                        (read docs/canonical-model.md before writing an adapter)
      app/connectors/   adapters (the only platform-aware code) + sync engine
      app/analytics/    the semantic layer: one definition per metric
      app/rag/          embeddings, ingest, hybrid search
      app/agent/        the Claude agent, behind our own Assistant interface
      app/dashboard/    HTTP for the UI screens. `routes.py` is canonical-only;
                        `operations.py` is the Data & sync screen and may know
                        about connectors, like the sync CLI does
    web/          Vue 3 + TypeScript + Vite + Tailwind v4 (Pinia, vue-echarts)
    sources/      fake customer systems used for testing (NOT in Supabase).
                  Animanga Knox runs two of them — a POS and a marketplace
                  export — so multi-register paths are exercised by default
    supabase/     Supabase CLI project — our canonical DB
    scripts/      seeding and eval scripts
    docs/         canonical-model.md and friends

## Tasks

There is no `make` on this machine, so the task runner is the stdlib-only
`tasks.py` at the repo root:

    python tasks.py setup      # uv sync + npm install
    python tasks.py db         # supabase start (via npx) — our canonical DB
    python tasks.py db-stop
    python tasks.py migrate    # alembic upgrade head
    python tasks.py members <tenant>            # who may open a shop
    python tasks.py invite <email> <tenant> [owner|manager|staff]
    python tasks.py revoke <email> <tenant>
    python tasks.py api
    python tasks.py web
    python tasks.py build      # production build of the web app
    python tasks.py test
    python tasks.py lint       # ruff + eslint
    python tasks.py typecheck  # mypy + vue-tsc
