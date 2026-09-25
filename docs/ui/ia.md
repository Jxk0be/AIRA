# Information architecture

Who this is for: a shop owner, usually on a phone, often standing behind the
counter with one hand free, in the ten minutes before opening. Mobile is the
design target and desktop is the enhancement — not the other way round.

Nine top-level destinations become five.

---

## The five destinations

### 1. Home — `/:tenant`

*"What do I need to deal with, and how did trade go?"*

Actions first, numbers second. This is the only screen most owners open on most
days.

- **Worth doing** — the full findings inbox, not a three-row preview. Filters
  (Open / Acted on / Dismissed, and by kind) live here.
- **This week at a glance** — the KPI row with its period switch.
- **Net sales** — with the day / week / month control and drag-to-zoom (F2).
- **Pinned** — charts the owner pinned from Ask.

Primary action: the top finding's own button ("Count these items", "Make a
draft"). Merges today's Dashboard and Worth doing, which fixes U4 — one inbox,
one truth, and a visible error state when it cannot load.

The breakdown panels (Top products, Category mix, By location, By channel) move
to Reports; the stock previews (Running out, Sitting still) move to Stock. Home
stops being a directory of other screens.

### 2. Ask — `/:tenant/ask`

*"I have a question nobody built a screen for."*

The assistant, unchanged in behavior. Conversation history becomes a drawer over
the content instead of a block that shoves it (U14), grouped by date, with
in-place rename and a proper confirm dialog instead of `window.prompt` /
`window.confirm` (U17).

Primary action: Ask.

### 3. Stock — `/:tenant/stock`

*"What is on my shelves, what do I buy, what is stuck?"*

Three tabs, one subject. Fixes U3.

| Tab | Is today | Primary action |
|---|---|---|
| All items | Inventory | Search |
| Reorder | Reorder | Make a draft |
| Not selling | Dead stock | I did this |

Tab state lives in the URL (`/stock?tab=reorder`). See "Old links must keep
working" below — this move breaks stored deep links unless redirects are added.

### 4. Reports — `/:tenant/reports`

*"Show me the numbers, and give the bookkeeper theirs."*

Things you look at monthly, not daily.

| Tab | Is today |
|---|---|
| Month end | The packet list and Build last month |
| Sales | Top products, Category mix, By location, By channel — moved off Home |
| Busy hours | Staffing: the heatmap, the observations, the rota |

Primary action: Build last month.

### 5. Settings — `/:tenant/settings`

*"Is my data current, who gets emailed, what color is my shop?"*

| Tab | Is today |
|---|---|
| Data & sync | Connected system, last sync, data quality, sync history, the worker, job history |
| Documents | Upload and list |
| Notifications | Recipients, Monday's email preview, what we actually sent |
| Appearance | Light / Dark / System toggle, and the business color (F1) |
| Shop | The dev-only shop switcher, moved out of the global nav |

Primary action: Sync now.

An **Account** tab is deliberately absent — there is nothing to put in it yet.
See "Authentication and tenancy" below; when sign-in lands, it slots in here and
in the desktop account menu without disturbing the other five destinations.

---

## Where everything moves

| Today | Goes to |
|---|---|
| Dashboard — KPI row, Net sales | Home |
| Dashboard — Top products, Category mix, By location, By channel | Reports › Sales |
| Dashboard — Running out | Stock › Reorder (as the "act now" band) |
| Dashboard — Sitting still | Stock › Not selling |
| Dashboard — Pinned | Home › Pinned |
| Worth doing | Home (the whole list, not a preview) |
| Assistant | Ask |
| Reorder | Stock › Reorder |
| Dead stock | Stock › Not selling |
| Inventory | Stock › All items |
| Staffing | Reports › Busy hours |
| Reports (month-end packets) | Reports › Month end |
| Reports — recipients, sent log, digest preview | Settings › Notifications |
| Data & sync | Settings › Data & sync |
| Data & sync — Documents | Settings › Documents |
| Shop `<select>` in the global nav | Settings › Shop, and the desktop account menu |
| Shop picker at `/` | Unchanged — it is the no-shops / API-down screen |

Nothing is deleted. Every screen that exists today has a home.

---

## Navigation

### Mobile (< 768px)

- **Bottom tab bar**, fixed, five items: Home · Ask · Stock · Reports ·
  Settings. Icon plus text label. Active state is a filled icon, heavier label
  and a top rule — never color alone (A4). `padding-bottom:
  env(safe-area-inset-bottom)` for the iPhone home indicator.
