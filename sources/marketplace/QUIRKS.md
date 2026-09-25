# CardNexus — every trap in the export, and how to find it

Animanga Knox's **second register**. The counter runs RegisterOne; this is the TCG
marketplace the shop started selling on in July 2026, which has no API on any plan
they can afford and hands them one spreadsheet a month.

It is deliberately not a second Panel & Pawn. That export is a badly typed till
report — no ids, three spellings of every item, dollar signs and `3/7/25` dates.
This one is machine-written and perfectly consistent. **The awkwardness here is
what is absent, not how it is typed**, which is exactly how a marketplace
integration really behaves.

The mapping that reads it is [`mappings/animanga_knox_online.yaml`](../../mappings/animanga_knox_online.yaml),
and it is the entire integration — no code was written for this source.

```bash
python tasks.py export-online
python tasks.py backfill animanga_knox --source animanga_knox_online
```

## What is missing, and what each absence costs

| Absent | Declared | What it means downstream |
| --- | --- | --- |
| Cost of any kind | `has_costs: false` | Margin covers the counter only. The month-end packet states the coverage rather than spreading it across sales it cannot account for. |
| Customer identity | `has_customers: false` | There *is* a buyer handle on every row. It is pseudonymous and cannot be joined to the person who walks into the shop, so it stays unmapped — counting it would inflate the customer count and corrupt repeat-rate. |
| Stock levels | `has_inventory_history: false` | The marketplace reports what left, never what is left. No `inventory_levels` are synthesised; inventing a shelf from what sold would be a guess. |
| Payouts | `has_payments: false` | Takings cover the counter only. This is the one that used to break the packet — see below. |
| Refunds | *(not a capability)* | They arrive on a separate statement the shop has never sent us, so online net sales equal online gross sales. |
| Time of day | *(see notes)* | A settlement date, no clock. Hour-of-day questions stay answerable for the shop floor and unanswerable online. |

## The traps

**Shipping and fees are columns, and neither is revenue.** `Shipping Paid` is what
the buyer paid for postage; `Marketplace Fee` is the commission. Both are in the
file so the shop can see them, and both are deliberately unmapped. Counting either
into net sales overstates the shop's takings — the fee especially, because it is
money going *out*.

**Listing titles match nothing in the POS.** "Charizard ex - 199/165" is the
marketplace's name for a card RegisterOne calls something else entirely. Product
identity is `(tenant_id, source, external_id)`, so the same card is two products.
That is correct and it is a real limit: a best-sellers list can show both names as
separate rows. Revenue consolidates; catalogues do not.

**Categories are the marketplace's vocabulary, not the shop's.** They are named
differently on purpose — "Sealed Boxes" here, "Sealed Product" at the counter — so
that two systems' categories do not render as duplicate rows that look like a bug.
A partial category search for "sealed" therefore matches both and the assistant
says so rather than claiming neither exists.

**Condition is part of the variant.** One product per card, one variant per
condition it sold in. A Near Mint Charizard and a Moderately Played one are
different sellable things at different prices, so a line's identity is the order
number *plus* the folded title *plus* the condition — keying on the title alone
would silently merge two lines of the same card into one.

**Two rows have no `Set`.** The marketplace lets a seller list without one. They
still carry a category, so they are not uncategorised; nothing should crash on the
blank.

**Multi-line orders share an order number**, and postage is charged once per
order, on its first line only.

## Why it is small

About two and a half orders a day of mostly cheap singles — around a seventh of
what the counter takes. That is realistic for a storefront a few months old, and
it is load-bearing for the fixture:

* **December 2025 stays the shop's best month.** `best_month` in the golden
  questions asserts it, and a storefront doing counter-scale volume would
  overtake it.
* **Every historical eval question is untouched.** They all ask about windows
  that closed before the storefront opened on 1 July 2026, which is why `OPENED`
  is a fixed date rather than one relative to today.

## Why the location is synthesised

The storefront is not a place, but it gets a location row of its own —
`Animanga Knox Online` — and that is not cosmetic. The anomaly detectors baseline
**per location**. Without a separate one, online trade would be folded into the
counter's normal Saturday and the planted scenarios in
[`sources/registerone/SCENARIOS.md`](../registerone/SCENARIOS.md) would stop being
findable.
