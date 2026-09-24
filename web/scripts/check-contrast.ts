/**
 * The contrast gate. Run by `python tasks.py ui-check`, which `tasks.py test`
 * calls, so a token that fails is a failing build rather than a note in a doc.
 *
 * It parses `src/style.css` rather than importing a duplicate of the palette:
 * the thing under test has to be the thing that ships, or the two drift and
 * the gate starts passing a palette nobody uses.
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
 *                                        between categorical colours forces a
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
 *                                        which is to say the same colour.
 */

import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS = resolve(HERE, '../src/style.css')

type Rgb = [number, number, number]
type Palette = Record<string, string>

const MIN_TEXT = 4.5
const MIN_UI = 3.0
const MIN_DELTA_E = 20
const MIN_DELTA_E_CVD = 11

// ---------------------------------------------------------------- colour maths

const srgbToLinear = (v: number): number =>
  v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
const linearToSrgb = (v: number): number =>
  v <= 0.0031308 ? 12.92 * v : 1.055 * Math.pow(Math.max(v, 0), 1 / 2.4) - 0.055

function parseHex(hex: string): Rgb {
  const raw = hex.replace('#', '')
  const full = raw.length === 3 ? raw.split('').map((c) => c + c).join('') : raw
  const pair = full.match(/../g)
  if (!pair || pair.length < 3) throw new Error(`not a colour: ${hex}`)
  return [
    parseInt(pair[0]!, 16) / 255,
    parseInt(pair[1]!, 16) / 255,
    parseInt(pair[2]!, 16) / 255,
  ]
}

const toHex = (rgb: Rgb): string =>
  '#' +
  rgb
    .map((v) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, '0'))
    .join('')

function luminance(hex: string): number {
  const [r, g, b] = parseHex(hex).map(srgbToLinear) as Rgb
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((m, n) => n - m) as [number, number]
  return (hi + 0.05) / (lo + 0.05)
}

function toLab(hex: string): [number, number, number] {
  const [r, g, b] = parseHex(hex).map(srgbToLinear) as Rgb
  const x = (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / 0.95047
  const y = r * 0.2126729 + g * 0.7151522 + b * 0.072175
  const z = (r * 0.0193339 + g * 0.119192 + b * 0.9503041) / 1.08883
  const f = (t: number) => (t > 216 / 24389 ? Math.cbrt(t) : ((24389 / 27) * t + 16) / 116)
  const [fx, fy, fz] = [f(x), f(y), f(z)]
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)]
}

/** CIEDE2000. ~1 is a just-noticeable difference; 20+ reads as another colour. */
export function deltaE(hexA: string, hexB: string): number {
  const [L1, a1, b1] = toLab(hexA)
  const [L2, a2, b2] = toLab(hexB)
  const RAD = Math.PI / 180
  const DEG = 180 / Math.PI
  const C1 = Math.hypot(a1, b1)
  const C2 = Math.hypot(a2, b2)
  const Cbar = (C1 + C2) / 2
  const G = 0.5 * (1 - Math.sqrt(Cbar ** 7 / (Cbar ** 7 + 25 ** 7)))
  const ap1 = (1 + G) * a1
  const ap2 = (1 + G) * a2
  const Cp1 = Math.hypot(ap1, b1)
  const Cp2 = Math.hypot(ap2, b2)
  const hue = (b: number, ap: number) => (b === 0 && ap === 0 ? 0 : (Math.atan2(b, ap) * DEG + 360) % 360)
  const hp1 = hue(b1, ap1)
  const hp2 = hue(b2, ap2)
  const dLp = L2 - L1
  const dCp = Cp2 - Cp1
  let dhp = 0
  if (Cp1 * Cp2 !== 0) {
    dhp = hp2 - hp1
    if (dhp > 180) dhp -= 360
    else if (dhp < -180) dhp += 360
  }
  const dHp = 2 * Math.sqrt(Cp1 * Cp2) * Math.sin((dhp * RAD) / 2)
  const Lbar = (L1 + L2) / 2
  const Cpbar = (Cp1 + Cp2) / 2
  let hbar = hp1 + hp2
  if (Cp1 * Cp2 !== 0) {
    if (Math.abs(hp1 - hp2) > 180) hbar += hbar < 360 ? 360 : -360
    hbar /= 2
  }
  const T =
    1 -
    0.17 * Math.cos((hbar - 30) * RAD) +
    0.24 * Math.cos(2 * hbar * RAD) +
    0.32 * Math.cos((3 * hbar + 6) * RAD) -
    0.2 * Math.cos((4 * hbar - 63) * RAD)
  const dTheta = 30 * Math.exp(-(((hbar - 275) / 25) ** 2))
  const Rc = 2 * Math.sqrt(Cpbar ** 7 / (Cpbar ** 7 + 25 ** 7))
  const Sl = 1 + (0.015 * (Lbar - 50) ** 2) / Math.sqrt(20 + (Lbar - 50) ** 2)
  const Sc = 1 + 0.045 * Cpbar
  const Sh = 1 + 0.015 * Cpbar * T
  const Rt = -Math.sin(2 * dTheta * RAD) * Rc
  return Math.sqrt(
    (dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh),
  )
}

/** Machado, Oliveira & Fernandes (2009), severity 1.0, applied in linear RGB. */
const CVD: Record<string, number[]> = {
  protanopia: [0.152286, 1.052583, -0.204868, 0.114503, 0.786281, 0.099216, -0.003882, -0.048116, 1.051998],
  deuteranopia: [0.367322, 0.860646, -0.227968, 0.280085, 0.672501, 0.047413, -0.01182, 0.04294, 0.968881],
  tritanopia: [1.255528, -0.076749, -0.178779, -0.078411, 0.930809, 0.147602, 0.004733, 0.691367, 0.3039],
}

export function simulate(hex: string, kind: keyof typeof CVD): string {
  const m = CVD[kind]!
  const [r, g, b] = parseHex(hex).map(srgbToLinear) as Rgb
  return toHex([
    linearToSrgb(m[0]! * r + m[1]! * g + m[2]! * b),
    linearToSrgb(m[3]! * r + m[4]! * g + m[5]! * b),
    linearToSrgb(m[6]! * r + m[7]! * g + m[8]! * b),
  ])
}

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

  console.log(
    `\n${rows.length} pairs checked across 2 themes — ` +
      (failed.length ? `${failed.length} FAILED` : 'all pass'),
  )
  if (failed.length) process.exitCode = 1
}

main()
