/**
 * The shop's own color, made safe to use.
 *
 * A shop owner picks one color. The interface needs two shades of it, because
 * one color cannot clear 4.5:1 against a near-white page *and* a near-black
 * one — that is why the palette that ships has `#3d46b8` in light and `#949bf5`
 * in dark, the same blue at two lightnesses. So we keep the hue and chroma the
 * owner chose and move lightness, per theme, only as far as it takes to clear
 * AA on the surfaces the color actually lands on.
 *
 * Three things follow from that, and all three are deliberate.
 *
 * **We show the adjustment, we do not hide it.** Settings prints both derived
 * shades next to the pick with the measured ratios, so an owner who chose a
 * pale mint and sees a deep green in light mode knows why. Silently correcting
 * a color and saying nothing is how you get an owner who thinks the picker is
 * broken.
 *
 * **We still refuse some colors.** A near-gray has no hue to preserve: lighten
 * or darken it and you get gray, which reads as disabled rather than as the
 * shop. That is refused with the reason, not quietly accepted.
 *
 * **The charts do not follow.** The six series colors were found by a search
 * that kept every pair 20 CIEDE2000 apart, and 11 apart under each of three
 * dichromacies. Dropping the shop's color into series 1 would break a promise
 * the build gate makes, and nothing about a brand color says the first category
 * in a pie chart should be it.
 */

import {
  type Oklch,
  contrast,
  deltaE,
  fromOklch,
  mix,
  normalizeHex,
  toOklch,
} from './color.ts'
import type { ResolvedTheme } from './theme.ts'

const MIN_TEXT = 4.5
/**
 * What we aim for when deriving a shade. The tenth above 4.5 is slack: the
 * nearest passing shade otherwise lands on 4.50-something, and a token sitting
 * exactly on the line is one rounding away from being under it.
 */
const TARGET_TEXT = 4.6
/** Below this there is no hue left to preserve — it is gray with a tint. */
const MIN_CHROMA = 0.04
/**
 * CIEDE2000 between the pick and a derived shade, past which "a shade of your
 * color" stops being a fair description of what the owner will see.
 */
const DRIFT_WORTH_SAYING = 25

/** The tokens a brand color replaces. Everything else stays as shipped. */
export interface BrandTokens {
  primary: string
  'primary-fg': string
  'primary-subtle': string
  focus: string
}

interface Surfaces {
  bg: string
  surface: string
  raised: string
  ink: string
}

/**
 * The surfaces of each theme, read out of the stylesheet that is actually
 * loaded.
 *
 * Same reasoning as the build gate parsing `style.css`: a second copy of the
 * palette in TypeScript would drift from the one that ships, and the copy that
 * drifted quietly would be the one deciding whether an owner's color is
 * readable. `FALLBACK` exists for the case where the sheet is not readable
 * (it never is in a unit test with no document), and `check-contrast.ts`
 * asserts it still matches `style.css` on every build, so it cannot rot.
 */
export const FALLBACK: Record<ResolvedTheme, Surfaces> = {
  light: { bg: '#fbfbf9', surface: '#ffffff', raised: '#f4f4f1', ink: '#1a1a17' },
  dark: { bg: '#15161a', surface: '#1c1e23', raised: '#24272d', ink: '#ecedf0' },
}

const SELECTOR: Record<ResolvedTheme, string> = {
  light: ':root',
  dark: ':root[data-theme="dark"]',
}

let cached: Record<ResolvedTheme, Surfaces> | null = null

