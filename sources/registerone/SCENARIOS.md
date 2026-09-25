# Planted on purpose, for the detectors to find

`QUIRKS.md` is about surviving messy data. This file is the other half: each
row below is a specific situation a specific feature claims it can spot, put
into the fixture at a known size so the claim is testable rather than
plausible.

Every one of them obeys RegisterOne's own invariants — a count still equals the
sum of its movement history, a refund never exceeds its payment, nothing goes
negative. A fixture that has to break its own rules to make a feature look good
is not evidence of anything.

`python tasks.py seed` measures all of these back **out of the database** after
every run and prints them, so this file and the data cannot quietly drift
apart. The code is `registerone/scenarios.py`.

| # | Scenario | Size | Which feature it is for |
| --- | --- | --- | --- |
| 1 | Items that will run out inside ten days | 8 variations | Reorder assistant |
| 2 | Dead stock, never sold, still on the shelf | 12 items, ~$1,500 at cost | Dead stock rescue |
| 3 | Stock that left as a sale with nothing rung up | 6 units, one variation | Possible-shrink alert |
| 4 | A Saturday well below a normal Saturday | ~four fifths down | Sales anomaly alert |
| 5 | A week where refunds spike | ~16 extra refunds, ~15% of that week's sales | Refund spike alert |

Two things the fixture deliberately does **not** contain, because they are
absences rather than events: a lead time on any supplier, and a case size. No
POS we have met records either, which is why the reorder assistant assumes a
two-week lead time and says so, and why those fields are editable in the app.

---

## 1. Eight items about to run out

Chosen from variations that actually sell — at least three units in the last
four weeks — and then trimmed to between three and nine days of cover at that
rate.

Deliberately **not** trimmed to zero or one. An item with one left is obvious
to anyone walking past the shelf; an item with six left that sells one a day is
the one a reorder assistant earns its keep on, and it is the case a shop owner
cannot spot by eye.

Trimming takes units off the *most recent* shipments first, never below one
unit per shipment. Taking them off an early delivery would send the running
balance negative halfway through the history, which the fixture's own invariant
check would catch.

```sql
-- what is left, against what it sells
select i.name, c.quantity::numeric as on_hand,
       sum(l.quantity::numeric) filter (
         where o.created_at > now() - interval '28 days') as units_28d
from inventory_counts c
join item_variations v on v.id = c.variation_id
join catalog_items i on i.id = v.item_id
left join order_line_items l on l.catalog_object_id = v.id
left join orders o on o.id = l.order_id and o.state <> 'CANCELED'
where c.state = 'IN_STOCK' and c.location_id = 'LOC_MAIN'
group by 1, 2
having sum(l.quantity::numeric) filter (
         where o.created_at > now() - interval '28 days') >= 3
order by c.quantity::numeric / nullif(
  sum(l.quantity::numeric) filter (
    where o.created_at > now() - interval '28 days') / 28.0, 0) asc
limit 12;
```

**A feature that passes:** the reorder assistant names all eight, gives each a
quantity, and explains each one in a sentence containing its rate of sale and
what is left.

## 2. Twelve dead-stock items, about $1,500 at cost

Twelve variations that have never sold once, received as a single shipment, and
still sitting there. Their quantities are set so the total at cost lands within
a few dollars of $1,500 — the figure the feature quotes back to the owner, so
it is chosen here rather than left to fall out of the simulation. A test that
asserts "about fifteen hundred dollars" against a number nobody picked is a
test that breaks the first time the seed changes.

```sql
select i.name, c.quantity::numeric as on_hand,
       vi.unit_cost_amount / 100.0 as unit_cost,
       c.quantity::numeric * vi.unit_cost_amount / 100.0 as tied_up
from inventory_counts c
join item_variations v on v.id = c.variation_id
join catalog_items i on i.id = v.item_id
join variation_vendor_info vi on vi.variation_id = v.id
where c.state = 'IN_STOCK' and c.quantity::numeric > 0
  and not exists (select 1 from order_line_items l where l.catalog_object_id = v.id)
order by tied_up desc;
```

