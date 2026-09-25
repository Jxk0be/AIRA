# Automated UI checks

    python tasks.py ui-check      # contrast + raw-color lint + Playwright + axe
    python tasks.py ui-shots      # full-page screenshots into docs/ui/after/
    python tasks.py ui-fixtures   # re-record the API responses the tests replay

`ui-check` also runs as part of `python tasks.py test`.

## What runs

| | |
|---|---|
| **Contrast gate** | `web/scripts/check-contrast.ts` — 186 token pairs, both themes. Parses `style.css`, so it tests what ships, and imports the app's own `lib/color.ts` so the gate and the picker cannot measure differently. |
| **Raw-color lint** | In `tasks.py`. Fails on hex, `rgb()`/`hsl()`, or a Tailwind palette color inside a component. |
| **Business color** | `brand.spec.ts` — picks a color in Settings, then measures the button the browser actually painted. The luminance maths is written out again in the spec: an app that checked its own contrast with the function that chose the color would agree with itself whatever it did. |
| **axe** | WCAG 2.2 AA tags only, every screen, every theme, every size. Nothing excluded. |
| **Keyboard** | Focus always visible, never trapped, skip link present, one labeled `<main>` and `<nav>`. |
| **Layout** | No horizontal scroll, no text under 13px, tap targets ≥44px on mobile. |

Ten screens × four projects (375×812 and 1280×800, each light and dark) = 40 runs
per check.

`lib/brand.ts` carries a hardcoded copy of the four surfaces, for the case where
it cannot read the loaded stylesheet. That copy decides whether an owner's color
is readable, so the contrast gate checks it against `style.css` on every run and
fails if the two have drifted.

## No database required

The API is replayed from `web/tests/fixtures/` — 21 real responses recorded from
a live tenant. Running these for real would mean standing up Supabase, FastAPI
and a seeded tenant before a single pixel could be checked, so the checks would
be skipped locally and flake in CI, and a red run would more often mean "the
database was not ready" than "the page is wrong".

A request with no fixture is failed loudly rather than passed through, so the
suite cannot quietly start depending on a live API again.

Re-record with `python tasks.py ui-fixtures` (needs `python tasks.py api`)
whenever a response model changes. The diff is the review.

## Baseline

### After the business color — 442 passed, 0 failed, 0 flaky

Sixteen more than the step-7 baseline: four `brand.spec.ts` cases across four
projects. They are the first in the suite to open a control and use it rather
than scan a page as it loads.

### After step 6, all screens — 426 passed, 0 failed, 0 flaky

Every screen is rebuilt and the suite is green: axe on twelve routes in both
themes at both sizes, keyboard and focus, the layout rules, the redirects, and
the contrast gate.

That is a floor, not a finish line. These checks catch violations, not whether
the thing is good to use. `docs/ui/still-weak.md` lists the ten things they do
not cover, worst first — streaming has no test at all, overlays are only ever
tested closed, and only one of the two dev shops is ever rendered.

### After step 6, Home + Ask + Stock — 354 passed, 12 failed, 0 flaky

Home, Ask, all three Stock tabs, Reports › Sales, Settings › Appearance and the
styleguide are green. Three screens are left: Reports › Month end, Reports ›
Busy hours and Settings › Data.

| Failures | Check | Where |
|---|---|---|
| 6/40 | tap targets ≥44px | Month end, Busy hours, Data — mobile |
| 6 | axe violations | `scrollable-region-focusable` on the month-end `<pre>` and the staffing heatmap |

### A regression this baseline caught

The document scrolled instead of `main`, so the desktop sidebar rode up with the
content and left dead space below it. The layout was exactly one viewport tall
with `main` clipping its own overflow, which *happened* to leave the document
unscrollable — but nothing said it had to be, so anything a pixel taller (a
browser resolving `dvh` differently, a chart tooltip placed outside its
container) scrolled the page instead.

`AppShell`'s root now owns the height and clips, and the rail is its own scroll
container. `layout.spec.ts` asserts it on every screen: the window must not
scroll and the sidebar must not move when content does.

### After step 6, Home and Ask — 293 passed, 33 failed, 0 flaky

Home, Ask, Reports › Sales, Settings › Appearance and the styleguide are fully
green. Everything still failing is a screen step 6 has not reached.

| Failures | Check | Where |
|---|---|---|
| 12/40 | tap targets ≥44px | Stock, Reports, Settings — mobile only |
| 8/40 | text ≥13px | Inventory's sort arrows, Reorder's "gone first" badge |
| 13 | axe violations | `scrollable-region-focusable` on the reorder tables, staffing heatmap and month-end `<pre>`; `target-size` on the inventory search; `color-contrast` on the remaining `text-white` buttons |

### After step 6, Home only — 291 passed, 35 failed, 0 flaky

