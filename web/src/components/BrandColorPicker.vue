<script setup lang="ts">
/**
 * Settings › Appearance › Your color.
 *
 * The interesting part of this screen is not choosing a color, it is the two
 * previews underneath. Any color a shop picks has to work on a near-white page
 * and a near-black one, and no single color does — so we keep the hue and move
 * the lightness per theme (`lib/brand.ts` does the math). That is a
 * correction, and a correction made silently is the kind of thing an owner
 * notices a week later and concludes the app is broken.
 *
 * So both derived shades are drawn, side by side, in their own theme's
 * surfaces, with the measured contrast under each. Whatever the picker shows
 * is what the app will look like.
 *
 * There is no "Save" without a "Use the default": a setting you cannot undo in
 * one click is a setting people are afraid to try.
 */
import { computed, ref, watch } from 'vue'

import { PRESETS, evaluateBrand, previewSurfaces } from '../lib/brand'
import { useBusy } from '../lib/busy'
import { normalizeHex } from '../lib/color'
import { useTenantStore } from '../stores/tenant'
import UiButton from '../ui/UiButton.vue'
import { withToast } from '../ui/toast'

const shop = useTenantStore()

/** What is in the field. May be half-typed, may be nonsense — that is the point. */
const draft = ref(shop.brandColor ?? PRESETS[0]!.hex)
// Two buttons doing two different jobs. They shared one boolean, so saving a
// color spun "Back to the default" as well.
const busy = useBusy<'save' | 'reset'>()

// Someone switching shop in the dev picker should see that shop's color, not
// the last one they were looking at.
watch(
  () => shop.brandColor,
  (next) => {
    draft.value = next ?? PRESETS[0]!.hex
  },
)

const verdict = computed(() => evaluateBrand(draft.value))

/** The native swatch cannot hold "#3d4", so it gets the last valid value. */
const swatch = computed(() => normalizeHex(draft.value) ?? shop.brandColor ?? PRESETS[0]!.hex)

const example = PRESETS[0]!.hex
const saved = computed(() => shop.brandColor)
const changed = computed(() => (normalizeHex(draft.value) ?? '') !== (saved.value ?? ''))

const THEMES = [
  { key: 'light', label: 'Light mode' },
  { key: 'dark', label: 'Dark mode' },
] as const

/** Everything a preview card needs, with not one color named in the template. */
const previews = computed(() => {
  const v = verdict.value
  if (!v.ok) return []
  return THEMES.map(({ key, label }) => {
    const fit = v.fits[key]
    const s = previewSurfaces(key)
    return {
      key,
      label,
      ratio: fit.ratio,
      shade: fit.tokens.primary,
      page: { backgroundColor: s.bg, color: s.ink },
      card: { backgroundColor: s.surface, borderColor: s.raised },
      button: { backgroundColor: fit.tokens.primary, color: fit.tokens['primary-fg'] },
      link: { color: fit.tokens.primary },
      badge: { backgroundColor: fit.tokens['primary-subtle'], color: s.ink },
    }
  })
})

async function save() {
  const color = normalizeHex(draft.value)
  if (!color || !verdict.value.ok) return
  await busy.run('save', () =>
    withToast(() => shop.setBrandColor(color), {
      success: 'Saved. This is your color on every device you sign in on.',
      failure: 'Could not save that color',
    }),
  )
}

async function reset() {
  await busy.run('reset', () =>
    withToast(() => shop.setBrandColor(null), {
      success: 'Back to the color we ship',
      failure: 'Could not change that back',
    }),
  )
}
</script>

