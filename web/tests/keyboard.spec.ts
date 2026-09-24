import { expect, test } from '@playwright/test'

import { open, ROUTES } from './support/app'

/**
 * Tab through a screen and check three things axe cannot: that focus is always
 * visible, that it never gets stuck, and that there is a way past the
 * navigation to the content.
 *
 * axe checks the markup a page *has*; none of this is in the markup. A focus
 * ring that a card's `overflow:hidden` clips is invisible and perfectly valid
 * HTML, and a focus trap is only detectable by actually pressing Tab.
 */
for (const route of ROUTES) {
  test.describe(route.name, () => {
    test('focus is always visible and never trapped (audit A7, A12)', async ({ page }, testInfo) => {
      await open(page, route.path, testInfo)

      const seen: string[] = []
      /**
       * Per element, not per stop.
       *
       * A segmented control like `<input type="time">` takes several Tab
       * presses, and the browser draws its own highlight on the segment rather
       * than re-running the element's focus ring on every one. What matters is
       * that the element shows a ring while it holds focus, so this records
       * whether *any* of its stops did.
       */
      const ring = new Map<string, { label: string; everVisible: boolean }>()
      let stuck: string | null = null

      // 60 is well past the longest screen; a real trap shows up in the first
      // handful of repeats rather than needing an exhaustive walk.
      for (let step = 0; step < 60; step++) {
        await page.keyboard.press('Tab')

        const current = await page.evaluate(() => {
          const el = document.activeElement as HTMLElement | null
          if (!el || el === document.body) return null

          const style = getComputedStyle(el)
          const box = el.getBoundingClientRect()

          // "Visible" means a ring you could actually see: an outline with a
          // width, or a box-shadow standing in for one. `outline: none` with
          // nothing in its place is the failure this is looking for.
          const outlineWidth = parseFloat(style.outlineWidth || '0')
          const hasOutline = outlineWidth > 0 && style.outlineStyle !== 'none'
          const hasShadowRing = style.boxShadow !== 'none' && style.boxShadow !== ''
          const onScreen = box.width > 0 && box.height > 0

          // Identity, not label. A page can legitimately have four buttons all
          // reading "Make a draft" — one per supplier — and comparing by text
          // calls that a focus trap when it is just a list.
          const marked = el.dataset.tabProbe
          const id = marked ?? String((window as unknown as { __tab: number }).__tab ?? 0)
          if (!marked) {
            const seq = ((window as unknown as { __tab: number }).__tab ?? 0) + 1
            ;(window as unknown as { __tab: number }).__tab = seq
            el.dataset.tabProbe = String(seq)
          }

          const label =
            el.tagName.toLowerCase() +
            (el.getAttribute('aria-label') ? `[${el.getAttribute('aria-label')}]` : '') +
            (el.textContent ? `:${el.textContent.trim().slice(0, 24)}` : '')

          // `time`, `date` and `datetime-local` are single elements made of
          // several segments, and Tab walks the segments before leaving. Three
          // stops on one element is correct there, not a trap.
          const type = (el as HTMLInputElement).type ?? ''
          const multiStop =
            el.tagName === 'INPUT' && ['time', 'date', 'datetime-local', 'week', 'month'].includes(type)

          return {
            id: el.dataset.tabProbe ?? id,
            label: multiStop ? `${label}[${type}]` : label,
            multiStop,
            visible: (hasOutline || hasShadowRing) && onScreen,
            onScreen,
          }
        })

        if (!current) break // tabbed back out to the browser chrome

        if (current.onScreen) {
          const record = ring.get(current.id) ?? { label: current.label, everVisible: false }
          record.everVisible = record.everVisible || current.visible
          ring.set(current.id, record)
        }

        // The *same element* three times running is a trap. A repeated label is
        // not, and neither is a segmented date or time field.
        const recent = seen.slice(-2)
        if (!current.multiStop && recent.length === 2 && recent.every((one) => one === current.id)) {
          stuck = current.label
          break
        }
        seen.push(current.id)
      }

      const invisible = [...ring.values()].filter((one) => !one.everVisible).map((one) => one.label)

      expect(stuck, 'focus is trapped outside a dialog').toBeNull()
      expect(invisible, 'focusable elements that never showed a focus ring').toEqual([])
      expect(seen.length, 'nothing was reachable by keyboard').toBeGreaterThan(0)
    })

    test('has a skip link to the main content (audit A7)', async ({ page }, testInfo) => {
      test.skip(!route.shell, 'renders outside AppShell on purpose')
      await open(page, route.path, testInfo)
      await page.keyboard.press('Tab')

      const first = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null
        return el ? { text: (el.textContent ?? '').trim(), href: el.getAttribute('href') } : null
      })

      expect(first?.text ?? '', 'the first tab stop should skip to the content').toMatch(/skip/i)
      expect(first?.href ?? '', 'the skip link should point at #main').toContain('#')
    })

    test('has one main landmark and a labelled nav (audit A7)', async ({ page }, testInfo) => {
      test.skip(!route.shell, 'renders outside AppShell on purpose')
      await open(page, route.path, testInfo)

      const landmarks = await page.evaluate(() => ({
        main: document.querySelectorAll('main, [role="main"]').length,
        mainHasId: !!document.querySelector('main[id], [role="main"][id]'),
        navs: [...document.querySelectorAll('nav, [role="navigation"]')].map(
          (el) => el.getAttribute('aria-label') ?? el.getAttribute('aria-labelledby') ?? '',
        ),
      }))

      expect(landmarks.main, 'exactly one <main>').toBe(1)
      expect(landmarks.mainHasId, '<main> needs an id for the skip link').toBe(true)
      expect(
        landmarks.navs.filter((label) => label === ''),
        'every <nav> needs an aria-label',
      ).toEqual([])
    })
  })
}
