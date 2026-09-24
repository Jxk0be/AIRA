# UI audit

What this is built from: `docs/ui/complaints.md`, the seven screenshots in
`docs/ui/before/`, the two references in `docs/ui/references/`, and a read of
`web/src/router.ts`, `App.vue`, all ten views and all seven components.

Findings are numbered so the per-page rebuild prompts can cite them.

---

## 1. The routes today

Nine top-level destinations plus a fallback, all at `/:tenant/<screen>`.

| Route | Called | What the owner is actually trying to do |
|---|---|---|
| `/:tenant/dashboard` | Dashboard | "How did this week go, and is anything wrong?" |
| `/:tenant/insights` | Worth doing | "What should I deal with before I open?" |
| `/:tenant/assistant` | Assistant | "I have a specific question nobody built a screen for." |
| `/:tenant/reorder` | Reorder | "What do I buy, and from whom?" |
| `/:tenant/dead-stock` | Dead stock | "What is my money stuck in, and what do I do about it?" |
| `/:tenant/inventory` | Inventory | "What have I got, and what is it worth?" |
| `/:tenant/staffing` | Staffing | "Am I staffed when people actually come in?" |
| `/:tenant/month-end` | Reports | "Give the bookkeeper what they asked for." |
| `/:tenant/data` | Data & sync | "Is this data current, and why is that number odd?" |
| `/` | Shop picker | Nothing — it only renders when no shop exists or the API is down. |

Two of these are not really destinations. **Data & sync** is settings. **Shop
picker** is a dev affordance — `App.vue:63` says so in a comment — yet the shop
`<select>` sits above the navigation on every screen, taking ~56px off the top
of every phone.

---

## 2. Usability problems

### Structure and navigation

**U1 — Nine tabs do not fit a phone, so destinations are invisible.**
`App.vue:100` renders all nine in a single `overflow-x-auto` row. In
`before/mobile/scrollable-nav.png` the row is mid-scroll: "Dashboard" and
"Worth doing" have gone off the left edge, "Staffing" is cut in half on the
right, and nothing indicates there is more in either direction. The owner cannot
see where they are or what else exists.
→ complaints: *"names easy to understand"*, *"flow naturally"*, *"mobile comes first"*.

**U2 — Navigation costs ~130px of every phone screen before content starts.**
Logo row, then shop `<select>`, then the tab row — all stacked at the top, all
scrolling away with the content. On an 812px phone that is 16% of the viewport
spent on chrome that a bottom tab bar would give back.

**U3 — Four screens are the same job split four ways.**
Inventory, Reorder, Dead stock, and the dashboard's "Running out" / "Sitting
still" panels are all "what is on my shelves." The dashboard shows a ten-row
preview of two of them with no link through to the full screen, so the owner has
to already know Reorder exists to find it.

**U4 — "Worth doing" is both a screen and a dashboard block, and they disagree.**
`WorthDoing.vue` shows the top 3 on the dashboard; `InsightsView.vue` shows the
filtered list. The dashboard block silently renders nothing when the API fails
(`WorthDoing.vue:40`, deliberately) — so "nothing to do today" and "the inbox is
broken" look identical.

**U5 — Pinned charts are stranded.**
The dashboard's last line is "Charts you pin from the assistant show up here" —
an empty state advertising a feature the owner has to discover on another screen.

### Reading the numbers

**U6 — Rows in a list do not line up.**
`before/desktop/findings-different-widths.png`: the severity badge
(`InsightsView.vue:220`) is `shrink-0` with variable-width text, so "SHRINK" and
"DEAD STOCK" push their titles to different x positions. Every row starts in a
different place.
→ complaint: *"columns within those items should line up symmetrically"*.

**U7 — The same problem in Reorder, plus a heading that reads as a row.**
`before/mobile/hard-to-tell-dividers.png`: the vendor group heading "Paper
Lantern Books" (`ReorderView.vue:283`) is `text-sm font-semibold` — one notch
above the item names beside it, which is not enough to read as a divider. The
"GONE FIRST" badge sits inline with the item name and wraps it onto three lines,
so the On hand / Cover / Order / At cost figures no longer align with the headers
two rows up.
→ complaints: *"clear dividers"*, *"columns line up"*, *"more bold/stand-out
weights when talking about important things"*.

**U8 — The inventory table hides the column that identifies the row.**
`InventoryView.vue:166` sets `min-w-[46rem]` (736px) inside an `overflow-x-auto`.
At 375px you get `before/mobile/scroll-to-paginate.png`: five columns of money
and dates and no item names at all, so the cells are unidentifiable.
→ complaint: *"clean layout... without having to scroll horizontally at all"*.

**U9 — Paging requires scrolling past 50 rows.**
Previous/Next sits below the table body (`InventoryView.vue:224`), so moving
through 333 items means a full-page scroll each time. Same screenshot.

**U10 — The dashboard scrolls horizontally at 375px.**
Visible scrollbar in `before/mobile/dashboard-top-products.png`, and panel
content is clipped at the right edge in both dashboard shots. Most likely the
ECharts container measuring before the grid resolves (`ChartRenderer.vue:76`
already fights this) plus the un-wrapped caveat row at `DashboardView.vue:152`.
To be confirmed by the no-horizontal-scroll assertion in step 4 rather than
guessed at.
→ complaint: *"no overflowing the width of the screen"*.