<template>
  <section class="rounded-md border border-border bg-surface p-5">
    <h2 id="brand-color-heading" class="text-lg font-semibold text-ink">Your color</h2>
    <p class="mt-1 text-ink-muted">
      Used for buttons, links and the focus ring. Saved against the shop, so it follows you to
      the phone behind the counter. Charts keep their own colors — those are chosen so the
      series stay apart for colorblind readers, and one of them turning into your color would
      undo that.
    </p>

    <div class="mt-4 flex flex-wrap items-end gap-3">
      <label class="block">
        <span class="mb-1 block text-sm font-medium text-ink">Pick</span>
        <input
          type="color"
          :value="swatch"
          class="h-11 w-16 cursor-pointer rounded-md border border-border-strong bg-surface p-1"
          @input="draft = ($event.target as HTMLInputElement).value"
        />
      </label>

      <label class="block">
        <span class="mb-1 block text-sm font-medium text-ink">Or type a hex value</span>
        <input
          v-model="draft"
          type="text"
          inputmode="text"
          spellcheck="false"
          autocomplete="off"
          :placeholder="example"
          aria-describedby="brand-color-verdict"
          class="tabular min-h-11 w-40 rounded-md border border-border-strong bg-surface px-3 text-base text-ink"
        />
      </label>
    </div>

    <fieldset class="mt-4">
      <legend class="mb-2 text-sm font-medium text-ink">Or start from one of these</legend>
      <div class="flex flex-wrap gap-2">
        <button
          v-for="preset in PRESETS"
          :key="preset.hex"
          type="button"
          class="min-h-11 rounded-md border px-3 text-sm font-medium text-ink transition-colors hover:bg-raised"
          :class="swatch === preset.hex ? 'border-border-strong bg-raised' : 'border-border'"
          :aria-pressed="swatch === preset.hex"
          @click="draft = preset.hex"
        >
          <span
            class="mr-2 inline-block h-3 w-3 rounded-full align-middle"
            :style="{ backgroundColor: preset.hex }"
            aria-hidden="true"
          />
          {{ preset.name }}
        </button>
      </div>
    </fieldset>

    <!-- Always present, and never only a color: the verdict is a sentence. A
         polite region rather than an assertive one, because this updates on
         every keystroke and interrupting someone mid-type is not help. -->
    <p
      id="brand-color-verdict"
      role="status"
      class="mt-4 flex items-start gap-2 text-sm"
      :class="verdict.ok ? 'text-ink-muted' : 'text-danger'"
    >
      <svg class="mt-0.5 h-4 w-4 flex-none" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="1.5" />
        <path
          v-if="verdict.ok"
          d="m5 8.2 2 2L11 6"
          stroke="currentColor"
          stroke-width="1.75"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
        <path v-else d="M8 4.5v4.2M8 11.2v.6" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" />
      </svg>
      <span>
        <template v-if="!verdict.ok">{{ verdict.reason }}</template>
        <template v-else-if="verdict.note">{{ verdict.note }}</template>
        <template v-else>
          Readable in both themes. Here is what it looks like.
        </template>
      </span>
    </p>

    <div v-if="previews.length" class="mt-4 grid gap-3 sm:grid-cols-2">
      <div
        v-for="preview in previews"
        :key="preview.key"
        class="overflow-hidden rounded-md border border-border"
      >
        <div class="p-4" :style="preview.page">
          <p class="text-sm font-semibold">{{ preview.label }}</p>
          <div class="mt-2 rounded-md border p-3" :style="preview.card">
            <span
              class="inline-flex min-h-11 items-center rounded-md px-4 text-base font-semibold"
              :style="preview.button"
            >
              Build last month
            </span>
            <p class="mt-2 text-sm">
              <span class="underline" :style="preview.link">A link</span>
              <span
                class="tabular ml-2 rounded-sm px-2 py-0.5 text-xs font-medium"
                :style="preview.badge"
              >
                12 to order
              </span>
            </p>
          </div>
        </div>
        <p class="tabular border-t border-border bg-raised px-3 py-2 text-xs text-ink-muted">
          {{ preview.shade }} · {{ preview.ratio.toFixed(1) }}:1 against the page
          <span class="sr-only">, which clears the 4.5 to 1 minimum</span>
        </p>
      </div>
    </div>

    <div class="mt-4 flex flex-wrap gap-2">
      <UiButton
        :disabled="!verdict.ok || !changed || busy.anyBusy.value"
        :loading="busy.busy('save')"
        @click="save"
      >
        Save this color
      </UiButton>
      <UiButton
        v-if="saved"
        variant="secondary"
        :disabled="busy.anyBusy.value"
        :loading="busy.busy('reset')"
        @click="reset"
      >
        Use the default
      </UiButton>
    </div>
  </section>
</template>
