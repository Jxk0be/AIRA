# The canonical model

This is the contract. Every adapter — the code adapter for a cloud POS, the
config-driven mapping adapter for a spreadsheet — has one job: turn a customer's
system into the entities described here. Everything downstream (analytics, RAG,
the agent, the dashboard, the evals) is written once against this model and
never learns which platform a tenant is on.

If you are about to write a new adapter, read this file first. The Pydantic
models in [`api/app/canonical/models.py`](../api/app/canonical/models.py) are the
literal contract; the tables in
[`api/app/canonical/tables.py`](../api/app/canonical/tables.py) are where the
sync engine puts the results.

## The rules the whole model rests on

1. **Money is `Decimal` in dollars.** Sources that report integer cents (most of
   them) are converted by the adapter. Nothing past the adapter layer should
   ever see a cent integer. Stored as `NUMERIC(14,4)`: four decimal places
   because unit prices on fractional quantities need them, while totals still
   reconcile to the cent.
2. **Timestamps are timezone-aware UTC.** Every one of them. The tenant's
   timezone is used for *display* and for *date bucketing* — "December sales"
   means December in the shop's local time, not UTC.
3. **`tenant_id` on everything, and every query filters by it.** There is no
   such thing as a cross-tenant read.
4. **Identity is `(tenant_id, source, external_id)`.** `external_id` is the
   source system's own id. It has to be stable across syncs, because that triple
   is what makes a re-sync an update instead of a duplicate.
   Every sourced row also carries **`source_updated_at`** — when the source last
   changed it. The incremental watermark is the highest value seen in a run,
   never our own clock, so a POS with a slow clock still syncs correctly.
5. **Deletes are soft.** A product deleted in the POS today still has to resolve
   for an order line from last March. `deleted_at` is set; the row stays.
6. **Enumerated values are stored as the lowercase strings written here** —
   `in_store`, `canceled`, `received` — and a CHECK constraint enforces the list.
   Hand-written SQL, evals and the agent's tools all compare against these.

## Capabilities

Not every system can answer every question. A cash-only shop has no customer
records. A spreadsheet export often has no costs. Rather than assume, each
integration stores a `Capabilities` object:

| Capability | Meaning | What it gates |
| --- | --- | --- |
| `has_costs` | Unit costs are available for at least some variants | COGS, gross margin |
| `has_customers` | The system identifies customers | Repeat rate, new vs returning |
| `has_inventory_history` | Stock movements, not just current counts | Sell-through, days of cover |
| `multi_location` | More than one selling location | Location breakdowns |
| `has_online_channel` | Sales arrive from more than the register | Channel breakdowns |
| `supports_incremental` | Records can be fetched by "changed since" | Incremental sync; otherwise full re-pull |

A false capability means the relevant tool is not registered for that tenant at
all, and the dashboard widget does not render. The agent says "your system
doesn't track cost, so I can't compute margin" instead of returning zeros.
Capabilities live in the canonical layer precisely so analytics and the agent can
read them without importing anything from `app.connectors`.

## Entities

### Tenancy

**`tenants`** — one shop. `slug` is the human handle used on the CLI
(`--tenant tsundoku`). `timezone` is an IANA name and is the timezone every date
bucket is cut in. `currency` is ISO-4217. `settings` is free-form jsonb for
per-shop preferences (low-stock thresholds, dead-stock window).

**`integrations`** — a tenant's connection to one source system. `adapter` names
the registered adapter (`registerone`, `mapping`, `shopify`); `source` is the
string stamped onto every row this integration syncs, and is part of row
identity. `config` holds non-secret settings (base URL, file path, mapping file);
`secret_ref` is a *pointer* into a secret store, never the credential itself.
`capabilities` is the object above.

### Catalog

**`locations`** — a place that sells. The main store, a second shop, a
convention booth. Has its own `timezone`, because a booth in another state
really does close at a different hour.

**`categories`** — a tree via `parent_id`. Expect a shallow, messy one, and
expect categories to be renamed mid-history: the rename is not a new category,
it's the same `external_id` with a new name, and old orders roll up under the
new name. That is the behaviour owners expect.

