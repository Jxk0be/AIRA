import { expect, test, type Page } from '@playwright/test'

import { open, TENANT } from './support/app'

/**
 * A write that says it worked has to leave the screen showing what it did.
 *
 * These are the writes the API answers with `204 No Content`. They were all
 * broken the same way: the helper that runs a write reported failure by
 * returning `undefined`, which is also what a successful 204 returns, so the
 * "did that work?" check was never true. The toast said the line was removed
 * and the row stayed there until a reload.
 *
 * The fixture mock answers those paths with a real 204 (see `support/app.ts`),
 * because a mock that returns `{}` makes all of this pass while the browser
 * does not.
 */

const REORDER = `/${TENANT}/stock?tab=reorder`

const LINES = [
  {
    id: 'aaaaaaaa-0000-4000-8000-000000000001',
    variant_id: 'bbbbbbbb-0000-4000-8000-000000000001',
    name: 'Ramune Soda - Melon (200ml)',
    sku: 'DRI-RAMUNE-02-00101',
    quantity: '10',
    suggested_qty: '10',
    unit_cost: '1.73',
    line_cost: '17.30',
    on_hand_at_draft: '1',
    why: 'sells 2.8/week, 1 on hand, 14-day lead time',
    caveats: [],
    received_qty: null,
  },
  {
    id: 'aaaaaaaa-0000-4000-8000-000000000002',
    variant_id: 'bbbbbbbb-0000-4000-8000-000000000002',
    name: 'Glico Pocky - Matcha',
    sku: 'SNA-GLICOP-02-00088',
    quantity: '6',
    suggested_qty: '6',
    unit_cost: '1.73',
    line_cost: '10.38',
    on_hand_at_draft: '2',
    why: 'sells 2.4/week, 2 on hand, 14-day lead time',
    caveats: [],
    received_qty: null,
  },
]

const ORDER = {
  id: 'cccccccc-0000-4000-8000-000000000001',
  reference: 'PO-2026-09-0001',
  vendor_id: 'dddddddd-0000-4000-8000-000000000001',
  vendor_name: 'JFC International',
  vendor_email: 'knoxville@jfc.example',
  status: 'draft' as const,
  note: null,
  expected_at: null,
  units: '16',
  total_at_cost: '27.68',
  unpriced_lines: 0,
  mailto: null,
}

/**
 * Serve one draft order that really loses a line when the PATCH arrives.
 *
 * Registered after `open`, and Playwright prefers the newest matching route,
 * so this wins over the fixture mock for these paths.
 */
async function draftWithLines(page: Page): Promise<void> {
  let lines = [...LINES]
  const body = () => ({
    ...ORDER,
    lines,
    units: String(lines.reduce((total, line) => total + Number(line.quantity), 0)),
  })

  await page.route(
    (url) => url.pathname.includes('/purchase-orders'),
    async (route) => {
      const url = new URL(route.request().url())
      if (route.request().method() === 'PATCH') {
        const sent = JSON.parse(route.request().postData() ?? '{}') as { remove?: boolean }
        const id = url.pathname.split('/').pop()
        if (sent.remove) lines = lines.filter((line) => line.id !== id)
        await route.fulfill({ status: 204 })
        return
      }
      const one = /\/purchase-orders\/[^/]+$/.test(url.pathname)
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(one ? body() : [body()]),
      })
    },
  )
}

test.describe('a write that answers 204', () => {
  test('removing a line takes the row off the screen, without a reload', async ({
    page,
  }, testInfo) => {
    await open(page, REORDER, testInfo)
    await draftWithLines(page)
    await page.reload({ waitUntil: 'domcontentloaded' })

    await page.getByRole('button', { name: 'Review' }).first().click()

    const melon = page.getByRole('listitem').filter({ hasText: 'Ramune Soda - Melon (200ml)' })
    const pocky = page.getByRole('listitem').filter({ hasText: 'Glico Pocky - Matcha' })
    await expect(melon).toHaveCount(1)

    await melon.getByRole('button', { name: 'Remove' }).click()

    // The toast says so...
    await expect(page.getByText('Line removed')).toBeVisible()
    // ...and so does the list. This is the half that was broken.
    await expect(melon).toHaveCount(0)
    await expect(pocky).toHaveCount(1)
  })
})
