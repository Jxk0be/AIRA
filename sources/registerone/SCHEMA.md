# RegisterOne — schema and API

RegisterOne is a fictional cloud POS. It exists so that AIRA's adapters can be
tested against something that behaves like a real customer system instead of
like a convenient export.

It is shaped the way Square-style systems actually structure data, because
that shape is what the adapter has to survive:

- a catalog of **items** whose **variations** carry the price, the SKU and the
  stock — not the item
- **inventory counts** per variation per location, as decimal *strings*
- **orders** with line items, where the catalog reference can be null
- **payments** and **refunds** as separate objects, not fields on the order

Two hard rules:

1. **This is a customer source system.** It is read-only to us, and it never
   goes into Supabase.
2. **The adapter talks to the API, never to the database.** If a test can only
   pass by connecting to `registerone_db`, the test is cheating. (RegisterOne's
   *own* tests may use the database for ground truth — that is the point of
   ground truth.)

## Conventions

| Thing | How RegisterOne does it | What the adapter owes us |
| --- | --- | --- |
| Ids | Prefixed strings: `LOC_`, `CAT_`, `VEND_`, `ITEM_`, `VAR_`, `CUST_`, `ORD_`, `PAY_`, `REF_`, `ADJ_` | Keep them as `external_id`, unchanged |
| Money | Integer **cents** plus a `currency` column; over the API, `{"amount": 1299, "currency": "USD"}` | `Decimal` **dollars** |
| Quantities | Decimal **strings** (`"3"`, `"0.5"`) | `Decimal` |
| Timestamps | UTC | tz-aware UTC, bucketed in the shop's timezone |
| Deletes | `is_deleted` flag; the row stays and old orders still point at it | Soft delete, still resolvable for history |

## Tables

| Table | Key columns | Why it is shaped this way |
| --- | --- | --- |
| `locations` | `id`, `name`, `timezone`, `status` | Main store plus a "Con Booth" used a few weekends a year. |
| `categories` | `id`, `name`, `parent_id`, `updated_at` | A flat-ish tree. One category is renamed mid-history under the same id. |
| `vendors` | `id`, `name`, `account_number` | The distributors the shop buys from. |
| `catalog_items` | `id`, `name`, `description`, `category_id`, `product_type`, `is_deleted`, `custom_attributes` jsonb, `created_at`, `updated_at` | The "product". **Price does not live here.** |
| `item_variations` | `id`, `item_id`, `name`, `sku`, `upc`, `price_amount` (cents), `currency`, `pricing_type` (`FIXED`/`VARIABLE`), `track_inventory`, `is_deleted`, `updated_at` | Sizes, conditions (NM/LP/MP), editions. `price_amount` is null when `pricing_type = VARIABLE`. |
| `variation_vendor_info` | `variation_id`, `vendor_id`, `unit_cost_amount` (nullable) | Cost lives off to the side and is often simply missing. |
| `inventory_counts` | `variation_id`, `location_id`, `state` (`IN_STOCK`/`SOLD`/`WASTE`), `quantity` (string), `calculated_at` | Current stock per location, per state. |
| `inventory_adjustments` | `id`, `variation_id`, `location_id`, `from_state`, `to_state`, `quantity`, `occurred_at`, `reason` | Receiving, sales, damage, counts. Lets you compute sell-through and days of cover. |
| `customers` | `id`, `given_name`, `family_name`, `email`, `phone`, `reference_id`, `created_at`, `updated_at` | Sparse. Most walk-ins never become a customer. |
| `orders` | `id`, `location_id`, `customer_id` (nullable), `state` (`OPEN`/`COMPLETED`/`CANCELED`), `source` (`POS`/`ONLINE`), `total_money`, `total_tax_money`, `total_discount_money`, `total_tip_money`, `created_at`, `updated_at`, `closed_at`, `version` | All money in integer cents. |
| `order_line_items` | `uid`, `order_id`, `catalog_object_id` (nullable), `name`, `variation_name`, `quantity` (string), `base_price_money`, `total_discount_money`, `gross_sales_money`, `total_money`, `note` | A null catalog id means a custom-amount sale like "Misc singles". |
| `payments` | `id`, `order_id`, `amount_money`, `tip_money`, `source_type` (`CARD`/`CASH`/`EXTERNAL`), `card_brand`, `status`, `created_at` | One order can have split payments. |
| `refunds` | `id`, `payment_id`, `order_id`, `amount_money`, `reason`, `status`, `created_at` | Partial refunds exist, which is exactly where naive "net sales" math breaks. |

