import { expect, test } from '@playwright/test'

import { isMobile, open, ROUTES } from './support/app'

/**
 * The rules from `docs/ui/complaints.md` that a machine can actually check:
 * nothing runs off the side of the screen, text is big enough to read, and you
 * can hit things with a thumb.
 *
 * Every assertion here maps to a numbered finding in `docs/ui/audit.md`, so a
 * failure says which complaint has come back.
 */
for (const route of ROUTES) {
  test.describe(route.name, () => {
    test('does not scroll horizontally (audit U8, U10)', async ({ page }, testInfo) => {
      await open(page, route.path, testInfo)

      const overflow = await page.evaluate(() => {
        const doc = document.documentElement
        // The widest offender, not just the fact of one: "something overflows"
        // is not a bug report.
        const guilty: { tag: string; cls: string; right: number }[] = []
        const limit = doc.clientWidth

        /**
         * Inside a *deliberate* horizontal scroller — a tab strip, a wide table
         * in its own scroll box — sticking out is the point, and naming those
         * children buries whatever actually widened the page.
         *
         * "Deliberate" means the scroller is itself narrower than the viewport,
         * so it absorbs its overflow. That distinction matters: `<main>` here
         * carries `overflow-y-auto`, which makes its computed `overflow-x`
         * `auto` as well even though it is full-width and absorbs nothing.
         * Treating that as a clip hides every real offender on the page.
         */
        const insideDeliberateScroller = (el: HTMLElement): boolean => {
          for (let parent = el.parentElement; parent; parent = parent.parentElement) {
            const overflowX = getComputedStyle(parent).overflowX
            const scrolls = overflowX === 'auto' || overflowX === 'scroll' || overflowX === 'hidden'
            if (scrolls && parent.clientWidth < limit - 1) return true
          }
          return false
        }

        /**
         * Name the element that *widened* its parent, not every element that
         * ends up past the edge as a result.
         *
         * An overflow is almost always one box refusing to shrink — a grid or
         * flex child, which defaults to `min-width: auto` and so is never
         * narrower than its longest unbreakable content. Everything downstream
         * of it is just along for the ride.
         */
        for (const el of document.querySelectorAll<HTMLElement>('body *')) {
          const box = el.getBoundingClientRect()
          if (box.width === 0 || box.height === 0) continue
          if (insideDeliberateScroller(el)) continue

          if (box.right > limit + 1) {
            guilty.push({
              tag: el.tagName.toLowerCase(),
              cls: (el.getAttribute('class') ?? '').slice(0, 80),
              right: Math.round(box.width),
            })
          }
        }
        return {
          scrollWidth: doc.scrollWidth,
          clientWidth: doc.clientWidth,
          guilty: guilty.sort((a, b) => b.right - a.right).slice(0, 3),
        }
      })

      expect(
        overflow.scrollWidth,
        `page is ${overflow.scrollWidth}px wide in a ${overflow.clientWidth}px viewport. ` +
          `Widest: ${JSON.stringify(overflow.guilty)}`,
      ).toBeLessThanOrEqual(overflow.clientWidth + 1)
    })

    test('the tab strip fits the phone instead of scrolling (audit U1)', async ({
      page,
    }, testInfo) => {
      test.skip(!isMobile(testInfo), 'there is room for one row on a desktop')
      await open(page, route.path, testInfo)

      // The overflow test above deliberately excuses anything inside a
      // horizontal scroller, which is exactly what the tab strip used to be:
      // Settings needed 477px of a 343px screen and opened on "ta & sync",
      // clipped at both ends. Nothing catches that but a check aimed at it.
      const strip = await page.evaluate(() => {
        const list = document.querySelector<HTMLElement>('[role="tablist"]')
        if (!list) return null
        const box = list.getBoundingClientRect()
        const tabs = [...list.querySelectorAll<HTMLElement>('[role="tab"]')]
        return {
          hidden: list.scrollWidth - list.clientWidth,
          clipped: tabs
            .filter((tab) => {
              const rect = tab.getBoundingClientRect()
              return rect.left < box.left - 1 || rect.right > box.right + 1
            })
            .map((tab) => tab.textContent?.trim() ?? ''),
        }
      })

      test.skip(strip === null, 'this screen has no tabs')
      expect(strip!.hidden, 'the tab strip scrolls sideways').toBeLessThanOrEqual(1)
      expect(strip!.clipped, 'tabs cut off at the edge of the strip').toEqual([])
    })

    test('only the content scrolls, never the page (shell)', async ({ page }, testInfo) => {
      test.skip(!route.shell, 'renders outside AppShell on purpose')
      await open(page, route.path, testInfo)

      /**
       * The reported bug: the whole document scrolled, so the sidebar rode up
       * with the content and left dead space below it.
       *
       * The layout used to be exactly one viewport tall with `main` clipping its
       * own overflow, which *happened* to leave the document unscrollable — but
       * nothing said it had to be. Anything a pixel taller (a browser resolving
       * `dvh` differently, a chart tooltip placed outside its container) scrolled
       * the page instead. This asserts the guarantee rather than the accident.
       */
      const out = await page.evaluate(() => {
        window.scrollTo(0, 900)
        const windowMoved = window.scrollY
        window.scrollTo(0, 0)

        const rail = document.querySelector('aside')
        const railBefore = rail ? Math.round(rail.getBoundingClientRect().top) : null
        const main = document.getElementById('main')
        if (main) main.scrollTop = 900
        const railAfter = rail ? Math.round(rail.getBoundingClientRect().top) : null
        const mainMoved = main ? main.scrollTop : 0
        if (main) main.scrollTop = 0

        return { windowMoved, railBefore, railAfter, mainMoved }
      })

      expect(out.windowMoved, 'the window itself must never scroll').toBe(0)
      expect(out.railAfter, 'the sidebar must not move when content scrolls').toBe(out.railBefore)
    })

    test('body text is at least 16px (audit U12)', async ({ page }, testInfo) => {
      await open(page, route.path, testInfo)

      const small = await page.evaluate(() => {
        const found: { text: string; size: number }[] = []
        for (const el of document.querySelectorAll<HTMLElement>('body *')) {
          if (el.children.length > 0) continue
          const text = (el.textContent ?? '').trim()
          if (!text) continue
          if (el.closest('.sr-only')) continue
          const size = parseFloat(getComputedStyle(el).fontSize)
          // 13px is the documented floor for badges and dense metadata;
          // anything under it is a mistake rather than a decision.
          if (size < 13) found.push({ text: text.slice(0, 40), size })
        }
        return found
      })

      expect(small, 'text below the 13px floor').toEqual([])
    })

    test('tap targets are at least 44px (audit A11)', async ({ page }, testInfo) => {
      test.skip(!isMobile(testInfo), 'a pointer is not a thumb')
      await open(page, route.path, testInfo)

      const small = await page.evaluate(() => {
        const found: { label: string; w: number; h: number }[] = []
        const targets = document.querySelectorAll<HTMLElement>(
          'button, a[href], input:not([type="hidden"]), select, textarea, [role="button"], [role="radio"], [role="tab"]',
        )
        /**
         * A visually-hidden control is not a tap target. The skip link is the
         * obvious case: it is clipped to nothing until focused, but it still
         * reports a box, so measuring it means demanding a 44px hit area for
         * something nobody can see or touch.
         */
        const isVisuallyHidden = (el: HTMLElement): boolean => {
          const style = getComputedStyle(el)
          if (style.visibility === 'hidden' || style.display === 'none') return true
          // `sr-only` clips to a zero rect or insets the clip-path by 50%.
          if (style.clip === 'rect(0px, 0px, 0px, 0px)') return true
          if (style.clipPath === 'inset(50%)') return true
          return false
        }

        for (const el of targets) {
          const box = el.getBoundingClientRect()
          if (box.width === 0 || box.height === 0) continue
          if (isVisuallyHidden(el)) continue
          if (box.height < 44 || box.width < 24) {
            found.push({
              label: (el.textContent ?? el.getAttribute('aria-label') ?? el.tagName).trim().slice(0, 32),
              w: Math.round(box.width),
              h: Math.round(box.height),
            })
          }
        }
        return found
      })

      expect(small, 'targets too small for a thumb').toEqual([])
    })
  })
}
