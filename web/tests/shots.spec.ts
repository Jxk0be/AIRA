import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { test } from '@playwright/test'

import { open, ROUTES } from './support/app'

/**
 * Full-page screenshots of every screen, theme and size, into `docs/ui/after/`
 * beside the `before/` shots the overhaul started from.
 *
 * Only run by `python tasks.py ui-shots`, never by `ui-check`: writing 40 PNGs
 * into the repo is a thing you ask for, not a side effect of running the tests.
 * The guard is the UI_SHOTS variable the task sets.
 *
 * These are for looking at — by me, before claiming a page is done, and by you,
 * comparing against `before/`. They are deliberately not `toHaveScreenshot()`
 * assertions: a pixel-diff gate on a design that is still changing daily would
 * fail on every intended change and teach everyone to ignore it.
 */
const HERE = dirname(fileURLToPath(import.meta.url))
const AFTER = resolve(HERE, '../../docs/ui/after')

test.describe('screenshots', () => {
  test.skip(!process.env.UI_SHOTS, 'run via `python tasks.py ui-shots`')

  for (const route of ROUTES) {
    test(`${route.name}`, async ({ page }, testInfo) => {
      await open(page, route.path, testInfo)

      // `fullPage` extends the *document*, and this app does not scroll the
      // document — `App.vue` makes <main> the scroll container at 100dvh so the
      // assistant's composer can stay above the mobile keyboard. Left alone,
      // every screenshot is exactly one viewport tall with the rest cropped off.
      //
      // So: let every inner scroller grow to its content for the shot. This
      // changes the page, which is why it lives here and not in the shared
      // helper — no assertion should ever run against a page edited like this.
      // Pass 1: release the 100dvh cages. `App.vue` pins the shell to the
      // viewport so the assistant's composer can sit above the mobile keyboard,
      // and that is what stops the page from ever being taller than one screen.
      await page.evaluate(() => {
        for (const el of document.querySelectorAll<HTMLElement>('*')) {
          const height = el.getBoundingClientRect().height
          if (Math.abs(height - window.innerHeight) < 2) {
            el.style.height = 'auto'
            el.style.maxHeight = 'none'
            el.style.minHeight = '0'
          }
        }
        document.documentElement.style.height = 'auto'
        document.body.style.height = 'auto'
      })
      await page.waitForTimeout(150)

      // Pass 2: grow every vertical scroller to its content — but never touch
      // `overflow`. Setting it to `visible` also unclips the horizontal axis,
      // and the inventory table's 46rem minimum would then stretch a 375px
      // phone shot to 845px: a screenshot of a viewport nobody has.
      for (let round = 0; round < 3; round++) {
        const grew = await page.evaluate(() => {
          let changed = 0
          for (const el of document.querySelectorAll<HTMLElement>('*')) {
            if (!['auto', 'scroll'].includes(getComputedStyle(el).overflowY)) continue
            if (el.scrollHeight > el.clientHeight + 1) {
              el.style.maxHeight = 'none'
              el.style.height = `${el.scrollHeight}px`
              changed++
            }
          }
          return changed
        })
        if (grew === 0) break
        await page.waitForTimeout(120)
      }
      await page.waitForTimeout(250)

      const size = testInfo.project.name.startsWith('mobile') ? 'mobile' : 'desktop'
      const theme = testInfo.project.name.endsWith('dark') ? 'dark' : 'light'
      const folder = resolve(AFTER, size, theme)
      mkdirSync(folder, { recursive: true })

      await page.screenshot({
        path: resolve(folder, `${route.name}.png`),
        fullPage: true,
        animations: 'disabled',
      })
    })
  }
})
