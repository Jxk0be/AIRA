# The mess, on purpose

Clean fake data proves nothing about working with real customers. Every pattern
below is in the fixture deliberately, because each one is something a real shop
will hand you on day one, and each one breaks a naive adapter in its own way.

`python tasks.py seed` prints the measured share of each after every run, so
this file and the data cannot quietly drift apart. Each row's query is the way
to find it yourself in `registerone_db`.

| # | Quirk | Target | What it breaks if you ignore it |
| --- | --- | --- | --- |
| 1 | Orders with no customer | ~60% | Repeat-rate and customer-count metrics computed over *orders* instead of *identified orders* |
| 2 | Line items with no catalog object | ~4% | Joins to the catalog that silently drop revenue |
| 3 | Items soft-deleted upstream, still on old orders | 8 items | History that loses products, or a foreign key that refuses the sync |
| 4 | A category renamed mid-history, same id | 1 | Category mapping keyed on name instead of id, which splits one category into two |
| 5 | Duplicate customers sharing an email | ~5% | Inflated customer counts, deflated repeat rate |
| 6 | Partial refunds | ~2% of orders | "Net sales" that never subtracts refunds, or subtracts the whole order |
| 7 | Canceled orders | ~1% | Revenue counted from sales that never happened |
| 8 | Split payments (card + cash on one order) | ~3% of orders | Double-counted revenue when summing payments instead of orders |
| 9 | Variations with no unit cost | ~10% | A margin number that quietly treats missing cost as zero cost |
| 10 | Variations priced at the register (`VARIABLE`) | 5 | A null `price_amount` crashing the mapper, or being read as free |
| 11 | Variations low on stock | ~8% | Nothing — this one is there so reorder suggestions have something to find |
| 12 | Variations never sold (dead stock) | ~5% | Nothing — same, for markdown suggestions |

## Finding each one

**1. Orders with no customer.** Most walk-ins never become a customer record.

```sql
select count(*) filter (where customer_id is null)::numeric / count(*) from orders;
```

**2. Custom-amount line items.** Rung up as a bare price — "Misc singles",
"Bulk commons". There is no catalog object behind them, so they can never join
to a product, but the money is real and belongs in net sales.

```sql
select name, count(*), sum(total_money)/100.0 as dollars
from order_line_items where catalog_object_id is null group by 1 order by 2 desc;
```

**3. Soft-deleted items still referenced by old orders.** The shop stopped
carrying a shirt. The POS marked the item deleted. Last spring's receipts still
point at it, and "what sold last spring" has to keep working.

```sql
select count(distinct l.order_id)
from order_line_items l
join item_variations v on v.id = l.catalog_object_id
join catalog_items i on i.id = v.item_id
where i.is_deleted;
```

**4. A category renamed mid-history.** `CAT_TCG` was called `TCG` when the shop
set things up and is called `Trading Cards` now. **Same id.** Every order from
before the rename still belongs under the new name — that is what the owner
expects, and it is only true if you key on id.

```sql
select id, name, updated_at from categories where id = 'CAT_TCG';
```

**5. Duplicate customers.** Same email, different id, and often a different
spelling of the name — someone signed up twice at the register. Matching has to
happen on a normalised email or phone, and the loser has to be *merged*, never
deleted, because old orders still point at it.

```sql
select email, count(*), array_agg(id)
from customers where email is not null
group by email having count(*) > 1;
```

**6. Partial refunds.** A refund is its own record with its own date, and it is
usually for *part* of a payment. Net sales in the refund's month, not the
order's. Treating a refund as a negative order gets both wrong.

```sql
select r.id, r.amount_money, p.amount_money as payment, r.created_at, o.created_at as ordered
from refunds r join payments p on p.id = r.payment_id join orders o on o.id = r.order_id
order by r.created_at desc limit 10;
```

**7. Canceled orders.** They have line items. They have no payments. They are
not revenue.

```sql
select state, count(*), sum(total_money)/100.0 from orders group by 1;
```

**8. Split payments.** One order, card for part and cash for the rest. Summing
payments to get revenue double-counts nothing here — but summing payments
*and* orders does, and pairing each refund to the wrong tender breaks the
refunds-never-exceed-payments check.

```sql
select order_id, count(*), sum(amount_money)/100.0
from payments group by 1 having count(*) > 1 limit 10;
```

**9. Missing unit costs.** The shop never entered a cost for about one in ten
variations. This is *normal data*, not an error. The right behaviour is to
report margin over the lines that do have a cost and say what share that is —
never to treat a missing cost as zero, which reports 100% margin on those.

```sql
select count(*) filter (where vi.unit_cost_amount is null)::numeric / count(*)
from item_variations v left join variation_vendor_info vi on vi.variation_id = v.id;
```

**10. Variable-priced variations.** `pricing_type = 'VARIABLE'` and
`price_amount is null`: the price is set at the register, per sale. The catalog
genuinely cannot tell you what one costs; only the line item can.

```sql
select id, name, price_amount, pricing_type from item_variations where pricing_type = 'VARIABLE';
```

**11 and 12. Low stock and dead stock.** Roughly 8% of variations end the
history at three units or fewer, and roughly 5% have never sold at all despite
sitting on the shelf. These are not traps — they are there so that "what should
I reorder?" and "what has been on the shelf longest?" have real answers.

```sql
-- low
select count(*) filter (where quantity::numeric <= 3)::numeric / count(*)
from inventory_counts where state = 'IN_STOCK';

-- dead
select v.id, i.name from item_variations v join catalog_items i on i.id = v.item_id
where not exists (select 1 from order_line_items l where l.catalog_object_id = v.id);
```

## Patterns that are *not* mess

These are in the data on purpose too, but as signal rather than noise — they are
what makes the fixture's answers checkable:

- **Friday and Saturday are the busiest days**, in the shop's timezone. Anything
  bucketing dates in UTC will smear this and be visibly wrong.
- **November and December spike**, December hardest.
- **Four TCG set releases**, each with a visibly busier week.
- **Nine convention weekends** at `LOC_CON`, with a sealed-product and
  figure-heavy mix quite unlike the main store's.
- **Manga readers come back for the next volume** of the same series, so
  "which series has the most repeat buyers?" has a real answer.
- **TCG singles leave with sleeves** about a third of the time, so basket
  analysis has something to find.
