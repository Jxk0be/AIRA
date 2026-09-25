import { expect, test } from '@playwright/test'

import { open, TENANT } from './support/app'

/**
 * The shop's own color, measured in a real browser.
 *
 * `check-contrast.ts` proves the palette we *ship* clears AA. It cannot prove
 * anything about a color a shop owner types in, because that color does not
 * exist until they type it — and the whole promise of the picker is that no
 * color they choose can make the interface unreadable.
 *
 * So the ratios below are computed from `getComputedStyle` on the elements the
 * browser actually painted, with luminance math written out again here rather
 * than imported. An app that measured its own contrast with the same function
 * that chose the color would agree with itself no matter what it did.
 *
 * This is also the first spec in the suite that opens a control and interacts
 * with it, rather than scanning a page as it loads (docs/ui/still-weak.md #2).
 */

const APPEARANCE = `/${TENANT}/settings?tab=appearance`

/** WCAG 2.2, from scratch, against whatever the browser says it painted. */
const RATIO = `(fg, bg) => {
  const channels = (css) => css.match(/[\\d.]+/g).slice(0, 3).map(Number)
  const lum = (css) => {
    const [r, g, b] = channels(css)
      .map((v) => v / 255)
      .map((v) => (v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
  }
  const [hi, lo] = [lum(fg), lum(bg)].sort((a, b) => b - a)
  return (hi + 0.05) / (lo + 0.05)
}`

test.describe('business color', () => {
  test('a color that is picked is the color that gets used, readably', async ({
    page,
  }, testInfo) => {
    await open(page, APPEARANCE, testInfo)

    // The indigo we ship measures fine on its own, so "the button is readable"
    // would pass whether or not the save did anything. Hold on to the fill we
    // started with and require it to have moved.
    const fill = () =>
      page.evaluate(
        () => getComputedStyle(document.querySelector('button.bg-primary')!).backgroundColor,
      )
    const before = await fill()

    // Rust, which is nothing like indigo in either theme.
    await page.getByRole('button', { name: 'Rust' }).click()
    await page.getByRole('button', { name: 'Save this color' }).click()

    // Polled on the painted fill rather than on `--primary`: the property is
    // written synchronously but the button is re-rendered by the same change,
    // so the pixel that matters arrives a tick after the variable does.
    await expect.poll(fill).not.toBe(before)

    const measured = await page.evaluate(
      ([ratioSource]) => {
        const ratio = eval(ratioSource!) as (fg: string, bg: string) => number
        const button = document.querySelector('button.bg-primary') as HTMLElement | null
        if (!button) return null
        const style = getComputedStyle(button)
        const page = getComputedStyle(document.body)
        return {
          label: ratio(style.color, style.backgroundColor),
          fill: ratio(style.backgroundColor, page.backgroundColor),
          hue: style.backgroundColor,
        }
      },
      [RATIO],
    )

    expect(measured).not.toBeNull()
    // The button's own label has to be readable on the fill...
    expect(measured!.label).toBeGreaterThanOrEqual(4.5)
    // ...and the fill has to be visible against the page it sits on.
    expect(measured!.fill).toBeGreaterThanOrEqual(3)
  })

  test('a color that cannot work is refused, with the reason', async ({ page }, testInfo) => {
    await open(page, APPEARANCE, testInfo)

    const field = page.getByLabel('Or type a hex value')
    await field.fill('#888888')

    const verdict = page.locator('#brand-color-verdict')
    await expect(verdict).toContainText('gray')
    // Refusing without saying why, or refusing by graying a button with no
    // sentence, is the failure this is here to catch.
    await expect(page.getByRole('button', { name: 'Save this color' })).toBeDisabled()
  })

  test('both themes are shown, so no adjustment is silent', async ({ page }, testInfo) => {
    await open(page, APPEARANCE, testInfo)
    await page.getByLabel('Or type a hex value').fill('#fde047')

    // A yellow this pale cannot be read on a white page, so light mode gets a
    // much darker shade of it. The owner has to be able to see that happen.
    for (const label of ['Light mode', 'Dark mode']) {
      await expect(page.getByText(label, { exact: true })).toBeVisible()
    }
    await expect(page.locator('#brand-color-verdict')).toContainText('lightness')
    await expect(page.getByText(/:1 against the page/).first()).toBeVisible()
  })

  test('the default is one click away', async ({ page }, testInfo) => {
    await open(page, APPEARANCE, testInfo)

    const shipped = await page.evaluate(
      () => getComputedStyle(document.querySelector('button.bg-primary')!).backgroundColor,
    )

    await page.getByRole('button', { name: 'Teal' }).click()
    await page.getByRole('button', { name: 'Save this color' }).click()
    await expect(page.getByRole('button', { name: 'Use the default' })).toBeVisible()

    await page.getByRole('button', { name: 'Use the default' }).click()
    await expect
      .poll(() =>
        page.evaluate(() => document.documentElement.style.getPropertyValue('--primary')),
      )
      .toBe('')
    // And the page is genuinely back on the shipped palette, not merely
    // missing an override.
    await expect
      .poll(() =>
        page.evaluate(
          () => getComputedStyle(document.querySelector('button.bg-primary')!).backgroundColor,
        ),
      )
      .toBe(shipped)
  })
})