Home, Reports › Sales, Settings › Appearance and the styleguide are fully green.
Every remaining failure is in a screen step 6 has not reached yet: Ask, the three
Stock tabs, Reports › Month end and Busy hours, and Settings › Data.

| Failures | Check | Where |
|---|---|---|
| 14/40 | tap targets ≥44px | Ask, Stock, Reports, Settings — mobile only |
| 8/40 | text ≥13px | Inventory's sort arrows, Reorder's "gone first" badge |
| 13 | axe violations | `scrollable-region-focusable` on the reorder tables, staffing heatmap and month-end `<pre>`; `target-size` on the inventory search; `color-contrast` on the remaining `text-white` buttons |

### After step 5 (the app shell)### After step 5 (the app shell) — 225 passed, 53 failed

The shell turned 118 checks green. What is left is page *content*, which step 6
rebuilds one screen at a time.

| Failures | Check | Why |
|---|---|---|
| 22/40 | tap targets ≥44px | Mobile. Buttons and links inside the views. |
| 16/40 | text ≥13px | The `text-[0.6rem]`–`text-[0.7rem]` spans from audit U12. |
| 15 | axe violations | `scrollable-region-focusable` on the reorder tables, the staffing heatmap and the month-end `<pre>`; `target-size` on the inventory search; `color-contrast` on the four `text-white` buttons (audit A2). |

Now passing that were not: the skip link, the `<main>` landmark and labeled
`<nav>` (40 each), the Data & sync horizontal overflow, and the dashboard's
stock tables.

### Before step 5 — 107 passed, 153 failed

| Failures | Check |
|---|---|
| 40/40 | skip link — did not exist |
| 40/40 | `<main id>` + labeled `<nav>` — did not exist |
| 36/40 | text ≥13px |
| 20/20 | tap targets ≥44px |
| 15 | axe violations |
| 2/20 | horizontal scroll — Data & sync |

### Fixed along the way

**`color-contrast`, insights and dead-stock, dark only.** `text-white` on
`bg-brand` at **2.05:1** — audit A2, found by hand in step 1 and confirmed here
independently. Still open until step 6 moves those four buttons to `UiButton`,
which takes its foreground from `--primary-fg`.

**Horizontal scroll on Data & sync.** Three `PanelCard` sections rendered 589px
wide inside a 343px grid cell: a grid child defaults to `min-width: auto`, so
the unbreakable `key=value` config string set the track width. Fixed with
`min-w-0` on the card.

That fix then *caused* a new violation — narrower cards meant the stock tables
inside them began to scroll, and a scrollable region with nothing focusable
cannot be scrolled without a mouse. `StockTable` and `ChartRenderer`'s table
view are now `tabindex="0"` with a name.

**An unlabelled `<nav>`.** Inventory's pagination.

## Three things these checks got wrong at first

Recorded because all three are easy to reintroduce.

**Route glob.** `page.route('**/api/**')` also matches the dev server's own
`/src/api/client.ts`, so the app's own module was answered with JSON and the
page never booted — which looked like ten broken screens. It matches on
`pathname.startsWith('/api/')` now.

**Two false-positive focus traps.** Comparing tab stops by *label* called four
buttons reading "Make a draft" — one per supplier — a trap. Comparing by element
identity fixed that, and then `<input type="time">` tripped it again, because a
segmented field legitimately takes three Tab presses. Focus-ring visibility is
now judged per element across all its stops rather than per stop.

**A diagnostic that blamed the innocent.** The overflow message listed whichever
elements sat furthest right, which meant the nav links — legitimately clipped
inside their own scroller. Filtering those out then reported nothing at all,
because `<main>` carries `overflow-y-auto` and CSS computes its `overflow-x` to
`auto` too, so every element on the page looked clipped. It now ignores only
*deliberate* scrollers, meaning ones narrower than the viewport, which is what
finally named the three oversized cards.

A check that cries wolf is worse than no check, so all three were fixed rather
than suppressed.

## Screenshots

`ui-shots` writes 40 full-page PNGs into `docs/ui/after/<size>/<theme>/`.

Two things had to be worked around, both worth knowing before trusting them:

- **`fullPage` captures nothing extra here.** `App.vue` makes `<main>` the scroll
  container at `100dvh` so the assistant's composer can stay above the mobile
  keyboard, so the *document* is never taller than one screen. The shots spec
  releases those height cages and grows each vertical scroller to its content
  first.
- **It must not release `overflow` wholesale.** Doing that unclips the horizontal
  axis too, and the inventory table's 46rem minimum stretched a 375px phone shot
  to 845px — a screenshot of a viewport nobody has. Only heights are touched.

The assistant is genuinely one viewport tall by design, so its shot is 375×812
rather than a long page. That is correct, not a truncation.
