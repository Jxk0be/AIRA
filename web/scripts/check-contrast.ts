/**
 * The contrast gate. Run by `python tasks.py ui-check`, which `tasks.py test`
 * calls, so a token that fails is a failing build rather than a note in a doc.
 *
 * It parses `src/style.css` rather than importing a duplicate of the palette:
 * the thing under test has to be the thing that ships, or the two drift and
 * the gate starts passing a palette nobody uses. For the same reason the ratio
 * math comes from `src/lib/color.ts` — the app imports the same file to decide
 * whether a shop's chosen color is readable, and a gate that measured
 * differently from the picker would be worse than no gate.
 *
 * Three families of check, because they are three different questions.
 *
 *   1. Can you read the text?            WCAG 2.2 luminance contrast, >= 4.5:1
 *                                        for body text, >= 3:1 for large text,
 *                                        UI components, input borders and the
 *                                        focus ring.
 *
 *   2. Can you see the chart at all?     Each series >= 3:1 against the surface
 *                                        it is drawn on.
 *
 *   3. Can you tell the series apart?    NOT luminance contrast. Requiring that
 *                                        between categorical colors forces a
 *                                        light-to-dark ramp, which makes some
 *                                        categories look more important than
 *                                        others — that is a sequential palette,
 *                                        and a category list is not ordered.
 *                                        The right measure is perceptual
 *                                        distance: CIEDE2000 >= 20, and >= 11
 *                                        after simulating each of the three
 *                                        dichromacies. Six hues picked by eye
 *                                        passed (1) and (2) and still put two
 *                                        series 1.4 apart for a deuteranope,
 *                                        which is to say the same color.
 */

import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { FALLBACK } from '../src/lib/brand.ts'
import { CVD, contrast, deltaE, simulate } from '../src/lib/color.ts'

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS = resolve(HERE, '../src/style.css')

type Palette = Record<string, string>

const MIN_TEXT = 4.5
const MIN_UI = 3.0
const MIN_DELTA_E = 20
const MIN_DELTA_E_CVD = 11

// ------------------------------------------------------------------ the tokens

/**
 * Pull one palette out of style.css by the selector that opens its block.
 * Dark inherits anything it does not restate, which is how the file is written.
 */
function readPalette(css: string, selector: string, base: Palette = {}): Palette {
  const start = css.indexOf(selector + ' {')
  if (start === -1) throw new Error(`no ${selector} block in style.css`)
  const open = css.indexOf('{', start)
  const close = css.indexOf('\n}', open)
  const body = css.slice(open, close)
  const found: Palette = { ...base }
  for (const [, name, value] of body.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{3,8})\s*;/g)) {
    found[name!] = value!
  }
  return found
}

/** Every pair we promise. `min` is the ratio the pair has to clear. */
const TEXT_PAIRS: [string, string][] = [
  ['ink', 'bg'], ['ink', 'surface'], ['ink', 'raised'],
  ['ink-muted', 'bg'], ['ink-muted', 'surface'], ['ink-muted', 'raised'],
  ['primary', 'bg'], ['primary', 'surface'], ['primary', 'primary-subtle'],
  ['primary-fg', 'primary'],
  ['success', 'surface'], ['success-fg', 'success'], ['ink', 'success-subtle'],
  ['warning', 'surface'], ['warning-fg', 'warning'], ['ink', 'warning-subtle'],
  ['danger', 'surface'], ['danger-fg', 'danger'], ['ink', 'danger-subtle'],
  ['info', 'surface'], ['info-fg', 'info'], ['ink', 'info-subtle'],
]

/** Borders of inputs, icons and the focus ring are UI, not text: 3:1. */
const UI_PAIRS: [string, string][] = [
  ['border-strong', 'bg'], ['border-strong', 'surface'], ['border-strong', 'raised'],
  ['focus', 'bg'], ['focus', 'surface'],
]

const CHART_KEYS = ['chart-1', 'chart-2', 'chart-3', 'chart-4', 'chart-5', 'chart-6']

interface Row { theme: string; kind: string; pair: string; got: number; min: number; ok: boolean }

function audit(theme: string, p: Palette): Row[] {
  const rows: Row[] = []
  const add = (kind: string, pair: string, got: number, min: number) =>
    rows.push({ theme, kind, pair, got, min, ok: got >= min - 1e-9 })

  for (const [fg, bg] of TEXT_PAIRS) add('text', `${fg} on ${bg}`, contrast(p[fg]!, p[bg]!), MIN_TEXT)
  for (const [fg, bg] of UI_PAIRS) add('ui', `${fg} on ${bg}`, contrast(p[fg]!, p[bg]!), MIN_UI)

  for (const key of CHART_KEYS) add('chart', `${key} on surface`, contrast(p[key]!, p.surface!), MIN_UI)

  for (let i = 0; i < CHART_KEYS.length; i++) {
    for (let j = i + 1; j < CHART_KEYS.length; j++) {
      const a = p[CHART_KEYS[i]!]!
      const b = p[CHART_KEYS[j]!]!
      add('distinct', `${CHART_KEYS[i]} ~ ${CHART_KEYS[j]}`, deltaE(a, b), MIN_DELTA_E)
      for (const kind of Object.keys(CVD)) {
        add(
          kind.slice(0, 6),
          `${CHART_KEYS[i]} ~ ${CHART_KEYS[j]}`,
          deltaE(simulate(a, kind), simulate(b, kind)),
          MIN_DELTA_E_CVD,
        )
      }
    }
  }
  return rows
}

// ------------------------------------------------------------------- the report

/**
 * `brand.ts` keeps a hardcoded copy of the four surfaces for the case where it
 * cannot read the loaded stylesheet. That copy is what decides whether a shop
 * owner's color is readable, so it is checked against the real file here rather
 * than trusted to stay right.
 */
function checkFallback(light: Palette, dark: Palette): string[] {
  const wrong: string[] = []
  for (const [theme, palette] of [['light', light], ['dark', dark]] as const) {
    for (const [key, value] of Object.entries(FALLBACK[theme])) {
      if (palette[key] !== value) {
        wrong.push(`brand.ts FALLBACK.${theme}.${key} is ${value}, style.css says ${palette[key]}`)
      }
    }
  }
  return wrong
}

function main(): void {
  const css = readFileSync(CSS, 'utf8')
  const light = readPalette(css, ':root')
  const dark = readPalette(css, ':root[data-theme="dark"]', light)

  const rows = [...audit('light', light), ...audit('dark', dark)]
  const failed = rows.filter((r) => !r.ok)
  const verbose = process.argv.includes('--all')

  const groups = new Map<string, Row[]>()
  for (const r of rows) {
    const key = `${r.theme}/${r.kind}`
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(r)
  }

  for (const [key, group] of groups) {
    const bad = group.filter((r) => !r.ok)
    if (!verbose && bad.length === 0) {
      console.log(`  ok   ${key.padEnd(20)} ${String(group.length).padStart(3)} pairs`)
      continue
    }
    console.log(`\n${key}`)
    for (const r of verbose ? group : bad) {
      console.log(
        `  ${r.ok ? 'ok  ' : 'FAIL'} ${r.pair.padEnd(28)} ${r.got.toFixed(2).padStart(7)}  (min ${r.min})`,
      )
    }
  }

  const stale = checkFallback(light, dark)
  for (const line of stale) console.log(`\n  FAIL ${line}`)

  console.log(
    `\n${rows.length} pairs checked across 2 themes — ` +
      (failed.length ? `${failed.length} FAILED` : 'all pass'),
  )
  if (failed.length || stale.length) process.exitCode = 1
}

main()
