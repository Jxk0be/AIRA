# What is still weak

`python tasks.py ui-check` is green: 426 checks, twelve screens, both themes,
both sizes, plus 186 contrast pairs. That is a floor, not a verdict. It proves
no screen has an axe violation, a focus trap, an unreachable control, text under
13px, a tap target under 44px, a sideways scroll, or a colour pair that fails AA.

It proves nothing about whether the thing is good to use.

Ranked by how likely each is to bite, and what I would do about it.

---

## 1. The assistant's streaming has no automated test at all

The Playwright suite replays the API from fixtures, and fixtures cannot stream.
So the SSE loop in `api/stream.ts` — token accumulation, tool-call pairing, chart
events, the abort path — is covered by exactly one manual run I did by hand.

A regression there ships silently. It is the most complex code on the most
important screen.

**Fix:** a spec that serves a hand-written SSE body through `page.route` with a
chunked response, and asserts the turn ends with the right text, the right number
of tool chips, and the right live-region announcements. Half a day, and it would
have caught anything I broke while rebuilding `AskView`.

## 2. Dialogs, sheets and toasts are only ever tested closed

axe and the tap-target check run against the page as it loads. Nothing in the
suite opens the rename dialog, the delete confirmation, the history sheet, or
raises a toast — so the focus trap, the labelling and the target sizes *inside*
them rest on the one manual pass I did.

That is also where regressions hide, because overlays are the thing people forget
to re-check.

**Fix:** a spec per overlay that opens it, runs axe on the open state, tabs
through it, presses Escape, and asserts focus returned to the trigger. Same for a
toast: click a write, assert the live region got the message.

## 3. Only one shop is ever rendered

Every test uses `tsundoku`, which has every capability turned on. `panel_and_pawn`
exists in the same dev stack with **only** `has_costs` — no customers, no
inventory history, one location, no online channel.

The entire "degrade honestly" story — the sentence where a chart would have been,
the dash instead of a zero margin, the hidden channel split — is the product's
main argument and is not exercised by a single test.

**Fix:** record a second fixture set from `panel_and_pawn` and run the axe and
layout specs against both. The fixture recorder already takes a tenant via
`FIXTURE_TENANT`; it is mostly wiring.

## 4. Empty and error states are written but unverified

Every fixture is populated and every request succeeds. The empty states I wrote
("Nothing to do right now", "No packets yet"), and every error-with-retry block,
have never rendered in a test. Some of them I have never seen at all.

**Fix:** fixture variants — an empty set and a failing set — as two more
Playwright projects. Cheap, because the mock already fails loudly on a missing
fixture; it just needs an empty one and a 500 one.

## 5. Fixtures can go stale without anyone noticing

`web/tests/fixtures/` is a snapshot. If a Pydantic response model changes, the UI
tests keep passing against the old shape and the app breaks in the browser.

**Fix:** a CI job that re-runs `python tasks.py ui-fixtures` against a live API
and fails if the diff is non-empty. It needs the stack up, so it belongs in a
nightly rather than on every push.

## 6. The business colour picker is still a placeholder

You approved it in step 1 and it is a paragraph in Settings › Appearance saying
it does not exist. The storage question is answered (`Tenant.settings`, no
migration) and the hard part is unbuilt: the runtime contrast gate that has to
reject a colour the owner likes.

**Fix:** port `web/scripts/check-contrast.ts`'s ratio maths into the app, derive
`--primary-fg` from the chosen colour, and show the owner *why* a colour was
refused rather than silently correcting it.

## 7. `prefers-reduced-motion` is honoured but never tested

`style.css` kills animations under it and every overlay respects it. No test runs
with it set.

**Fix:** one Playwright project with `reducedMotion: 'reduce'`. Genuinely a
three-line change.

## 8. Zooming the chart is pointer-first

The slider handles are focusable, so it is *operable* by keyboard. But the only
instruction is "Drag across the chart to zoom in. Double-click to reset" — two
pointer verbs — and there is no keyboard shortcut to zoom.

**Fix:** arrow-key handling on the focused plot, and copy that names both routes.

## 9. The ECharts chunk is 572 KB

`ChartRenderer` pulls in 572 KB (about 202 KB gzipped) and every screen with a
chart waits for it. On the phone-behind-the-counter connection this product is
designed for, that matters more than it does here.

**Fix:** the imports are already selective; the remaining weight is the core plus
SVG renderer. Worth measuring whether the pie chart earns its import, and whether
Home's line chart could render as inline SVG with ECharts loaded only for Ask.

## 10. Conversation titles still collide

I fixed the history *drawer*; I did not fix what is in it. The fixture has four
conversations called "December sales" and no date grouping, so the list is hard
to scan for exactly the reason it was before.

**Fix:** group by day, and make the auto-titler disambiguate — it is a backend
change in the agent's titling prompt, not a UI one.

---

## And the thing no test can do

Nothing here has been on a real phone. `dvh`, the keyboard pushing the composer,
safe-area insets around the home indicator, and how any of it reads in sunlight
are all emulated at 375×812 in a headless browser.

Three things worth doing before calling this done:

1. **Use it on your actual phone for a few days**, in both themes, including
   outdoors.
2. **Try it with VoiceOver or TalkBack for two minutes.** It is the fastest way
   to find what axe cannot — whether the reading *order* makes sense, whether the
   announcements are useful or just noisy.
3. **Watch a pilot shop owner use it once without helping.** Where they hesitate
   is the next fix list, and it will not be any of the ten items above.