### Two additions to the original spec

- **`orders.updated_at`.** The API exposes an `updated_at` range filter and
  incremental sync is meaningless without one. `created_at` is when the sale
  happened; `updated_at` moves when the order is edited or closed.
- **`inventory_counts` carries all three states.** `IN_STOCK`, `SOLD` and
  `WASTE` rows per variation per location, so that "the count is the sum of the
  history" is checkable state by state.

### The arithmetic that has to hold

Every one of these is asserted by `registerone.seed` after every seed, and
again by the API's own tests after every simulated day:

```
IN_STOCK(variation, location) = Σ received − Σ sold − Σ wasted
order.total_money             = Σ(line.gross − line.discount) + tax + tip     (±1¢)
Σ refunds(payment)           ≤ payment.amount_money
IN_STOCK                     ≥ 0
```

Note the payment shape: **`amount_money` excludes the tip.** What the customer
handed over is `amount_money + tip_money`. Adding the tip twice is the single
easiest way to get a revenue number wrong here.

## The API

`http://localhost:8100`, bearer token from `REGISTERONE_TOKEN`.

| Endpoint | Notes |
| --- | --- |
| `GET /v2/locations` | Not paginated; there are two. |
| `GET /v2/catalog/list` | `types=ITEM \| ITEM_VARIATION \| CATEGORY`, **one type per call**. `begin_time` filters on `updated_at`. |
| `GET /v2/inventory/counts` | `location_ids` (comma separated). |
| `GET /v2/inventory/changes` | `location_ids`, `begin_time`, `end_time` on `occurred_at`. |
| `GET /v2/customers` | `begin_time` on `updated_at`. |
| `POST /v2/orders/search` | Body: `location_ids`, `query.updated_at.{start_at,end_at}`, `cursor`, `limit`, `include_canceled`. Line items come nested. |
| `GET /v2/payments` | `begin_time` / `end_time` on `created_at`. |
| `GET /v2/refunds` | Same. |
| `GET /health` | No auth, no rate limit, no faults. |

**Pagination.** Every list endpoint answers `{"objects": [...], "cursor": "..."}`
with at most 100 objects per page. The absence of `cursor` means the end. The
cursor is opaque (base64 keyset, not an offset) — treat it as a token. An
unreadable cursor is a `400` with code `INVALID_CURSOR`.

**Rate limiting.** More than `REGISTERONE_RATE_LIMIT` (default 10) requests per
second gets a `429` with a `Retry-After` header.

**Faults.** About `REGISTERONE_FAULT_RATE` (default 1%) of calls return a `503`
with `Retry-After`, so the adapter has to retry with backoff. Set it to `0` in
tests that are not about retry behaviour.

### Admin endpoints

Bearer `REGISTERONE_ADMIN_TOKEN`; exempt from rate limiting and faults.

| Endpoint | What it does |
| --- | --- |
| `POST /_simulate/day` | Body `{"day": "2026-09-24", "orders": 8}` (both optional). Inserts one more day of orders, line items, payments and inventory changes, then recomputes the affected counts so the invariant holds. This is how incremental sync gets tested. |
| `POST /_simulate/fault-rate` | Body `{"fault_rate": 1.0}`. Test infrastructure: makes retry logic testable on purpose instead of once every hundred calls. |

## Running it

```bash
python tasks.py sources        # docker compose up: registerone_db + registerone_api
python tasks.py seed           # 18 months of history, with a summary and invariant check
python tasks.py simulate-day   # one more day, for incremental sync
python tasks.py sources-test   # the API's own test suite
```

The seed is deterministic for a given `--seed` and `--end-date`. The default end
date is *today*, so the fixture always has fresh data for "last 30 days"
questions; pass `--end-date` when you want byte-identical reruns.
