/**
 * Light, Dark, or follow the machine.
 *
 * The default is **light**, not System. That is a deliberate departure from the
 * usual advice: this shop's owner asked for light, and before this existed the
 * app simply obeyed `prefers-color-scheme`, which is why every screenshot taken
 * on a dark laptop came out dark with no way to say otherwise.
 *
 * The resolved theme is written to `data-theme` on <html> rather than a class,
 * because `style.css` keys its dark palette off that attribute and because the
 * no-flash script in index.html has to set the same thing before Vue exists.
 *
 * `color-scheme` comes from the CSS, not from here — that is what makes native
 * checkboxes, date pickers and scrollbars match the page instead of staying
 * stubbornly light inside a dark one.
 */

import { computed, ref } from 'vue'

export type ThemeChoice = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

export const THEME_KEY = 'aira-theme'
const DEFAULT: ThemeChoice = 'light'

function isChoice(value: unknown): value is ThemeChoice {
  return value === 'light' || value === 'dark' || value === 'system'
}

/** Storage is allowed to be missing or to throw: Safari private mode does both. */
function readStored(): ThemeChoice {
  try {
    const raw = localStorage.getItem(THEME_KEY)
    return isChoice(raw) ? raw : DEFAULT
  } catch {
    return DEFAULT
  }
}

const prefersDark = (): boolean =>
  typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches

export function resolve(choice: ThemeChoice): ResolvedTheme {
  if (choice === 'system') return prefersDark() ? 'dark' : 'light'
  return choice
}

const choice = ref<ThemeChoice>(readStored())
const resolved = ref<ResolvedTheme>(resolve(choice.value))

/**
 * Swap the palette without animating the swap.
 *
 * Changing `data-theme` changes every token at once, and anything carrying a
 * color transition — buttons, tabs, nav items, cards — then cross-fades from
 * the old palette to the new one. Hundreds of elements smearing between light
 * and dark for 150ms reads as a glitch, not as polish. The switch should be
 * instant; the transitions exist for hover and press, not for this.
 *
 * The class is dropped on the next frame, after the new values have painted.
 */
function apply(): void {
  const root = document.documentElement
  const changed = root.dataset.theme !== resolve(choice.value)
  resolved.value = resolve(choice.value)

  if (!changed) {
    root.dataset.theme = resolved.value
    return
  }

  root.classList.add('theme-switching')
  root.dataset.theme = resolved.value
  requestAnimationFrame(() => {
    requestAnimationFrame(() => root.classList.remove('theme-switching'))
  })
}

export function setTheme(next: ThemeChoice): void {
  choice.value = next
  try {
    localStorage.setItem(THEME_KEY, next)
  } catch {
    // A preference we cannot remember is still a preference we can honor for
    // this visit.
  }
  apply()
}

/**
 * Call once at start-up. The attribute is already correct — index.html set it
 * before first paint — so this only wires up the listener that matters when the
 * choice is "system" and the machine flips at sunset.
 */
export function startTheme(): void {
  apply()
  window
    .matchMedia('(prefers-color-scheme: dark)')
    .addEventListener('change', () => {
      if (choice.value === 'system') apply()
    })
}

export function useTheme() {
  return {
    choice: computed(() => choice.value),
    resolved: computed(() => resolved.value),
    setTheme,
  }
}
