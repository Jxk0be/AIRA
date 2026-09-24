# Panel & Pawn — the mess in the export

The second fake customer exists to prove one thing: that AIRA's core is
genuinely platform-blind. Nothing about this shop resembles RegisterOne. There
is no API, no ids, no cents, no cursors, no customers and no history — just an
`.xlsx` somebody exports from the register and emails over.

Every trap below is one a real shop will hand you, and each breaks a different
naive assumption. `python tasks.py export` prints what the generator actually
produced; `mappings/panel_and_pawn.yaml` is how each one is handled, in config
rather than in code.

| # | Quirk | What it breaks |
| --- | --- | --- |
| 1 | One sheet per year | A reader that takes the first sheet silently loses a year of sales |
| 2 | A `TOTAL` row at the bottom of every sheet | Counting it roughly doubles the shop's revenue |
| 3 | Blank separator rows scattered through | A reader that stops at the first blank row loses everything after it |
| 4 | Dates as `3/7/25` strings | Parsed as ISO, or as day-first, they land in the wrong month |
| 5 | Money as `$1,234.50` | `float("$1,234.50")` throws; stripping only `$` still throws on the comma |
| 6 | A header with a trailing space (`"Sale Amt "`) | A mapping keyed on the exact string silently reads nothing |
| 7 | The same item typed four ways | Four products instead of one, and the shop's best seller looks like four mediocre ones |
| 8 | No SKUs anywhere | The only join between sales and inventory is the name, typed inconsistently on both |
| 9 | 2% of rows have no receipt number | Grouping on it merges them into one enormous sale |
| 10 | 9% of rows have no category | Those products vanish from every category breakdown |
| 11 | Quantities mixed text and number | `"2"` and `2` in one column; either type alone crashes the other |
| 12 | No customer data at all | Repeat-rate questions must be refused, not answered with zero |
| 13 | About half the catalog has no cost | Margin has to report its coverage instead of treating missing as free |
| 14 | **No** board game has a cost | "What's my margin on board games?" has to be refused outright |
| 15 | No history, only a current stock count | Sell-through and days-of-cover are unavailable, not zero |
| 16 | A date but never a time | Hour-of-day questions are unanswerable; sales are placed at midday local |

## How each is handled

**1, 3 and 6 — sheets, blanks and headers.** The mapping matches sheets by
regex (`^Sales \d{4}$`), so next year's sheet needs no edit. Blank rows are
dropped. Headers are matched case- and whitespace-insensitively, which is what
makes `"Sale Amt "` a non-event.

**2 — the totals row.** `settings.skip_rows_matching` lists `TOTAL`, `SUBTOTAL`
and `GRAND TOTAL`. Any row containing one of those in any cell is decoration.

```sql
-- if this ever returns a row, the totals line got in
select name_snapshot from order_lines
where tenant_id = :tenant and lower(name_snapshot) like '%total%';
```

**4 and 5 — dates and money.** `parse_date` takes an ordered list of formats
and tries each; `parse_money` strips currency symbols and thousands separators
and returns an exact `Decimal`. Blank money stays **null**, never zero — a
missing cost is information.

**7 and 8 — the same item typed four ways.** Every key goes through `fold_key`
(trim, collapse internal whitespace, case-fold) before `hash_id` turns it into
a stable synthetic id. Grouping happens on the folded key, and within a group
**the most common spelling wins the display name** — so the shop sees
`Iron Gutter #3`, not `IRON GUTTER  #3`, even though both are in the file. The
same folded key is what joins a sales row to its inventory row for cost.

**9 — receipts that are not there.** Rows sharing a receipt number are one sale.
A row with no receipt number becomes its own single-line order rather than being
merged with every other unnumbered row. Line ids are built on the *group*, not
on the receipt column, so two unnumbered rows selling the same item do not
collide on one id.

**10 — missing categories.** Those rows are skipped for the *category* entity,
so no blank category is created, but the products still import — uncategorised,
and reported as such in the data quality report.

**12 to 16 — what the shop simply does not have.** These are capabilities, not
bugs:

```yaml
has_customers: false           # no customer column exists
has_inventory_history: false   # a stock count, not a ledger
multi_location: false
has_online_channel: false
supports_incremental: false    # a file; every sync re-reads it
has_costs: true                # but only ~57% coverage, and 0% on board games
```

Each `false` switches off a tool for this tenant and a widget on their
dashboard. The agent says "your system doesn't record that" instead of
returning zero, and the conformance suite skips the checks those capabilities
rule out rather than failing them.

## What is signal, not mess

- **Friday and Saturday are busiest**, November and December strongest.
- **Comics outsell board games by volume; board games by value** — so "top
  products" differs depending on whether you ask by units or by dollars.
- **Board games have no costs at all**, which is what makes an honest refusal
  testable rather than hypothetical.