**`products`** — the thing a customer would name. "Volume 3 of that series."
It has no price: price lives on the variant, which is how real POS catalogs are
shaped. `attributes` is jsonb for whatever source-specific fields are worth
keeping (condition, edition, release date).

**`variants`** — the sellable unit, and the thing that actually has `sku`,
`barcode`, `price`, `cost`, and a stock level. **A single-variant product still
gets exactly one variant row**, so every downstream query has one shape to deal
with. `price` is nullable for variable-priced items (price set at the register).
`cost` is nullable and often missing — that is normal data, not an error, and
margin metrics report their coverage rather than guessing.

### Inventory

**`inventory_levels`** — stock on hand right now, per variant per location, with
the `as_of` timestamp the source gave us. One row per (variant, location).

**`inventory_movements`** — optional history: received, sold, returned,
adjusted, damaged, transferred, counted. Only sources with
`has_inventory_history` fill this. Without it, sell-through and days of cover
are not available, and the tools that need them are not registered.

### Sales

**`customers`** — sparse by nature: most walk-ins never become a customer
record. `email_normalized` (lowercased) and `phone_normalized` (digits only) are
written by the sync engine's dedupe step, and matching happens on those, never
on the raw values. Duplicates are merged by setting `merged_into_id` on the
loser and pointing it at the survivor — **never by deleting**, because old orders
must keep resolving to the row the POS actually referenced.

**`orders`** — one sale. `status` is `open` / `completed` / `canceled`.
`channel` is `in_store` / `online` / `event` / `other` — the normalisation that
lets "how did the con booth do?" be a question at all. `placed_at` is when it
happened; `closed_at` when it was settled.

Money is `subtotal`, `discount_total`, `tax_total`, `tip_total`, `total`, and
**`subtotal` is gross merchandise value, before discounts**. The arithmetic
every adapter has to satisfy, and the conformance suite checks to the cent:

```
total = subtotal - discount_total + tax_total + tip_total
```

A source that reports a discounted subtotal has to add the discounts back. A
source whose payment amount excludes the tip has to be careful not to count the
tip twice — that one is the easiest way to get a revenue figure wrong.

**`order_lines`** — `variant_id` is **nullable**, because custom-amount lines
("Misc singles", rung up as a price with no catalog item) are real and common.
Those lines carry `name_snapshot` and nothing else to join on. `name_snapshot`
and `unit_cost_snapshot` exist because the receipt is history: renaming or
deleting a product upstream must not rewrite what a past sale said or cost. A
null `unit_cost_snapshot` means no cost was known *at the time of sale*; margin
counts that line as uncovered rather than back-filling today's cost.

**`refunds`** and **`refund_lines`** — partial refunds are where naive "net
sales" math breaks, so refunds are their own records with their own
`occurred_at`, not a negative order. Line detail is stored when the source has
it and omitted when it doesn't.

### Sync plumbing

**`raw_records`** — the landing table. Adapters write the untouched source
payload here *before* mapping, keyed by `(tenant_id, source, entity,
external_id)`. Two jobs: re-run a mapping without re-fetching from a
rate-limited API, and settle "where did this number come from?" against exactly
what the POS sent.

**`sync_state`** — per-entity cursor, so incremental sync knows where it left
off. **`sync_runs`** — one row per run with mode, status, counts, duration and
errors. **`data_quality_reports`** — what we had to work with: missing costs,
uncategorized products, custom-amount share of sales, orders that don't
reconcile, negative stock, duplicates merged, stale data. Analytics reads it for
caveats; the dashboard shows it to the owner in plain language.

### Retrieval

**`documents`** — uploaded prose: policies, FAQs, event schedules.

**`chunks`** — one embedded passage, from either a product or a document.
`embedding` is `vector(1024)`, and 1024 is not arbitrary: pgvector's HNSW index
cannot index a plain `vector` column above 2,000 dimensions. `fts` is a stored
generated `tsvector` over `content`. `content_hash` (sha256 of content +
embedding model) lets ingest skip unchanged chunks, so a no-op re-ingest costs
zero embedding calls. `embedding_model` is stored on every row and filtered on
in every search, because **vectors from different models are not comparable**.