**A feature that passes:** dead stock rescue finds them, totals them at cost
rather than at retail, and gives each one a specific play — a markdown with the
break-even price, a bundle with something that is selling, or a return to the
two suppliers whose notes say they take returns.

## 3. Six units of shrink

One variation loses six units through a `SOLD` inventory movement that has no
order line behind it, four days before the end of the history.

This is what shrink actually looks like in a POS that reconciles its counts:
the stock movement is there, the receipt is not. It is **not** a broken total —
the count still equals the sum of its history — so a detector has to find it by
comparing the movement history against the order lines. Anything that finds it
by spotting a mismatched count is finding the wrong thing.

```sql
select v.id, i.name,
       sum(a.quantity::numeric) filter (where a.to_state = 'SOLD') as moved_out,
       coalesce((select sum(l.quantity::numeric)
                 from order_line_items l
                 join orders o on o.id = l.order_id and o.state <> 'CANCELED'
                 where l.catalog_object_id = v.id), 0) as rang_up
from item_variations v
join catalog_items i on i.id = v.item_id
join inventory_adjustments a on a.variation_id = v.id
group by 1, 2
having sum(a.quantity::numeric) filter (where a.to_state = 'SOLD') >
       coalesce((select sum(l.quantity::numeric)
                 from order_line_items l
                 join orders o on o.id = l.order_id and o.state <> 'CANCELED'
                 where l.catalog_object_id = v.id), 0);
```

**A feature that passes:** the shrink alert names one item, six units, and a
dollar figure at cost — and says a stock count would settle it, because a
miskeyed adjustment looks identical from here.

## 4. One very quiet Saturday

Three weeks before the end of the history, one Saturday keeps a third of its
takings. Its biggest baskets are removed until that lands, so what is left
reads like a genuinely dead day rather than like one unexplained large sale.
The removal happens before the inventory is built, so the stock history is
derived from what is left and nothing needs patching up afterwards.

**Why a third and not "45% below".** An ordinary Saturday at this shop ranges
from about $230 to about $620 — the weekend swing at a shop this size is
enormous. A day 45% below the median is still inside that range, and a
detector that flagged it would be flagging a third of the Saturdays in the
year. Finding that out is itself useful: it is the reason the anomaly detector
compares against a spread taken from the days *below* the median rather than a
symmetric one, and the reason it is quiet most weeks. For the scenario to be a
scenario, the day has to be one anybody would agree is alarming.

The day is chosen far enough back that the baseline around it is full of
ordinary Saturdays, and recent enough that anything looking at the last month
still sees it. It is excluded from the baseline it is judged against, because a
day that helps set its own median cannot look unusual.

**A feature that passes:** the sales anomaly detector flags that day against
other Saturdays — not against an average day, which would flag every Tuesday —
and reports the dollar difference. Across seven months of the fixture it finds
roughly a dozen days in total, which is the rate an owner will keep reading.

## 5. One week of refunds

Sixteen extra refunds land inside one week, five weeks before the end, drawn from
sales in the three weeks before it. That is how a real refund week works: a bad
batch, or a display model everybody brings back at once.

Planted as a count of refunds rather than as a raised probability, because a
refund is dated when the money goes back, not when the sale happened — raising
the chance on orders *placed* that week would put the refunds one to three
weeks later, which is a different week and not the scenario. Each one still
fits inside its payment.

```sql
select date_trunc('week', created_at at time zone 'America/New_York')::date as week,
       count(*), sum(amount_money) / 100.0 as refunded
from refunds group by 1 order by 3 desc limit 6;
```

**A feature that passes:** the refund spike alert flags that week, gives the
share of sales it represents against the shop's own normal share, and does not
flag any other week.
