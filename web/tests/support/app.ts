/**
 * What every UI spec needs: a page whose API is replayed from disk and whose
 * theme is the one the project asked for.
 */

import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import type { Page, TestInfo } from '@playwright/test'

const HERE = dirname(fileURLToPath(import.meta.url))
const FIXTURES = resolve(HERE, '../fixtures')

export const TENANT = 'tsundoku'

/**
 * Every screen, by the name the reports and screenshots use.
 *
 * `shell: false` marks a page that deliberately renders outside `AppShell` —
 * only the dev styleguide. The skip link, the `<main>` landmark and the
 * labelled `<nav>` are properties *of the shell*, so asserting them on a page
 * that has no shell would be testing something nobody built. Everything else —
 * axe, layout, focus visibility — still runs on it.
 */
export const ROUTES = [
  { name: 'home', path: `/${TENANT}`, shell: true },
  { name: 'ask', path: `/${TENANT}/ask`, shell: true },
  { name: 'stock-all', path: `/${TENANT}/stock?tab=all`, shell: true },
  { name: 'stock-reorder', path: `/${TENANT}/stock?tab=reorder`, shell: true },
  { name: 'stock-not-selling', path: `/${TENANT}/stock?tab=not-selling`, shell: true },
  { name: 'reports-month-end', path: `/${TENANT}/reports?tab=month-end`, shell: true },
  { name: 'reports-sales', path: `/${TENANT}/reports?tab=sales`, shell: true },
  { name: 'reports-busy-hours', path: `/${TENANT}/reports?tab=busy-hours`, shell: true },
  { name: 'settings-data', path: `/${TENANT}/settings?tab=data`, shell: true },
  { name: 'settings-documents', path: `/${TENANT}/settings?tab=documents`, shell: true },
  { name: 'settings-notifications', path: `/${TENANT}/settings?tab=notifications`, shell: true },
  { name: 'settings-appearance', path: `/${TENANT}/settings?tab=appearance`, shell: true },
  { name: 'styleguide', path: '/styleguide', shell: false },
] as const

const cache = new Map<string, string>()

function fixture(name: string): string | null {
  if (!cache.has(name)) {
    try {
      cache.set(name, readFileSync(resolve(FIXTURES, `${name}.json`), 'utf8'))
    } catch {
      return null
    }
  }
  return cache.get(name) ?? null
}

/**
 * Map a request URL onto a fixture file.
 *
 * Longest prefix first, so `/dead-stock/actions` does not get served the
 * `/dead-stock` body and `/notifications/messages` does not get `/notifications`.
 */
function fixtureFor(url: URL): string | null {
  const path = url.pathname.replace(/^\/api/, '')
  if (path === '/tenants') return 'tenants'

  const rest = path.replace(new RegExp(`^/tenants/${TENANT}`), '')

  const table: [string, string][] = [
    ['/insights', url.searchParams.get('limit') === '3' ? 'insights-top' : 'insights'],
    ['/dead-stock/actions', 'dead-stock-actions'],
    ['/dead-stock', 'dead-stock'],
    ['/notifications/messages', 'notification-messages'],
    ['/notifications', 'notifications'],
    ['/digest/preview', 'digest-preview'],
    ['/purchase-orders', 'purchase-orders'],
    ['/month-end', 'month-end'],
    ['/dashboard', 'dashboard'],
    ['/inventory', 'inventory'],
    ['/conversations', 'conversations'],
    ['/assistant', 'assistant'],
    ['/profile', 'profile'],
    ['/staffing', 'staffing'],
    ['/reorder', 'reorder'],
    ['/charts', 'charts'],
    ['/value', 'value'],
    ['/jobs', 'jobs'],
    ['/data', 'data'],
  ]

  for (const [prefix, name] of table.sort((a, b) => b[0].length - a[0].length)) {
    if (rest.startsWith(prefix)) return name
  }
  return null
}

/**
 * Serve the whole API from `tests/fixtures`.
 *
 * A GET with no fixture is failed loudly rather than passed through: a silent
 * fall-through to a live API is how a suite starts depending on a database
 * again without anyone noticing. Writes get a bare 200 — no spec asserts on a
 * write body yet, and inventing one would be a fixture nobody recorded.
 */
export async function mockApi(page: Page): Promise<string[]> {
  const missing: string[] = []

  // A pathname predicate rather than a `**/api/**` glob: that glob also matches
  // the dev server's own `/src/api/client.ts`, so the module the app is trying
  // to import gets answered with JSON and the page never boots.
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
      const request = route.request()
      const url = new URL(request.url())

      if (request.method() !== 'GET') {
        await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
        return
      }

      const name = fixtureFor(url)
      const body = name ? fixture(name) : null
      if (!body) {
        missing.push(`${request.method()} ${url.pathname}${url.search}`)
        await route.fulfill({
          status: 599,
          contentType: 'application/json',
          body: JSON.stringify({ detail: `no fixture for ${url.pathname}` }),
        })
        return
      }

      await route.fulfill({ status: 200, contentType: 'application/json', body })
    },
  )

  return missing
}

/**
 * Open a screen with the API mocked and the theme pinned.
 *
 * The theme goes into localStorage before any app script runs, which is the
 * same path a returning owner takes — and the only way to exercise the no-flash
 * script in index.html rather than route around it.
 */
export async function open(
  page: Page,
  path: string,
  testInfo: TestInfo,
): Promise<{ missing: string[] }> {
  const dark = testInfo.project.name.endsWith('dark')

  await page.addInitScript((theme) => {
    try {
      localStorage.setItem('aira-theme', theme)
    } catch {
      // Private mode. The default is light either way.
    }
  }, dark ? 'dark' : 'light')

  const missing = await mockApi(page)
  await page.goto(path, { waitUntil: 'domcontentloaded' })

  // Charts settle a frame or two after the data lands; without this the
  // screenshots catch half-drawn axes and axe scans a skeleton.
  await page.waitForLoadState('networkidle').catch(() => {})
  await page.waitForTimeout(350)

  return { missing }
}

export const isMobile = (testInfo: TestInfo): boolean =>
  testInfo.project.name.startsWith('mobile')
