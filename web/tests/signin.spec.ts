import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

import { openSignedOut } from './support/app'

/**
 * The gate, and the one screen behind none of it.
 *
 * Everything else in this suite runs with a seeded session, which is right —
 * those specs are about the screens. These are about what happens without one,
 * which is now the first thing every visitor meets and so wants the same axe and
 * layout treatment as the rest.
 *
 * The password field is deliberately not typed into and submitted here: there is
 * no auth server in this suite, so a real sign-in has nothing to answer it. What
 * is checked is everything up to that point — the redirect, the labels, the
 * keyboard path, and that the three modes are reachable without a mouse.
 */

test('a signed-out visitor is sent to sign in, with nothing leaked on the way', async ({
  page,
}, testInfo) => {
  const { missing } = await openSignedOut(page, '/animanga_knox/stock?tab=reorder', testInfo)

  await expect(page).toHaveURL(/\/sign-in/)
  // The screen they wanted is remembered, so signing in does not dump them on
  // the home page having lost what they clicked.
  expect(page.url()).toContain('next=')
  expect(page.url()).toContain('stock')

  // No shop data was fetched before the redirect. A guard that renders the
  // screen first and redirects afterwards has already sent the request.
  expect(missing, 'nothing should have been requested from the API').toEqual([])

  await expect(page.getByRole('heading', { level: 2, name: 'Sign in' })).toBeVisible()
})

test('sign-in has no accessibility violations', async ({ page }, testInfo) => {
  await openSignedOut(page, '/sign-in', testInfo)

  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()

  const report = results.violations
    .map((violation) => {
      const where = violation.nodes
        .slice(0, 4)
        .map((node) => `      ${node.target.join(' ')}`)
        .join('\n')
      return `  [${violation.impact}] ${violation.id}: ${violation.help}\n${where}`
    })
    .join('\n')

  expect(report, 'axe violations on sign-in').toBe('')
})

test('sign-in does not scroll horizontally', async ({ page }, testInfo) => {
  await openSignedOut(page, '/sign-in', testInfo)
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  expect(overflow, 'the page is wider than the viewport').toBeLessThanOrEqual(1)
})

test('both fields are reachable and labelled', async ({ page }, testInfo) => {
  await openSignedOut(page, '/sign-in', testInfo)

  // By label, not by selector: a field a screen reader cannot name is a field
  // this should fail on.
  const email = page.getByLabel('Email')
  const password = page.getByLabel('Password')
  await expect(email).toBeVisible()
  await expect(password).toBeVisible()

  await email.fill('owner@animangaknox.test')
  await email.press('Tab')
  await expect(password).toBeFocused()

  // Autocomplete is what lets a password manager fill this, which is the only
  // way we want a password entered.
  await expect(email).toHaveAttribute('autocomplete', 'email')
  await expect(password).toHaveAttribute('autocomplete', 'current-password')
})

test('the three modes are reachable, and sign-up asks for a longer password', async ({
  page,
}, testInfo) => {
  await openSignedOut(page, '/sign-in', testInfo)

  await page.getByRole('button', { name: 'Create an account' }).click()
  await expect(page.getByRole('heading', { level: 2, name: 'Create an account' })).toBeVisible()
  await expect(page.getByLabel('Password')).toHaveAttribute('minlength', '8')
  await expect(page.getByLabel('Password')).toHaveAttribute('autocomplete', 'new-password')

  await page.getByRole('button', { name: 'Sign in instead' }).click()
  await expect(page.getByRole('heading', { level: 2, name: 'Sign in' })).toBeVisible()

  await page.getByRole('button', { name: 'Forgotten your password?' }).click()
  await expect(page.getByRole('heading', { level: 2, name: 'Reset your password' })).toBeVisible()
  // Nothing to type a password into when you have forgotten it.
  await expect(page.getByLabel('Password')).toHaveCount(0)
})