function surfaces(): Record<ResolvedTheme, Surfaces> {
  if (cached) return cached
  const found: Record<string, Partial<Surfaces>> = { light: {}, dark: {} }

  try {
    for (const sheet of Array.from(document.styleSheets)) {
      // A sheet from another origin throws on `.cssRules`. Ours never is, but
      // a browser extension's might be, and one throwing sheet should not
      // cost us the palette.
      let rules: CSSRule[]
      try {
        rules = Array.from(sheet.cssRules)
      } catch {
        continue
      }
      for (const rule of rules) {
        if (!(rule instanceof CSSStyleRule)) continue
        for (const theme of ['light', 'dark'] as const) {
          if (rule.selectorText !== SELECTOR[theme]) continue
          for (const key of ['bg', 'surface', 'raised', 'ink'] as const) {
            const value = rule.style.getPropertyValue(`--${key}`).trim()
            if (value) found[theme]![key] = value
          }
        }
      }
    }
  } catch {
    // No document, or no readable sheets. The fallback is checked at build
    // time, so this is a degraded path rather than a wrong one.
  }

  cached = {
    light: { ...FALLBACK.light, ...found.light },
    dark: { ...FALLBACK.dark, ...found.dark },
  }
  return cached
}

/**
 * The four surfaces of one theme, for a preview that has to draw the theme you
 * are *not* currently in. Copied out so a component never names a color itself.
 */
export function previewSurfaces(theme: ResolvedTheme): Readonly<Surfaces> {
  return surfaces()[theme]
}

/**
 * Somewhere to start, for an owner who has a color in mind but not a hex.
 *
 * Every one of them is a real color at a lightness that survives the gate in
 * both themes with little drift, so the first thing a new owner clicks does
 * something obvious rather than lecturing them about contrast.
 */
export const PRESETS: { name: string; hex: string }[] = [
  { name: 'Indigo', hex: '#3d46b8' },
  { name: 'Teal', hex: '#0f766e' },
  { name: 'Forest', hex: '#15803d' },
  { name: 'Rust', hex: '#c2410c' },
  { name: 'Crimson', hex: '#be123c' },
  { name: 'Plum', hex: '#7e22ce' },
]

// --------------------------------------------------------------- deriving

/**
 * Walk lightness until the color clears `MIN_TEXT` on the worst surface.
 *
 * Which way to walk is decided by the page, not by the color: on a light theme
 * the only way to gain contrast is to get darker, and on a dark theme, lighter.
 * The step is small enough (0.005 in OKLab L, about 200 tries end to end) that
 * the result is the *nearest* usable shade rather than a safe-looking one some
 * distance past it.
 */
function darkenOrLightenToPass(start: Oklch, against: string[], down: boolean): string | null {
  for (let i = 0; i <= 200; i++) {
    const l = down ? start.l - i * 0.005 : start.l + i * 0.005
    if (l < 0 || l > 1) break
    const hex = fromOklch({ ...start, l })
    if (against.every((bg) => contrast(hex, bg) >= TARGET_TEXT)) return hex
  }
  return null
}

/**
 * The button label. Black or white, whichever is readable on the fill.
 *
 * Not derived from the brand color: a tinted label on a tinted button is how
 * you get 4.6:1 in the mockup and an unreadable button on a phone in daylight.
 */
function foregroundFor(fill: string): string {
  const white = contrast('#ffffff', fill)
  const black = contrast('#141414', fill)
  return white >= black ? '#ffffff' : '#141414'
}

/**
 * The tinted background behind badges and selected rows.
 *
 * Derived by compositing the brand over the surface, then backing the mix off
 * until body text is still readable on it. The tint is decoration, so it gives
 * way rather than costing the owner their color — at alpha 0 it is the surface,
 * which already passes.
 */
function subtleFor(fill: string, s: Surfaces, start: number): string {
  for (let alpha = start; alpha > 0; alpha -= 0.02) {
    const tint = mix(fill, s.surface, alpha)
    if (contrast(s.ink, tint) >= MIN_TEXT && contrast(fill, tint) >= 3) return tint
  }
  return s.surface
}

export interface ThemeFit {
  theme: ResolvedTheme
  tokens: BrandTokens
  /** The ratio the derived shade gets against the worst of the three surfaces. */
  ratio: number
  /** How far the derived shade sits from what was picked. Under ~10 is close. */
  drift: number
}

