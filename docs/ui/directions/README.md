# Two visual directions

Open these in a browser — no build step, no network needed:

    docs/ui/directions/direction-a.html
    docs/ui/directions/direction-b.html

Each file shows the **new Home screen** from `../ia.md` at 375×812 and 1280×800,
in light and dark, plus the six-color chart palette in both themes.

---

## The choice

|  | **A — Counter** | **B — Ledger** |
|---|---|---|
| Feel | Airy, spacious, one accent | Dense, structured, hairline rules |
| Type | One sans throughout | Serif headings, mono figures |
| Surfaces | Rounded cards, soft shadow | Square panels, visible rules |
| Accent | Indigo `#3D46B8` | Deep green `#0F5547` (today's brand, deepened) |
| Relationship to today | A break from it | An evolution of it |

**A suits** an owner who opens this between customers and wants one answer fast.
Everything is spaced for a thumb and a glance, nothing competes for attention.
**It costs density** — long lists like the 333-item catalog mean more scrolling.

**B suits** an owner who reads numbers all day and wants more on screen at once:
the whole KPI line, three findings and the chart without scrolling. **It costs
calm** — more rules and more type sizes mean more to take in, and it is less
forgiving if a page gets crowded later.

You can also mix: "A's layout with B's mono figures" is a reasonable answer.

---

## What both directions already fix

These are not styling preferences — they are the audit findings, demonstrated:

- **U1, U2** — five-item bottom tab bar instead of nine scrolling tabs. The shop
  switcher is gone from the top of the screen, giving back ~56px.
- **U6, U7** — the severity label sits in a fixed-width rail, so every finding
  title starts at the same x. Measured: all three titles at x=442 (A) and x=406
  (B), identical in light and dark.
- **U8, U10** — no horizontal overflow in any of the eight frames. Measured:
  `scrollWidth === clientWidth` at both 375 and 1280.
- **U11** — caveats kept, but demoted to a quiet line under the chart instead of
  a yellow block bigger than the number it qualifies.
- **U12** — body text 16px, nothing below 12px (the notification count).
- **U15** — the Pinned panel is shown mid-skeleton, honoring `prefers-reduced-motion`.
- **U18** — a Light / Dark / System control, with **Light selected by default**.
- **A2, A4** — severity reads "Urgent" / "Worth a look" in words as well as
  color. KPI deltas carry ▲/▼ and a sign, not just red and green.
- **A9** — every chart has a descriptive `aria-label` and a "View as table" toggle.
- **A11** — every tap target is ≥44×44. Measured: zero violations.
- **F2** — the Day / Week / Month control and "drag to zoom" are on the chart.

---

## The color work

Both palettes passed a gate before any markup was written. Text ≥4.5:1 on its
surface, UI and interactive borders ≥3:1, chart series ≥3:1 on their surface.

The chart palettes needed more than a contrast check. Two corrections worth
recording, because step 3's `check-contrast.ts` should inherit both:

**1. Luminance contrast is the wrong test between categorical series.**
Requiring it forces a light-to-dark ramp, which makes some categories look more
important than others — that is a sequential palette, not a qualitative one.
The right test is perceptual distance: **CIEDE2000 ΔE ≥ 20**.

**2. Six hand-picked hues do not survive color blindness.** The first attempt
passed every contrast rule and still collapsed under simulation — two series
were ΔE 1.4 apart for a deuteranope, i.e. the same color. Both palettes are now
searched under **Machado (2009) simulation for protanopia, deuteranopia and
tritanopia, requiring ΔE ≥ 11 in all three**, with the first series pinned to the
direction's accent and chroma capped so nothing turns neon.

This is also why the business-color picker (F1) cannot just store a hex: the
same gate has to run at runtime on whatever color the owner picks, and derive
`--color-primary-fg` from it rather than assuming white.

### Tokens

**A — Counter**

| | Light | Dark |
|---|---|---|
| bg / surface / raised | `#FBFBF9` `#FFFFFF` `#F4F4F1` | `#15161A` `#1C1E23` `#24272D` |
| text / muted | `#1A1A17` `#5C5C55` | `#ECEDF0` `#A9ADB6` |
| border / border-strong | `#E4E4DF` `#8E8E86` | `#2E3138` `#7C818C` |
| accent / on-accent | `#3D46B8` `#FFFFFF` | `#949BF5` `#13141A` |
| success / warning / danger / info | `#1C6B42` `#8A5A00` `#B3301F` `#1F5C8A` | `#5FD196` `#E0A83C` `#F1836C` `#69B6E8` |
| chart 1–6 | `#3D46B8` `#794060` `#BB754B` `#9280B1` `#605535` `#119681` | `#949BF5` `#C4A963` `#DB7B88` `#75E2C2` `#FFC2F9` `#23A2B0` |

**B — Ledger**

| | Light | Dark |
|---|---|---|
| bg / surface / raised | `#F6F4EF` `#FFFFFF` `#EFECE4` | `#131210` `#1B1A16` `#232119` |
| text / muted | `#14130F` `#5B5649` | `#F1ECE2` `#ADA491` |
| border / border-strong | `#DFD9CC` `#8B8474` | `#332F27` `#857C6A` |
| accent / on-accent | `#0F5547` `#FFFFFF` | `#6FC5A3` `#0F110F` |
| focus ring | `#A33E18` | `#E8926A` |
| success / warning / danger / info | `#1B6B41` `#7E5400` `#A93122` `#1C5580` | `#63CC8D` `#DBA94A` `#E8846A` `#6FB0DE` |
| chart 1–6 | `#0F5547` `#808A3C` `#4E4380` `#754115` `#B75D73` `#3F92A8` | `#6FC5A3` `#798EDC` `#C38AAD` `#9D9272` `#FAC785` `#D0D1FE` |

Note B gives the focus ring its own color (a warm rust) because its accent is
also the primary-button background — audit A12.

---

## Caveats on these mockups

- Static HTML. Nothing is clickable; the segmented controls and buttons are
  rendered states, not behavior.
- Fonts are system stacks with the intended faces named first. Direction B is
  designed for Fraunces + IBM Plex; without them installed it falls back to
  Georgia and Consolas and reads slightly heavier than intended.
- Phone frames are 760px tall rather than 812 so both fit side by side, so the
  fold sits a little higher here than on a real handset.
- The data is Animanga Knox's seeded catalog and is internally
  consistent: $12,160.45 net over 412 orders is the $29.52 average shown.

---

## What I need from you

Pick A or B, or tell me what to combine. Then step 3 builds the chosen direction
as real tokens and primitives in `web/`, with the contrast gate wired into
`python tasks.py ui-check`.
