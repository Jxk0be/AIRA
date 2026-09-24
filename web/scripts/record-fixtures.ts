/**
 * Record one real API response per screen into `tests/fixtures/`.
 *
 * The UI tests replay these rather than talking to a live API, which is a
 * deliberate trade. Running them for real would mean standing up Supabase and
 * FastAPI and a seeded tenant before a single pixel could be checked — so the
 * checks would be skipped locally and flake in CI, and a red run would more
 * often mean "the database was not ready" than "the page is wrong".
 *
 * These tests are about layout, contrast, focus order and axe. Fixtures make
 * them deterministic and fast, and let them run on a machine with no database
 * at all.
 *
 * The cost is that a fixture can go stale against a changed API. That is why
 * this is a script you re-run — `python tasks.py ui-fixtures` — rather than a
 * pile of hand-written JSON nobody dares touch. Re-record whenever a response
 * model changes; the diff is the review.
 *
 *     python tasks.py api          # in one terminal
 *     python tasks.py ui-fixtures  # in another
 */

import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const OUT = resolve(HERE, '../tests/fixtures')
const API = process.env.VITE_API_BASE ?? 'http://localhost:8000'
const TENANT = process.env.FIXTURE_TENANT ?? 'tsundoku'

/**
 * Every GET the app makes on first paint of each screen, keyed by the file it
 * lands in. The key doubles as the name the test helper matches on.
 */
const ENDPOINTS: Record<string, string> = {
  'tenants': '/tenants',
  'profile': `/tenants/${TENANT}/profile`,
  'dashboard': `/tenants/${TENANT}/dashboard?days=30&weeks=52`,
  'charts': `/tenants/${TENANT}/charts`,
  'insights': `/tenants/${TENANT}/insights?status=open`,
  'insights-top': `/tenants/${TENANT}/insights?status=open&limit=3`,
  'value': `/tenants/${TENANT}/value`,
  'inventory': `/tenants/${TENANT}/inventory?limit=50&offset=0&sort=name`,
  'reorder': `/tenants/${TENANT}/reorder`,
  'purchase-orders': `/tenants/${TENANT}/purchase-orders`,
  'dead-stock': `/tenants/${TENANT}/dead-stock`,
  'dead-stock-actions': `/tenants/${TENANT}/dead-stock/actions`,
  'staffing': `/tenants/${TENANT}/staffing`,
  'month-end': `/tenants/${TENANT}/month-end`,
  'digest-preview': `/tenants/${TENANT}/digest/preview`,
  'notifications': `/tenants/${TENANT}/notifications`,
  'notification-messages': `/tenants/${TENANT}/notifications/messages`,
  'data': `/tenants/${TENANT}/data`,
  'jobs': `/tenants/${TENANT}/jobs`,
  'assistant': `/tenants/${TENANT}/assistant`,
  'conversations': `/tenants/${TENANT}/conversations`,
}

async function main(): Promise<void> {
  mkdirSync(OUT, { recursive: true })

  let failures = 0
  for (const [name, path] of Object.entries(ENDPOINTS)) {
    try {
      const response = await fetch(`${API}${path}`, { headers: { Accept: 'application/json' } })
      if (!response.ok) {
        console.log(`  FAIL ${name.padEnd(24)} ${response.status} ${path}`)
        failures++
        continue
      }
      const body = await response.json()
      writeFileSync(resolve(OUT, `${name}.json`), JSON.stringify(body, null, 2) + '\n')
      console.log(`  ok   ${name.padEnd(24)} ${path}`)
    } catch (cause) {
      console.log(`  FAIL ${name.padEnd(24)} ${String(cause)}`)
      failures++
    }
  }

  console.log(
    `\n${Object.keys(ENDPOINTS).length - failures}/${Object.keys(ENDPOINTS).length} recorded into tests/fixtures`,
  )
  if (failures) {
    console.log(`\nIs the API running? \`python tasks.py api\``)
    process.exitCode = 1
  }
}

await main()
