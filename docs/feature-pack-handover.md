# Feature pack — where this got to

Written at the end of the first build session and updated at the end of the
second. Delete it once the list at the bottom is empty.

## What was built

Seven of the eight phases in the feature pack. **Phase 5, "next volume is in",
was dropped on request** — messaging a shop's customers means running a consent
regime (prior express consent, a working STOP, A2P 10DLC registration) and that
is not a commitment this product is taking on. It was built and then removed;
`20260924_0600_drop_series_feature.py` is the migration that took its tables
back out, and it is reversible if that decision ever changes.

| Phase | Lives in | State |
| --- | --- | --- |
| 1 Foundation | `app/insights`, `app/jobs`, `app/notify` | done |
| 2 Weekly digest | `app/digest` | done |
| 3 Reorder assistant | `app/reorder`, `app/analytics/demand.py` | done |
| 4 Dead stock rescue | `app/deadstock`, `app/analytics/stale.py` | done |
| 5 Next-volume alerts | — | **dropped on request** |
| 6 Anomaly alerts | `app/anomalies` | done |
| 7 Month-end packet | `app/monthend`, `app/reporting` | done |
| 8 Staffing hints | `app/staffing` | done |

Verified end to end against the RegisterOne fixture: all eight detectors run,
every planted scenario in `sources/registerone/SCENARIOS.md` is found, the five
new screens render on real data, and the digest's number validator was observed
catching the model inventing `$183.75` and falling back to the plain template.

## Where the second session got to

Everything on the first session's gap list is closed, and the screens it
listed as missing are built.

* `python tasks.py test` — **312 passed, 5 skipped**, and green on three
  consecutive runs (it used to degrade its own fixture; see below).
* `python tasks.py conformance` — **37 passed, 5 skipped**.
* `python tasks.py lint` and `python tasks.py typecheck` — clean on both sides.
  Ruff and mypy had never been run over this code; that is done, and
  `pyproject.toml` now records the two deliberate exceptions (en dashes and
  curly quotes we write on purpose, and FastAPI's parameter markers, which are
  declarations rather than mutable defaults).
* The worker has been left running for an extended stretch and does its rounds.

### The thing worth knowing: the fixture used to erode itself

The first session read seven failures as "the fixture was seeded with the wrong
`--end-date`" and expected a reseed to clear them. A reseed did not clear them,
and the real cause is worth writing down because it will save someone an
afternoon.

`test_sync_incremental` asks RegisterOne to simulate a day, so **every run of
the suite leaves the fixture one day further from its seed.** Three things
depended on that not happening:

1. **A test that guessed where a scenario was.** The quiet-Saturday tests found
   the planted Saturday by subtracting three weeks from the fixture's last sale
   day, which is only correct on a freshly seeded fixture. They now search the
   candidate Saturdays and identify the planted one by its depth. The shrink
   test had the same shape — the detector reconciles a fortnight, the event is
   planted four days before the seed date, so after ten days of drift it was
   simply behind the detector — and now walks back through consecutive
   fortnights.
2. **A simulated day could sell the planted dead stock.** `/_simulate/day` drew
   from anything in stock, including the twelve items the fixture planted as
   never-sold, so a few runs quietly ate the scenario. It now only sells things
   the shop has actually sold before, which also happens to be more realistic.
3. **Reseeding the source leaves the canonical DB holding the old lineage.**
   `tasks.py seed --reset` regenerates RegisterOne from scratch; a backfill then
   *soft-deletes* what vanished rather than removing it, so rows from the
   superseded fixture stay in the table carrying stamps that can be ahead of
   everything the source now returns. Six tests counted those rows against what
   the source currently holds and read the difference as a sync fault: the
   watermark check, the incremental row counts, the month-by-month comparison
   and two of the quirks. They now look at live rows only, which is what every
   one of them claimed to measure and what most of the queries around them
   already did. If you want a genuinely clean slate rather than a correct one,
   the full rebuild is `db-reset` → `migrate` → `seed` → `backfill` (both
   tenants) → `documents`.

The suite is now green three runs in a row on a fixture reseeded *without* a
canonical rebuild, which is the case that used to produce five failures.

The fixture still has a finite life, and it is the **reorder** scenario that
dates it rather than the shrink one. The other four planted situations are
events that happened on a day and stay in the history; the eight items "about
to run out" are a state of the shelf, and the simulated days keep selling them.
Somewhere around a fortnight of drift they have genuinely run out and no test
can find them again. That one now fails with a message naming drift and the
reseed command, rather than with a bare assertion about the forecast.

### Other fixes

* **Stored capabilities now refresh on every sync.** `SyncEngine.run` asks the
  adapter what it can do and writes it to `integrations.capabilities` before it
  opens the run, logging what was gained or lost. `tsundoku` reports
  `has_vendors` and `has_payments` again, so the month-end packet splits
  takings by tender and the conformance check passes.
* **Agent tools had no test coverage.** `reorder_suggestions` and
  `busiest_hours` were registered but missing from the tool tests' `CALLS`
  table, which surfaced as a `KeyError` inside an assertion about response
  size. Both are covered now, and
  `test_every_tool_has_arguments_to_call_it_with` fails loudly if the next tool
  is added without an entry.
