import { expect, test } from '@playwright/test'

import { open, TENANT } from './support/app'

/**
 * Old links must keep working.
 *
 * Every finding the backend writes carries a `suggested_action.route` — strings
 * like `reorder` or `dashboard?day=2026-09-17` — and those are already sitting
 * in the `insights` table. `digest/build.py` builds the weekly email's links
 * from the same values, so they are also in inboxes nobody can edit.
 *
 * So these are not a nicety. A broken redirect here means a finding the owner
 * has not actioned yet leads nowhere, and so does every link in every digest
 * already sent. The detectors stay untouched; the router absorbs the rename,
 * and this is what stops someone quietly removing a redirect later.
 *
 * Runs once per project rather than per screen — a redirect does not care about
 * the viewport — but the cost of letting it run in all four is a few seconds.
 */
const MOVED: [string, string][] = [
  [`/${TENANT}/dashboard`, `/${TENANT}`],
  [`/${TENANT}/insights`, `/${TENANT}`],
  [`/${TENANT}/assistant`, `/${TENANT}/ask`],
  [`/${TENANT}/inventory`, `/${TENANT}/stock?tab=all`],
  [`/${TENANT}/reorder`, `/${TENANT}/stock?tab=reorder`],
  [`/${TENANT}/dead-stock`, `/${TENANT}/stock?tab=not-selling`],
  [`/${TENANT}/staffing`, `/${TENANT}/reports?tab=busy-hours`],
  [`/${TENANT}/month-end`, `/${TENANT}/reports?tab=month-end`],
  [`/${TENANT}/data`, `/${TENANT}/settings?tab=data`],
  // Existed only between steps 5 and 6, while the findings list moved to Home.
  [`/${TENANT}/worth-doing`, `/${TENANT}`],
]

for (const [from, to] of MOVED) {
  test(`${from} still works`, async ({ page }, testInfo) => {
    await open(page, from, testInfo)
    const landed = new URL(page.url())
    expect(landed.pathname + landed.search, `${from} should land on ${to}`).toBe(to)
  })
}

test('a query string survives the redirect', async ({ page }, testInfo) => {
  // `dashboard?day=…` is what the sales-anomaly detector writes
  // (anomalies/detectors.py:231). Losing the day loses the finding's point.
  await open(page, `/${TENANT}/dashboard?day=2026-09-17`, testInfo)
  expect(new URL(page.url()).searchParams.get('day')).toBe('2026-09-17')
})

test('an unknown path does not dead-end', async ({ page }, testInfo) => {
  await open(page, `/${TENANT}/nonsense`, testInfo)
  await expect(page.locator('main#main')).toBeVisible()
})