export type BrandVerdict =
  | { ok: true; color: string; fits: Record<ResolvedTheme, ThemeFit>; note: string | null }
  | { ok: false; reason: string }

/**
 * Can this shop use this color, and what will it look like?
 *
 * Pure: no DOM writes, no storage. Settings calls it on every keystroke to show
 * a live verdict, and `applyBrand` calls it again before touching anything, so
 * a color that got into the database past the picker still cannot land on the
 * page.
 */
export function evaluateBrand(input: string): BrandVerdict {
  const color = normalizeHex(input)
  if (!color) {
    return { ok: false, reason: 'That is not a color. Use a hex value like #3d46b8.' }
  }

  const picked = toOklch(color)
  if (picked.c < MIN_CHROMA) {
    return {
      ok: false,
      reason:
        'There is no color in that — it is a shade of gray. Lightening or ' +
        'darkening it to make it readable would only give a different gray, and ' +
        'the interface would read it as switched off rather than as your shop.',
    }
  }

  const s = surfaces()
  const fits: Partial<Record<ResolvedTheme, ThemeFit>> = {}

  for (const theme of ['light', 'dark'] as const) {
    const against = [s[theme].bg, s[theme].surface, s[theme].raised]
    const shade = darkenOrLightenToPass(picked, against, theme === 'light')
    if (!shade) {
      return {
        ok: false,
        reason:
          `There is no shade of that color that can be read on the ${theme} ` +
          'theme. Try one with more depth to it.',
      }
    }
    const fg = foregroundFor(shade)
    if (contrast(fg, shade) < MIN_TEXT) {
      return {
        ok: false,
        reason: `A button filled with that color has no readable label in the ${theme} theme.`,
      }
    }
    fits[theme] = {
      theme,
      tokens: {
        primary: shade,
        'primary-fg': fg,
        'primary-subtle': subtleFor(shade, s[theme], theme === 'light' ? 0.14 : 0.26),
        focus: shade,
      },
      ratio: Math.min(...against.map((bg) => contrast(shade, bg))),
      drift: deltaE(color, shade),
    }
  }

  // Both shades are shown either way — that is the "no silent correction"
  // promise. The note is for the case where one of them has moved far enough
  // that seeing it without an explanation would look like a bug.
  const far = (['light', 'dark'] as const).filter(
    (theme) => fits[theme]!.drift >= DRIFT_WORTH_SAYING,
  )

  return {
    ok: true,
    color,
    fits: fits as Record<ResolvedTheme, ThemeFit>,
    note: far.length
      ? `Your color is used at a different lightness in ${far.join(' and ')} ` +
        `mode, because at the one you picked it cannot be read there. The hue is ` +
        `kept; only the lightness moves.`
      : null,
  }
}

// --------------------------------------------------------------- applying

const VARS = ['primary', 'primary-fg', 'primary-subtle', 'focus'] as const

/**
 * Put the shop's color on the page, or take it off again.
 *
 * Written as inline custom properties on `<html>`, which beats any selector in
 * `style.css` without the file needing to know this feature exists. Called
 * again on every theme change, because the two themes get different shades.
 *
 * A color that fails the gate is not applied and not half-applied: the shipped
 * palette is restored instead. That is the backstop for anything written
 * straight to the API — the endpoint validates the hex and nothing more, and
 * this is where a readable interface is actually guaranteed.
 */
export function applyBrand(color: string | null, theme: ResolvedTheme): void {
  const root = document.documentElement
  const clear = () => VARS.forEach((name) => root.style.removeProperty(`--${name}`))

  if (!color) return clear()
  const verdict = evaluateBrand(color)
  if (!verdict.ok) return clear()

  const { tokens } = verdict.fits[theme]
  for (const name of VARS) root.style.setProperty(`--${name}`, tokens[name])
}
