import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

import { open, ROUTES } from './support/app'

/**
 * axe on every screen, in both themes, at both sizes.
 *
 * WCAG 2.2 AA only. The `best-practice` rules are deliberately left out: they
 * are opinions worth holding but not worth failing a build over, and mixing
 * them in is how a suite gets a long ignore-list and stops meaning anything.
 *
 * Nothing is excluded from the scan. If a violation shows up it is a real one
 * to fix, not a selector to add here.
 */
for (const route of ROUTES) {
  test(`${route.name} has no accessibility violations`, async ({ page }, testInfo) => {
    const { missing } = await open(page, route.path, testInfo)
    expect(missing, 'every request should be served from a fixture').toEqual([])

    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
      .analyze()

    // One line per violation, with the elements, so a failure is actionable
    // from the CI log without reopening the browser.
    const report = results.violations
      .map((violation) => {
        const where = violation.nodes
          .slice(0, 4)
          .map((node) => `      ${node.target.join(' ')}`)
          .join('\n')
        return `  [${violation.impact}] ${violation.id}: ${violation.help}\n${where}`
      })
      .join('\n')

    expect(report, `axe violations on ${route.name}`).toBe('')
  })
}
