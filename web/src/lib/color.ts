/**
 * Color math. No Vue, no DOM — just numbers, so the build gate can import it.
 *
 * This file exists because there are now two places that have to agree about
 * whether a color is readable: `web/scripts/check-contrast.ts`, which fails the
 * build if a shipped token does not clear AA, and `lib/brand.ts`, which refuses
 * a color the shop's owner picked. Two implementations of the same ratio would
 * eventually disagree, and the one that disagreed quietly would be the one in
 * front of the customer.
 *
 * So the gate imports this. `node` runs `.ts` directly, and nothing here needs
 * a bundler.
 */

export type Rgb = [number, number, number]

export const srgbToLinear = (v: number): number =>
  v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)

export const linearToSrgb = (v: number): number =>
  v <= 0.0031308 ? 12.92 * v : 1.055 * Math.pow(Math.max(v, 0), 1 / 2.4) - 0.055

/** True for `#abc` and `#aabbcc`, false for everything else including names. */
export function isHex(value: string): boolean {
  return /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(value.trim())
}

export function parseHex(hex: string): Rgb {
  const raw = hex.trim().replace('#', '')
  const full = raw.length === 3 ? raw.split('').map((c) => c + c).join('') : raw
  const pair = full.match(/../g)
  if (!pair || pair.length < 3) throw new Error(`not a color: ${hex}`)
  return [
    parseInt(pair[0]!, 16) / 255,
    parseInt(pair[1]!, 16) / 255,
    parseInt(pair[2]!, 16) / 255,
  ]
}

export const toHex = (rgb: Rgb): string =>
  '#' +
  rgb
    .map((v) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, '0'))
    .join('')

/** Normalize user input to `#rrggbb`, or null if it is not a color at all. */
export function normalizeHex(value: string): string | null {
  const text = value.trim()
  const withHash = text.startsWith('#') ? text : `#${text}`
  if (!isHex(withHash)) return null
  return toHex(parseHex(withHash))
}

export function luminance(hex: string): number {
  const [r, g, b] = parseHex(hex).map(srgbToLinear) as Rgb
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** WCAG 2.2 contrast ratio, 1 to 21. Order of the arguments does not matter. */
export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((m, n) => n - m) as [number, number]
  return (hi + 0.05) / (lo + 0.05)
}

/**
 * Composite `top` over `bottom` at `alpha`, in sRGB.
 *
 * Deliberately not in linear light. This mirrors what the browser does when it
 * paints a translucent fill, and the point of the mix is to predict the pixel
 * the user will actually see well enough to measure contrast against it.
 */
export function mix(top: string, bottom: string, alpha: number): string {
  const a = parseHex(top)
  const b = parseHex(bottom)
  return toHex([
    a[0] * alpha + b[0] * (1 - alpha),
    a[1] * alpha + b[1] * (1 - alpha),
    a[2] * alpha + b[2] * (1 - alpha),
  ])
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

/** CIEDE2000. ~1 is a just-noticeable difference; 20+ reads as another color. */
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
  const hue = (b: number, ap: number) =>
    b === 0 && ap === 0 ? 0 : (Math.atan2(b, ap) * DEG + 360) % 360
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
export const CVD: Record<string, number[]> = {
  protanopia: [
    0.152286, 1.052583, -0.204868, 0.114503, 0.786281, 0.099216, -0.003882, -0.048116, 1.051998,
  ],
  deuteranopia: [
    0.367322, 0.860646, -0.227968, 0.280085, 0.672501, 0.047413, -0.01182, 0.04294, 0.968881,
  ],
  tritanopia: [
    1.255528, -0.076749, -0.178779, -0.078411, 0.930809, 0.147602, 0.004733, 0.691367, 0.3039,
  ],
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

// ------------------------------------------------------------------- OKLCH
//
// Needed because "the same color, lighter" is not a question sRGB can answer.
// A shop picks one color; light and dark need two shades of it, and nudging
// the channels in sRGB slides the hue as it goes — a navy lightened that way
// arrives somewhere near purple. OKLab is perceptually uniform enough that
// moving L alone keeps the color recognizable.

/** Björn Ottosson's OKLab, from linear sRGB. */
export function toOklab(hex: string): [number, number, number] {
  const [r, g, b] = parseHex(hex).map(srgbToLinear) as Rgb
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ]
}

function oklabToRgb(L: number, a: number, b: number): Rgb {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3
  return [
    linearToSrgb(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    linearToSrgb(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    linearToSrgb(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
  ]
}

const inGamut = (rgb: Rgb): boolean => rgb.every((v) => v >= -0.001 && v <= 1.001)

export interface Oklch {
  /** 0 (black) to 1 (white). */
  l: number
  /** 0 is gray. Real colors land between about 0.05 and 0.37. */
  c: number
  /** Degrees. */
  h: number
}

export function toOklch(hex: string): Oklch {
  const [L, a, b] = toOklab(hex)
  return { l: L, c: Math.hypot(a, b), h: (Math.atan2(b, a) * 180) / Math.PI }
}

/**
 * Back to a hex the browser can paint, reducing chroma until it fits in sRGB.
 *
 * Clamping the channels instead would be shorter and wrong: it fits by moving
 * the hue, which is the one thing this whole detour exists to preserve.
 */
export function fromOklch({ l, c, h }: Oklch): string {
  const rad = (h * Math.PI) / 180
  const at = (chroma: number) => oklabToRgb(l, Math.cos(rad) * chroma, Math.sin(rad) * chroma)

  if (inGamut(at(c))) return toHex(at(c))

  let lo = 0
  let hi = c
  for (let i = 0; i < 24; i++) {
    const mid = (lo + hi) / 2
    if (inGamut(at(mid))) lo = mid
    else hi = mid
  }
  return toHex(at(lo))
}
