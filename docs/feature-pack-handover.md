# Feature pack — where this got to

Written at the end of the first build session so the next one can start
without re-reading the diff. Delete it once the list at the bottom is empty.

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

## Known gaps, worst first

1. **A tenant's stored capabilities do not pick up new adapter capabilities.**
   `integrations.capabilities` is written when the tenant is created, so
   `tsundoku` still says `has_vendors: false, has_payments: false` even though
   the adapter now declares both and the data synced fine. Effect: the
   month-end packet cannot split takings by tender, and the conformance check
   `test_the_adapter_describes_what_the_integration_stored` fails.
   Fix: have the sync engine refresh `integrations.capabilities` from
   `adapter.describe()` at the start of every run. One place, a few lines.

2. **Seven other tests fail**, all of them tests that existed before this work.
   Whether they passed beforehand is unverified — the suite was never run at
   the start of the session, which was a mistake. They all touch the fixture's
   dates, and the fixture was last seeded with
   `--end-date 2026-09-23` rather than the default of *today*:
   `test_every_month_matches_the_source`, both `test_registerone_quirks` date
   assertions, both `test_sync_incremental` tests, and the two
   `test_agent_tools` ones. Re-run `python tasks.py seed` with no `--end-date`
   and `python tasks.py backfill`, then re-check — most or all should clear.
   Anything left is a real regression and should be read as one.

3. **Nothing ran `python tasks.py lint` or `typecheck` on the Python side.**
   `vue-tsc` passes. Ruff and mypy have not been run over ~6,000 new lines.

4. **The worker has never been left running.** `python tasks.py round` does one
   pass and is the safer thing to try first; `python tasks.py worker` is the
   loop. Neither has been exercised for more than a single tick.

5. **No notification recipient exists for any tenant**, so the digest job
   currently skips with "nobody at this shop has asked for the digest". Add one
   through `PUT /tenants/{slug}/notifications` or the notifications screen —
   there is an API for it but no screen yet (see below).

## Not built, and known not to be

* A notifications screen. The API is there (`app/notify/routes.py`): list and
  upsert recipients, read the outbound message log. No Vue page.
* A jobs screen. Same — `app/jobs/routes.py` is there and the Data & sync page
  is where it belongs, but it is not wired in.
* The `mappings/panel_and_pawn.yaml` spreadsheet has no `vendors` or
  `variant_vendors` section. The mapping adapter supports both; the file just
  does not use them, so Panel & Pawn exercises the no-vendor path. That is
  useful coverage, not an oversight, but it is worth a deliberate decision.
* Email and SMS have never left the machine. `NOTIFY_TRANSPORT` defaults to
  `console`; set it to `live` with a Resend key to actually send.

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