**U11 — Caveats out-shout the numbers they qualify.**
`before/mobile/dashboard-chart.png`: a four-line yellow block sits between the
KPIs and the chart. In `dashboard-top-products.png` another one is itself cut off
mid-sentence. The honesty is right and worth keeping; the volume is wrong. On a
phone the caveat is physically bigger than the thing it is about.

**U12 — Body text is 15px, and a lot of text is far smaller.**
`style.css:94`. Below that: `text-[0.6rem]` (9.6px) on the inventory sort arrows
and the Reorder "gone first" badge, `text-[0.62rem]` on the insight kind badge
and the desktop "Analyst" label, `text-[0.68rem]` on the KPI labels and the
assistant's cost line, `text-[0.7rem]` inside tool chips.
→ complaints: *"text super visible, nice spacing, not too small"*, *"smoother and
a bit thicker"*.

### Feedback and state

**U13 — Nothing confirms a write.**
There is no toast component in the app. Six views each hold a private `notice`
ref rendering an inline paragraph above the content, which shoves everything down
(`InsightsView.vue:194`, `ReorderView.vue:162`, `DeadStockView.vue:120`,
`StaffingView.vue:117`, `MonthEndView.vue:150`, `DataView.vue:166`). Nothing at
all confirms pinning a chart, unpinning one, rating an insight, changing a PO
quantity, or removing a PO line.
→ complaint: *"toast messages if we make a POST of any sort or update data"*.

**U14 — The assistant's history panel shoves the conversation.**
`before/mobile/history-moving-chat.png`: tapping History expands a block
(`AssistantView.vue:263`) between the header and the thread, pushing the
conversation down and off-screen. It should be a drawer over the top, not a
layout change. Seven conversations are visible and six are titled "December
sales" — auto-titles collide, and there is no date grouping.

**U15 — Skeletons exist in two places and nowhere else.**
`PanelCard` and `KpiRow` have them. Inventory dims the whole table to
`opacity-50` while loading (`InventoryView.vue:191`); Assistant shows a text
string; Reorder, Dead stock, Staffing, Reports and Data have nothing between
click and render.
→ complaint: *"skeleton pulsing UI"*.

**U16 — No error is recoverable.**
Every view renders the caught message in a red-bordered `<p>` and stops. There is
no Retry anywhere. The API's own messages are good by design (`client.ts:1-8` —
FastAPI's `detail` is written for the shop owner), but a network failure produces
`Could not reach the API (TypeError: Failed to fetch)` (`client.ts:65`), which is
not.

**U17 — Native browser dialogs.**
Renaming a conversation is `window.prompt` and deleting one is `window.confirm`
(`AssistantView.vue:213,221`). Unstyled, unthemed, and on mobile they look like
the browser has broken.

**U18 — No theme control at all, and the default follows the OS.**
`style.css:30` is a bare `prefers-color-scheme` block. There is no toggle, no
persistence, and no `[data-theme]` root. This is exactly why every
before-screenshot is dark: the OS is dark and the owner has no say.
→ complaint: *"light/dark mode as an option, default to light mode"*.
This contradicts the overhaul plan's step 3, which asks for System as the
default. The complaint wins.

### Language

**U19 — Internal vocabulary reaches the screen.**
"Data & sync" as a destination name. Sync failures render as
`{{ JSON.stringify(fault) }}` (`DataView.vue:290`). Integration config renders as
`key=value · key=value`. Capability names become "has online channel" and
"supports incremental". Job names become "month end packet"; message statuses
read "suppressed" and "queued". The insight filters' two `<select>`s have
`sr-only` labels, so visually they are two unexplained dropdowns.
→ complaint: *"names easy to understand, not confusing"*.

**U20 — One action, four different button styles.**
Primary actions are `bg-brand text-white` in Insights, Dead stock, Reorder and
Staffing, but `bg-brand text-paper` in Data and Assistant. Secondary actions are
variously `border border-rule`, bare text, or `text-brand`. The Reorder screen
alone shows four visual tiers of button inside one panel.
→ complaint: *"clear CTAs"*.

---

## 3. Accessibility problems

### Measured contrast against the current palette

Computed from the hex values in `style.css`, WCAG 2.x relative luminance.

| Pair | Light | Dark | |
|---|---|---|---|
| `ink` on `panel` | 18.25 | 14.90 | pass |
| `ink-muted` on `panel` | 5.58 | 6.50 | pass |
| **`ink-faint` on `panel`** | **3.08** | **3.72** | **fails AA for text** |
| **`ink-faint` on `paper`** | **2.88** | 4.00 | **fails** |
| **`ink-faint` on `sunk`** | **2.67** | 3.52 | **fails** |
| **`rule` on `panel`** | **1.35** | **1.25** | **fails 3:1 for UI borders** |
| **`rule-strong` on `panel`** | **1.73** | **1.67** | **fails 3:1** |
| **`text-white` on `bg-brand`** | 9.64 | **2.05** | **fails badly in dark** |
| `text-paper` on `bg-brand` | 9.02 | 9.10 | pass |

