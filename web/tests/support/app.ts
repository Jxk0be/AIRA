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

export const TENANT = 'animanga_knox'

/**
 * Every screen, by the name the reports and screenshots use.
 *
 * `shell: false` marks a page that deliberately renders outside `AppShell` —
 * only the dev styleguide. The skip link, the `<main>` landmark and the
 * labeled `<nav>` are properties *of the shell*, so asserting them on a page
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

/** Every write the API declares `status_code=204`, by the path it answers on. */
const NO_CONTENT = [
  /\/charts\/[^/]+$/,
  /\/conversations\/[^/]+$/,
  /\/insights\/[^/]+\/feedback$/,
  /\/purchase-orders\/lines\/[^/]+$/,
  /\/staffing\/shifts\/[^/]+$/,
]

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
  if (path === '/me') return 'me'

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
 * again without anyone noticing. Writes get a bare 200, except the one whose
 * body a spec reads back; inventing the rest would be fixtures nobody
 * recorded.
 */
/**
 * Replacement bodies for named fixtures, keyed by fixture name.
 *
 * For the handful of specs that need a shop unlike the recorded one — two
 * registers rather than one, say. Everything not overridden still comes off disk,
 * so a spec states only what it is actually about.
 */
export type Overrides = Record<string, unknown>

export async function mockApi(page: Page, overrides: Overrides = {}): Promise<string[]> {
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
        // The one write with a body a spec asserts on: saving the shop's color
        // has to come back with the color, or the page cannot recolor without
        // a reload and `brand.spec.ts` would be testing the mock's shrug.
        if (url.pathname.endsWith('/appearance')) {
          const sent = JSON.parse(request.postData() ?? '{}') as { brand_color?: string | null }
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ tenant: TENANT, brand_color: sent.brand_color ?? null }),
          })
          return
        }
        // The writes the API answers with 204 answer with 204 here too. A mock
        // that is kinder than the API hides the bugs that only 204 causes:
        // `{}` is truthy, so every "did that work?" check passed under test
        // and failed in the browser.
        if (NO_CONTENT.some((pattern) => pattern.test(url.pathname))) {
          await route.fulfill({ status: 204 })
          return
        }
        await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
        return
      }

      const name = fixtureFor(url)
      const overridden = name && name in overrides ? JSON.stringify(overrides[name]) : null
      const body = overridden ?? (name ? fixture(name) : null)
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
 * Where `lib/supabase.ts` keeps the session. Named there rather than derived
 * from the project URL precisely so this file can write to it.
 */
const AUTH_STORAGE_KEY = 'aira-auth'

/**
 * A session that supabase-js will hand back without asking anybody.
 *
 * Every screen is behind a router guard now, so a suite with no session renders
 * the sign-in page thirteen times. Seeding one is better than mocking the auth
 * library: the app takes its normal path — stored session, `getSession`, guard,
 * `/me` — and only the auth server is absent.
 *
 * `expires_at` is far enough out that no refresh is attempted, which is what
 * keeps the suite off the network. The token is not a real JWT and never reaches
 * anything that would verify it: the API is replayed from `../fixtures`.
 */
function seededSession(): string {
  const oneDay = Math.floor(Date.now() / 1000) + 24 * 60 * 60
  return JSON.stringify({
    access_token: 'ui-suite-not-a-real-token',
    refresh_token: 'ui-suite-not-a-real-refresh-token',
    token_type: 'bearer',
    expires_in: 24 * 60 * 60,
    expires_at: oneDay,
    user: {
      id: '00000000-0000-4000-8000-000000000001',
      aud: 'authenticated',
      role: 'authenticated',
      email: 'owner@animangaknox.test',
      app_metadata: {},
      user_metadata: {},
      created_at: '2026-01-01T00:00:00.000Z',
    },
  })
}

/**
 * Open a screen with the API mocked, a session in place and the theme pinned.
 *
 * Both go into localStorage before any app script runs, which is the same path a
 * returning owner takes — and the only way to exercise the no-flash script in
 * index.html rather than route around it.
 */
export async function open(
  page: Page,
  path: string,
  testInfo: TestInfo,
  overrides: Overrides = {},
): Promise<{ missing: string[] }> {
  const dark = testInfo.project.name.endsWith('dark')

  await page.addInitScript(
    ({ theme, authKey, session }) => {
      try {
        localStorage.setItem('aira-theme', theme)
        localStorage.setItem(authKey, session)
      } catch {
        // Private mode. The default is light either way.
      }
    },
    { theme: dark ? 'dark' : 'light', authKey: AUTH_STORAGE_KEY, session: seededSession() },
  )

  const missing = await mockApi(page, overrides)
  await page.goto(path, { waitUntil: 'domcontentloaded' })

  // Charts settle a frame or two after the data lands; without this the
  // screenshots catch half-drawn axes and axe scans a skeleton.
  await page.waitForLoadState('networkidle').catch(() => {})
  await page.waitForTimeout(350)

  return { missing }
}

/**
 * Open a path with no session, to exercise the router's gate.
 *
 * The API is still mocked, and `missing` is still returned — a signed-out visit
 * that fetched anything from the API is itself the bug, because it means the
 * guard let a screen render before it redirected.
 */
export async function openSignedOut(
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
  await page.waitForLoadState('networkidle').catch(() => {})
  return { missing }
}

export const isMobile = (testInfo: TestInfo): boolean =>
  testInfo.project.name.startsWith('mobile')
