import { expect, test, type Page } from '@playwright/test'

import { open, TENANT } from './support/app'

/**
 * One button at a time.
 *
 * Every screen with a row of buttons held a single `working` boolean and bound
 * it to all of them, so pressing "Make a draft" for one supplier spun the
 * button for all five — and the owner could not tell which order was being
 * built, or whether the press had landed on the right one at all.
 *
 * Nothing in the suite could catch it, for a reason worth keeping: the fixture
 * mock answers every write instantly, so a loading state exists for less than
 * a frame and a screenshot of a settled page shows nothing. These specs hold
 * the response open instead, and assert on the state while it is held.
 *
 * What is asserted is "exactly one", not "this one is busy" — a check that only
 * looks at the button that was pressed passes just as happily when all five are
 * spinning, which is the bug.
 */

/**
 * Hold a write open until the test has looked at the page.
 *
 * Matched on `pathname` rather than a glob: these endpoints carry query
 * strings — `/reorder/drafts?vendor_id=…` — and a glob is compared against the
 * whole URL, so it silently misses and the fixture mock answers instantly
 * instead. A held route that quietly does not hold makes this spec pass on the
 * bug it exists to catch.
 */
async function holdOpen(page: Page, path: string, body: unknown): Promise<() => void> {
  let release = () => {}
  const held = new Promise<void>((resolve) => {
    release = resolve
  })
  // Registered after `open`, and Playwright prefers the newest matching route,
  // so this wins over the catch-all the fixture mock gives every write.
  await page.route(
    (url) => url.pathname === path,
    async (route) => {
      await held
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(body),
      })
    },
  )
  return release
}

const busy = (page: Page, name: string) => page.getByRole('button', { name }).and(page.locator('[aria-busy="true"]'))

test.describe('only the button you pressed', () => {
  test('a draft spins one supplier, not every supplier', async ({ page }, testInfo) => {
    await open(page, `/${TENANT}/stock?tab=reorder`, testInfo)
    const release = await holdOpen(page, `/api/tenants/${TENANT}/reorder/drafts`, [])

    const buttons = page.getByRole('button', { name: 'Make a draft' })
    const before = await buttons.count()
    test.skip(before < 2, 'this fixture has fewer than two suppliers to tell apart')
    await buttons.nth(1).click()

    // One spinner, and it is the second one.
    await expect(busy(page, 'Make a draft')).toHaveCount(1)
    await expect(buttons.nth(1)).toHaveAttribute('aria-busy', 'true')
    // The rest go quiet rather than pretend to be working.
    await expect(buttons.nth(0)).toBeDisabled()
    await expect(buttons.nth(0)).not.toHaveAttribute('aria-busy', 'true')

    release()
    await expect(busy(page, 'Make a draft')).toHaveCount(0)
  })

  test('logging one rescue spins one row', async ({ page }, testInfo) => {
    await open(page, `/${TENANT}/stock?tab=not-selling`, testInfo)
    const release = await holdOpen(page, `/api/tenants/${TENANT}/dead-stock/actions`, {})

    const buttons = page.getByRole('button', { name: 'I did this' })
    const before = await buttons.count()
    test.skip(before < 2, 'this fixture has fewer than two rows to tell apart')
    await buttons.first().click()

    await expect(busy(page, 'I did this')).toHaveCount(1)
    await expect(buttons.first()).toHaveAttribute('aria-busy', 'true')

    release()
  })

  test('saving a color does not spin the button that undoes it', async ({ page }, testInfo) => {
    await open(page, `/${TENANT}/settings?tab=appearance`, testInfo)

    // "Use the default" only exists once there is something to go back from,
    // and this shop starts with the color we ship — so a first save has to
    // land before the two buttons are even on screen together. Without it this
    // spec measures one button against itself and passes on the bug.
    const save = page.getByRole('button', { name: 'Save this color' })
    const undo = page.getByRole('button', { name: 'Use the default' })
    await page.getByRole('button', { name: 'Rust' }).click()
    await save.click()
    await expect(undo).toBeVisible()

    const release = await holdOpen(page, `/api/tenants/${TENANT}/appearance`, {
      tenant: TENANT,
      brand_color: '#2f6f4f',
    })
    await page.getByRole('button', { name: 'Forest' }).click()
    await save.click()

    await expect(page.locator('[aria-busy="true"]')).toHaveCount(1)
    await expect(save).toHaveAttribute('aria-busy', 'true')
    await expect(undo).not.toHaveAttribute('aria-busy', 'true')

    release()
  })
})