* **Dev tenants get a digest recipient.** `ensure_dev_tenant` now writes one
  (`owner@tsundoku.example`, `owner@panelandpawn.example` — `.example` is
  reserved by RFC 2606 and cannot be delivered to), so a fresh checkout's
  digest job does something instead of skipping with "nobody at this shop has
  asked for the digest". It only runs when the tenant is first created; for an
  existing database, call `ensure_dev_tenant` once.
* **The digest preview leaked `{{unsubscribe_url}}`.** The HTML preview route
  substituted it and the JSON one did not, so the owner-facing preview showed a
  template variable. It now reads "(a link unique to each recipient)", which is
  what it is.
* **The month-end packet reported every tipping shop as not balancing.** This
  one is worth reading. A payment's `amount` is the sale *without* its tip —
  the tip rides alongside it on the same row — so takings worked out as
  `payments - refunds` came up short by exactly the month's tips, every month,
  against a `net sales + tax + tips` expectation that included them. Tsundoku's
  August was $9.00 out and December $33.00, both past the $5 tolerance, so the
  packet printed "a gap of $9.00. Left in rather than adjusted out" in the data
  notes. The arithmetic was doing what it was told; what it was told was wrong.
  Takings now add the tips back, both packets reconcile to the cent, and
  `test_the_month_end_packet_reconciles` fails if the tips are dropped again.
  It is the one bug found here that would have reached a customer, because the
  two reconciliation lines are exactly what a bookkeeper decides whether to
  trust the packet on.

* **Every file download in the app returned 422.** The month-end PDF and
  workbook, and the purchase order PDF and CSV — four of them, all broken, and
  the two features' actual deliverables. Each download route spells its format
  as a dotted suffix on the id (`/month-end/{packet_id}.pdf`), a path parameter
  will happily swallow that suffix, and FastAPI matches in declaration order,
  so the plain `/month-end/{packet_id}` declared above them took every download
  request and failed to parse `"<uuid>.pdf"` as a UUID. The handlers were never
  reached and their own unit tests all still passed, because the PDF writer was
  never the problem. The bare-id routes are now declared last, with a comment
  saying why, and `tests/test_downloads.py` fetches all four over HTTP and
  checks the magic bytes.

### The two missing screens are built

* **The worker, on Data & sync.** Two cards: the schedules with when each last
  succeeded, and the job history. Overdue is explained rather than just shown
  in red, because the worker deliberately lets a stale digest go rather than
  sending Monday's email on a Thursday.
* **Notifications, on Reports.** Who hears from us (with an add/update form)
  and what we actually sent, including held-back messages and why. They sit
  next to the digest preview because that is what they govern. Verified end to
  end: "Send me a test" delivered through the console transport and appeared in
  the log.

## Still open

1. **Email and SMS have never left the machine.** `NOTIFY_TRANSPORT` defaults
   to `console`; set it to `live` with a Resend key to actually send. Nothing
   has exercised the real transport, and the dev tenants' `.example` addresses
   are undeliverable by design.
2. **`mappings/panel_and_pawn.yaml` has no `vendors` or `variant_vendors`
   section.** The mapping adapter supports both; the file does not use them, so
   Panel & Pawn exercises the no-vendor path. That is useful coverage, not an
   oversight, but it is worth a deliberate decision.
3. **`python tasks.py worker` prints a `runpy` RuntimeWarning on startup**
   (`app.jobs.worker` is imported by `app/jobs/__init__.py` before being run as
   a module). Harmless, and one line of tidying in the package's exports.

## Things worth knowing before changing anything

* **Detector thresholds were tuned against this one shop.** A 45%-down Saturday
  is *inside* Tsundoku's normal Saturday range — the ordinary ones run $230 to
  $620 — which is why the planted scenario had to be made much deeper and why
  the baseline keeps a spread per side. Any threshold change should be checked
  against `test_an_ordinary_stretch_is_quiet`, which fails if the detector gets
  noisy.
* **The model never touches a number.** `app/llm.py` holds the validator; the
  digest and the dead-stock phrasing both fall back to a plain template if the
  model writes a figure that was not in the payload. Keep it that way.
* **Nothing in this pack can send an order or message a customer.** The reorder
  assistant's furthest reach is a `mailto:` the owner presses send on.
* **`app/deadstock.plan()` takes `phrase=False` by default.** Turning it on
  costs a model round trip, which is fine in the weekly detector and is twenty
  seconds of blank screen on a page load.
* **A test that goes through the `client` fixture commits.** The `db` fixture
  rolls everything back; a request through `client` runs in the app's own
  session and its writes outlive the test, against the same local database
  every other test reads. That is not always harmless — a purchase order draft
  left behind makes the reorder assistant stop suggesting the variants on it,
  which is correct behaviour and which silently empties a planted scenario a
  later test is looking for. `tests/test_downloads.py` takes back what it
  creates, in a `finally`.
* **Uvicorn's reloader has been seen announcing a reload it never carried out.**
  If an API change appears not to have taken, restart the server before
  believing the code is wrong. This cost half an hour: the fix was right and
  the running process was not.