> **Deviation from the build guide, on purpose.** The guide specifies a unique
> index on `(tenant_id, source, source_id)`. Documents split into several
> passages, so that unique constraint would allow only one chunk per document.
> The identity here is `(tenant_id, source, source_id, chunk_index)`; product
> chunks are always index 0.

### Conversations

**`conversations`**, **`messages`** (with `tool_calls` and `charts` alongside
the text), and **`saved_charts`** for charts the owner pinned to the dashboard.

## How a sync runs

`api/app/connectors/sync.py` walks the entities in dependency order —
locations, categories, products, variants, customers, orders, refunds,
inventory levels, inventory movements — because each may reference the ones
before it by `external_id`, and the engine resolves those into foreign keys as
it goes.

Each record's untouched payload lands in **`raw_records` first**, then it is
mapped and upserted on `(tenant_id, source, external_id)`. Two modes:

| | Backfill | Incremental |
| --- | --- | --- |
| What it pulls | everything | records changed since the watermark |
| Watermark | ignored, then set | `max(source_updated_at)` from the last run |
| Rows the source stopped returning | soft-deleted | **left alone** |

That last row is not a detail. Sweeping on an incremental pull would soft-delete
the entire shop the first night it ran, because "not returned" means "did not
change", not "gone".

Stock **levels** are the exception to incremental: they are current state, not
history, so they are pulled in full every run. A level that did not change is
still the level we have to be holding.

## What an adapter actually yields

Adapters yield the Pydantic models, not table rows. They never set `tenant_id`
or `source` — the sync engine stamps those. References between entities are by
the *other* entity's `external_id` (`CanonicalVariant.product_external_id`), and
the sync engine resolves them into uuid foreign keys.

```python
CanonicalOrder(
    external_id="ORD_00412",
    location_external_id="LOC_MAIN",
    customer_external_id=None,          # cash walk-in
    status=OrderStatus.COMPLETED,
    channel=Channel.IN_STORE,
    subtotal=Decimal("24.00"),
    discount_total=Decimal("2.40"),
    tax_total=Decimal("2.06"),
    tip_total=Decimal("0"),
    total=Decimal("23.66"),
    placed_at=datetime(2026, 3, 7, 18, 42, tzinfo=timezone.utc),
    lines=[
        CanonicalOrderLine(
            external_id="ORD_00412:1",
            variant_external_id="VAR_0881",
            name_snapshot="Hollow Lantern Vol. 3",
            quantity=Decimal("2"),
            unit_price=Decimal("12.00"),
            discount=Decimal("2.40"),
            unit_cost_snapshot=Decimal("7.20"),
        )
    ],
)
```

## The invariants an adapter has to hold

These are exactly what the conformance suite checks, and they are the objective
answer to "is this adapter done?":

- Money arrives as `Decimal` dollars; timestamps are tz-aware UTC.
- `external_id`s are stable across runs.
- Re-syncing is idempotent — same input, same row count, same values.
- `since` returns only records newer than the cursor.
- Order totals reconcile: lines + tax + tips − discounts, within one cent.
- Refunds never exceed what was paid.
- Soft-deleted products still resolve for historical order lines.
- Declared capabilities match reality: a source claiming `has_costs` has costs —
  and a source that does *not* claim one has not quietly invented it either.
- Duplicate customers are merged by pointing at a survivor, never deleted, and
  never merged into another duplicate.
- `raw_records` holds a payload for every record that was synced.
- Dates bucket in the shop's timezone, not UTC.

The suite lives in `api/tests/conformance/` and is parametrised by target;
adding a new adapter to it is a single entry in `targets.py`. Run it with
`python tasks.py conformance`.

## Nothing here is shaped like one POS

A deliberate check on this document: it names no vendor, no `catalog_object_id`,
no cents, no `IN_STOCK` state, no cursor format. The shapes it *does* commit to
— price on the variant rather than the product, nullable variant on an order
line, refunds as separate records, capabilities instead of assumptions — are
shapes that show up across Square, Shopify, Clover, Lightspeed and a spreadsheet
alike, and each one exists to survive a specific kind of real-world mess.