- **Top bar**, compact: page title, and at most one contextual action.
- **Sub-navigation** (Stock's three tabs, Reports' three, Settings' five) is a
  segmented control under the top bar. Stock and Reports fit at 375px without
  scrolling; Settings scrolls horizontally with an edge fade so it is visibly
  scrollable — unlike today's nav (U1).
- The shop switcher leaves the top of the screen entirely (U2), giving back
  ~56px on every page.
- Unread findings show as a badge on Home, labeled "3 new actions".

### Desktop (≥ 1024px)

- **Collapsible left sidebar** — the same five destinations, shop name at the
  top, closer to `references/easy-navigation.png` than to today's nine-item rail.
- **Top bar** — page title, global search (Cmd/Ctrl+K to jump to pages, products
  and findings), theme toggle, account menu containing the shop switcher.
- Sub-navigation is tabs under the page title, matching mobile's grouping so the
  two layouts teach the same structure.
- Everything fits without horizontal scrolling at 1280px, including the
  inventory table (U8).

### Both

- `<header>`, `<nav aria-label="Main">`, `<main id="main">`, and a skip-to-content
  link (A7).
- A route change moves focus to the page heading and announces the new title.
- Scroll position is restored on back. Note that `main` is the scroll container,
  not the window (`App.vue:37`) — this is deliberate, it is what keeps the Ask
  composer pinned above the mobile keyboard, and it means scroll restoration has
  to be handled explicitly rather than inherited from the browser.

---

## Naming

| Today | Proposed | Why |
|---|---|---|
| Dashboard | **Home** | Matches the tab-bar convention; "Dashboard" describes a layout, not a destination. |
| Assistant | **Ask** | A verb. Says what you do there. |
| Inventory / Reorder / Dead stock | **Stock** | One place for shelves. Short enough for a 375px tab bar. |
| Dead stock | **Not selling** (tab) | Plainer than the trade term. Settled. |
| Month end | **Reports** | Already the page title; now also the destination. |
| Data & sync | **Settings** | Nobody looks under "Data & sync" for notification preferences. |
| Worth doing | **Worth doing** | Unchanged — already plain, and the phrase works. |

Jargon to remove while rebuilding (U19): raw `JSON.stringify` sync errors,
`key=value` integration config, `has_online_channel` / `supports_incremental`,
`month_end_packet`, message statuses "suppressed" and "queued", and the two
unlabelled filter dropdowns on the findings list.

---

## Deliberately not top-level

- **Shop switcher** — dev-only (`App.vue:63`). A real deployment is one shop per
  login, so it must not shape the navigation.
- **Pinned charts** — a section of Home, not a destination. Rename the action
  from "Pin to dashboard" to "Pin to Home" (U5).
- **Worth doing** — not its own destination any more. If it is not on the first
  screen, it is not actions-first.

---

## Built

All five destinations are in, with every screen rebuilt and every old path
redirecting. Nothing is transitional any more:

- **Home** holds the full findings inbox, the KPI line, net sales with
  day/week/month and drag-to-zoom, and pinned charts. `/worth-doing` — which
  existed only between steps 5 and 6 — redirects here.
- **Reports** has all three tabs. Sales holds the breakdowns that used to open
  the day on the dashboard.
- **Settings** has four: Data & sync, Documents, Notifications, Appearance.
  Documents came out of the Data screen and Notifications out of Month end,
  which is where nobody looked for a policy file or an email address.

One deviation from the table above: **Shop is not its own tab.** The dev shop
switcher is a section inside Appearance, because a five-tab settings screen for
a control that disappears the moment real authentication lands is a worse trade
than one extra section.

## Old links must keep working

Every finding carries a `suggested_action.route` that the backend wrote when the
finding was detected, and `InsightsView.act()` pushes straight to it. The
detectors emit seven distinct paths:

| Emitted by | Route string | New home |
|---|---|---|
| `anomalies/detectors.py:231` | `dashboard?day=<iso>` | `/` with Net sales focused on that day |
| `anomalies/detectors.py:503` | `dashboard` | `/` |
| `anomalies/detectors.py:385`, `:685` | `inventory` | `/stock?tab=all` |
| `anomalies/detectors.py:619` | `data` | `/settings?tab=data` |
| `deadstock/detector.py:123` | `dead-stock` | `/stock?tab=not-selling` |
| `reorder/detector.py:167` | `reorder` | `/stock?tab=reorder` |
| `staffing/detector.py:87` | `staffing` | `/reports?tab=busy-hours` |

These strings are **already persisted** in `insights.suggested_action` (JSONB),
and `digest/build.py:230` uses the same values to build links in the weekly
email — so they also exist in inboxes we cannot edit.

So: do not rewrite the detectors. Keep every old path as a redirect route in
`web/src/router.ts`, preserving the query string. That covers stored findings,
sent emails and any bookmark, and it keeps the AI layer untouched — which
CLAUDE.md rule 1 requires anyway. `dashboard?day=` is the one that needs real
work: it should land on Home with the net sales chart switched to day grain and
zoomed to that date, which is only possible once F2 lands.

## Authentication and tenancy

**There is no authentication today, and the UI overhaul does not add any.**

A tenant is a slug in the URL, resolved server-side by `shop_dependency`
(`api/app/http.py:34`). No id ever arrives from the client and nothing from the
client reaches SQL — that is CLAUDE.md rule 3 held in one function on purpose.

The intended destination is **Supabase Auth with row-level security**, at
deployment time, and the groundwork is already laid:

- `api/app/http.py:7` — *"There is still no authentication. When it lands, this
  is the function that learns about it, and every route that already depends on
  `Shop` gets it."* One dependency to change, not forty routes.
- `api/app/dashboard/routes.py:16` — *"Real auth, with row-level security behind
  it, comes with deployment."*
- `api/migrations/env.py:31` already excludes `auth`, `storage`, `realtime`,
  `vault` and the rest from autogenerate, so Supabase Auth can own `auth.users`
  while Alembic owns `public` (CLAUDE.md rule 6).
- `GET /tenants` (`routes.py:258`) is the dev switcher, and says what it becomes:
  *"the shops you may see, which is the same query with a join on it"* — a
  memberships table from `auth.users.id` to `tenants.id`.

What this IA owes that future, and nothing more:

- The shop switcher stays **dev-only** and out of the global navigation, so
  removing it changes no layout.
- The desktop **account menu** exists from day one holding the theme toggle and
  the switcher. Sign-out and profile drop into it later.
- Settings has room for an **Account** tab without renumbering anything.

No screen should be designed around a signed-in user until that lands.

## The two API changes

Both additive, both backwards-compatible, neither touching a response shape that
already exists. **Both approved.**

**1. Chart granularity (F2).** The dashboard hardcodes `Grain.WEEK` at
`api/app/dashboard/routes.py:376`, while `Grain.DAY` and `Grain.MONTH` already
exist and `sales_series` already accepts a grain. Add one optional query param,
`grain=day|week|month`, defaulting to `week`.

**2. Business color (F1).** Stored in the existing `Tenant.settings` JSONB
column (`api/app/canonical/tables.py:81`) — **no migration needed**. This is
already the pattern for per-shop config: `low_stock_threshold` and
`dead_stock_days` both read through `ctx.setting(key, default)`
(`api/app/analytics/context.py:169`). Needs:

- `brand_color` added to the `ShopProfile` response. Note `ShopProfile` is
  `ConfigDict(extra="forbid")`, so the field has to be declared, not smuggled in.
- One write endpoint for tenant settings. There is none today — the nearest
  writes are `POST /sync`, `POST /charts` and `PUT /notifications`.

Per-shop and per-account, so it follows the owner to their phone, which is what
the complaint asks for: *"the main color for my current business I am looking
at."*

Because a shop owner can pick any color, step 3's contrast gate has to run at
**runtime** for this token, not just in CI: derive `--color-primary-fg` from the
chosen color, and refuse a color that cannot clear 4.5:1 against both themes'
surfaces rather than shipping the dark-mode failure described in audit A2.

### Built, with one correction to the plan above

The paragraph before this one asks for a color that clears 4.5:1 against
*both* themes' surfaces. **No color can.** Light needs 4.5:1 against `#fbfbf9`
and dark needs it against `#15161a`; anything dark enough for one is invisible
on the other. The palette we ship already knew this — `--primary` is `#3d46b8`
in light and `#949bf5` in dark, one blue at two lightnesses — but the plan
above did not.

So the rule became: **one hue, two lightnesses, both shown.** `lib/brand.ts`
keeps the hue and chroma the owner picked and moves lightness in OKLab, per
theme, only as far as it takes to clear AA on `bg`, `surface` and `raised`.
Settings draws both derived shades in their own theme's surfaces with the
measured ratio under each, because a correction the owner cannot see is a
correction they will eventually report as a bug.

What is still refused, with the reason on screen: anything that is not a hex
value, and anything with no hue to preserve (chroma under 0.04 in OKLab) —
a grey lightened to be readable is just a different grey, and the interface
reads it as switched off.

| | |
|---|---|
| Storage | `Tenant.settings["brand_color"]`, no migration |
| Read | `brand_color` on `ShopProfile` |
| Write | `PUT /tenants/{slug}/appearance`, validates the hex and nothing more |
| Gate | `web/src/lib/brand.ts`, in the browser, against the loaded stylesheet |
| Applied as | inline `--primary`, `--primary-fg`, `--primary-subtle`, `--focus` on `<html>` |
| Not applied to | the six chart colors — see below |

The endpoint deliberately does *not* judge readability. Answering that means
knowing the palette, and the palette lives in `web/src/style.css`; a second copy
in Python would drift, and the copy that drifted quietly would be the one
deciding. Instead the client refuses to **apply** a stored color that fails the
gate, falling back to the shipped palette — which closes the loop for anything
written straight to the API.

The charts keep their own colors. Those six were found by a search that holds
every pair 20 CIEDE2000 apart and 11 apart under each of three dichromacies;
dropping the shop's color into series 1 would break a promise the build gate
makes, and nothing about a brand color says the first slice of a pie should
be it.

---

## Decisions

| | |
|---|---|
| "Not selling" over "Dead stock" | **Settled** — plain language wins. |
| Staffing lives under Reports › Busy hours | **Settled** — no sixth destination. |
| `grain` param on `/dashboard` | **Approved.** |
| Business color in `Tenant.settings` + a write endpoint | **Approved, and built.** |
| Supabase Auth | Deployment-time, not part of this overhaul. |

**Still open: the desktop command palette (Cmd/Ctrl+K).** Recommendation is to
defer it until after the pages are rebuilt — it is a real chunk of work, it is
desktop-only, and mobile is the priority. The sidebar and top bar in step 5
should leave the slot for it. Say so if you would rather have it in step 5.
