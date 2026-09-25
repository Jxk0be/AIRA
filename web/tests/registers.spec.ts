import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from '@playwright/test'

import { open, TENANT } from './support/app'

/**
 * The section neither incumbent can ship.
 *
 * Animanga Knox runs two registers: RegisterOne at the counter and the con booth,
 * and a TCG marketplace it started selling on in July 2026 that hands it a
 * spreadsheet a month. So the recorded fixtures are a two-register shop, every
 * other spec in this suite renders the consolidation card, and axe and the
 * layout checks cover it without anybody opting in.
 *
 * What is worth testing here is the two ends of that: that the card leads the
 * screen for a shop with two tills, and that it disappears entirely for a shop
 * with one — because a permanent "connect another register" panel would be an
 * advert where a number should be, and most shops have one till.
 */

const FIXTURES = resolve(dirname(fileURLToPath(import.meta.url)), 'fixtures')

/** The recorded fixture, as a plain object we can narrow. */
function recorded(name: string): Record<string, unknown> {
  return JSON.parse(readFileSync(resolve(FIXTURES, `${name}.json`), 'utf8')) as Record<
    string,
    unknown
  >
}

/** The same shop with its marketplace disconnected. */
function oneRegister(profile: Record<string, unknown>): Record<string, unknown> {
  const sources = profile.sources as { source: string }[]
  return { ...profile, sources: [sources[0]] }
}

const NOTHING_TO_ADD_UP = {
  available: false,
  reason:
    'Everything Animanga Knox sells goes through RegisterOne (counter and booth), so there is ' +
    'nothing to add together yet. Connect a second register and this splits by system.',
  data: null,
}

test('a two-register shop gets the split, first', async ({ page }, testInfo) => {
  const { missing } = await open(page, `/${TENANT}/reports?tab=sales`, testInfo)
  expect(missing).toEqual([])

  await expect(page.getByRole('heading', { name: 'All your registers' })).toBeVisible()

  // Both registers, by the shop's own names for them — not the source slugs.
  await expect(page.getByText('RegisterOne (counter and booth)')).toBeVisible()
  await expect(page.getByText('CardNexus (online storefront)')).toBeVisible()
  await expect(page.getByText('animanga_knox_online')).toHaveCount(0)

  // The *first* section on the screen, not merely ahead of some of them: for a
  // shop with two tills this is the reason they are here, so nothing outranks it.
  const sections = await page.locator('h2, h3').allTextContents()
  const first = sections.find((text) => text.trim().length > 0)
  expect(first).toContain('All your registers')
})

test('the split adds up to what the rest of the screen says', async ({ page }, testInfo) => {
  /**
   * A consolidated figure that does not reconcile to its own parts is worse than
   * no consolidated figure, because somebody will read it to a bookkeeper. The
   * API guarantees this; the fixture has to keep telling the UI the truth about
   * it, or these specs stop being evidence of anything.
   */
  await open(page, `/${TENANT}/reports?tab=sales`, testInfo)

  const dashboard = recorded('dashboard') as {
    kpis: { key: string; value: string }[]
    by_source: { data: { rows: { net_sales: string }[]; total_net_sales: string } }
  }
  const net = Number(dashboard.kpis.find((k) => k.key === 'net_sales')!.value)
  const summed = dashboard.by_source.data.rows.reduce((total, r) => total + Number(r.net_sales), 0)

  expect(summed).toBeCloseTo(net, 2)
  expect(Number(dashboard.by_source.data.total_net_sales)).toBeCloseTo(net, 2)
})

test('a one-register shop is not shown a consolidation card', async ({ page }, testInfo) => {
  const { missing } = await open(page, `/${TENANT}/reports?tab=sales`, testInfo, {
    profile: oneRegister(recorded('profile')),
    dashboard: { ...recorded('dashboard'), by_source: NOTHING_TO_ADD_UP },
  })
  expect(missing).toEqual([])

  await expect(page.getByRole('heading', { name: 'All your registers' })).toHaveCount(0)
  // Not even the refusal sentence: the card is absent, not empty.
  await expect(page.getByText('nothing to add together yet')).toHaveCount(0)

  // The other breakdowns are unaffected.
  await expect(page.getByRole('heading', { name: 'In store or online' })).toBeVisible()
})