**A1 — The whole `ink-faint` tier fails AA, and it is used 108 times.**
It carries timestamps, SKUs, column headers, KPI comparisons, caveat counts and
most of the Data screen — almost always at `text-xs` or smaller, which makes it
worse rather than better. Worst case 2.67:1 against `sunk`.

**A2 — Four primary buttons are unreadable in dark mode.**
`bg-brand text-white` at 2.05:1 — `DeadStockView.vue:156`, `InsightsView.vue:250`,
`ReorderView.vue:199`, `StaffingView.vue:248`. The two views using `text-paper`
instead are fine, so this is an inconsistency that happens to be a failure.
Fixing it properly needs a real `--color-primary-fg` token, not a find-replace.

**A3 — Borders do not meet 3:1.** `rule` at 1.35:1 is the border on every input,
select and card. Input borders are UI components under WCAG 2.2 and need 3:1.

**A4 — Severity is colour-only.** `SEVERITY_STYLE` (`InsightsView.vue:47`) encodes
urgent / warn / info as border and text colour. The badge's *text* says the kind
("Reordering", "Shrink"), never the severity. Remove the colour and the ranking
disappears. The KPI deltas are fine by contrast — the sign is in the text.

**A5 — The staffing heatmap is opacity-only.** `StaffingView.vue:72` sets
`opacity` from the rate; the value lives in a `title` on a `<div>`, which is not
keyboard-reachable and is poorly announced. The "somebody is rostered on" hairline
is a 1px line with no text equivalent. The `<table>` has no caption.

**A6 — Unlabelled controls.** The PO quantity `<input type="number">`
(`ReorderView.vue:231`) has no label or `aria-label`, so a screen reader reads
"spin button" with no indication of which line it belongs to. The inventory sort
buttons carry no `aria-sort` on the `<th>`. No table uses `scope="col"`.

**A7 — No landmarks, no skip link, no focus management.** `<nav>` has no
`aria-label`, `<main>` has no `id`, there is no skip-to-content link, and a route
change moves nothing but the scroll position (`App.vue:37`) — focus stays where it
was and the new page title is never announced.

**A8 — Streaming is silent to a screen reader.** Tokens append into a `v-html`
block (`AssistantView.vue:364`) with no `aria-live`. Tool chips appear with no
announcement. A blind user gets no signal that an answer is arriving, has
finished, or has failed.

**A9 — Charts have no text alternative.** `ChartRenderer.vue` emits an SVG with no
`aria-label`, no `role="img"` and no table fallback. A `table` *spec type* already
exists and is rendered by the same component — the mechanism for "view as table"
is already built and unused.

**A10 — Loading states are not announced.** `PanelCard`'s skeleton is
`aria-hidden` inside a section with no `aria-busy`; `InventoryView`'s
`opacity-50` conveys nothing non-visually at all.

**A11 — Touch targets.** The inventory sort buttons, the insight
Dismiss / Snooze / Yes / No row, the tool-chip disclosure and the conversation
Rename / Delete links are all well under 44×44px.

**A12 — The focus ring is `--brand`,** which is also the primary-button
background, so a focused primary button shows a ring in its own colour. It needs
its own token.

---

## 4. What the complaints ask for that does not exist yet

These are features, not restyling. The overhaul plan omits all five.

**F1 — Business colour per shop.** *"pick a 'business color' in the Data & sync
page so I can choose what the main color is."* `--brand` is a hardcoded hex in
`style.css`. This needs a per-tenant stored value, a picker, and — given A2 —
automatic foreground selection plus a refusal to accept a colour that cannot
clear 4.5:1 in both themes.

**F2 — Chart zoom and per-day granularity.** *"drag and zoom in or change the
graph to be per day instead of per week."* The dashboard hardcodes `Grain.WEEK`
(`api/app/dashboard/routes.py:376`). `Grain.DAY` and `Grain.MONTH` already exist
(`api/app/analytics/results.py:72`) and `sales_series` already takes a grain, so
this needs one additive optional `grain` query param — the only backend change in
the whole overhaul, and backwards-compatible. ECharts `dataZoom` is not currently
imported.

**F3 — Toasts on every write.** See U13.

**F4 — Skeletons everywhere.** See U15.

**F5 — A light-first theme toggle.** See U18.

---

## 5. Corrections to the overhaul plan that this audit assumes

- There is no `make` on this machine. `ui-check` and `ui-shots` become tasks in
  `tasks.py`, hanging off `python tasks.py test`.
- `web/` has no test runner of any kind. Step 4 is greenfield, not a bolt-on.
- The contrast script belongs in `web/scripts/`, not the Python-only `scripts/`.
- UI tests should mock the API with recorded fixtures rather than require FastAPI
  and Supabase to be running.
- Tokens are not being created from nothing: `style.css` already has a considered
  semantic set and a deliberately designed dark palette. Step 3 extends it
  (success / warning / info, `primary-fg`, `focus-ring`, `chart-1..6`, elevation,
  motion) rather than renaming it.
- The default theme is **light**, not System.
