<script setup lang="ts">
/**
 * One thing on a screen, with room for the reason it is missing.
 *
 * The successor to `PanelCard`, and the unavailable state is still the point: a
 * shop whose system records no costs must not get an empty chart with a zero
 * axis, it gets the sentence its own data-quality report would give, where the
 * chart would have been.
 *
 * Two things changed.
 *
 * The caveat is no longer a yellow slab. On a phone it was routinely taller
 * than the number it qualified — four lines of warning under a single figure
 * (audit U11). The honesty is worth keeping and the volume was not, so it is a
 * quiet line with an icon, and it stays visible rather than folding away.
 *
 * `aria-busy` replaces a decorative skeleton nobody could hear: the skeleton is
 * hidden from assistive tech and the section announces that it is loading.
 */
import type { Caveat } from '../api/types'
import UiSkeleton from '../ui/UiSkeleton.vue'

withDefaults(
  defineProps<{
    title: string
    subtitle?: string
    available?: boolean
    reason?: string | null
    caveats?: Caveat[]
    loading?: boolean
    empty?: boolean
    emptyText?: string
  }>(),
  { available: true, caveats: () => [], emptyText: 'Nothing to show for this period.' },
)
</script>

<template>
  <section
    class="min-w-0 rounded-lg border border-border bg-surface"
    :aria-busy="loading ? 'true' : 'false'"
  >
    <header class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 px-4 pt-3.5 pb-2">
      <h2 class="text-base font-semibold text-ink">{{ title }}</h2>
      <p v-if="subtitle" class="text-sm text-ink-muted">{{ subtitle }}</p>
      <slot name="actions" />
    </header>

    <div class="px-4 pb-4">
      <UiSkeleton v-if="loading" :lines="3" />

      <p v-else-if="!available" class="text-base text-ink-muted">{{ reason }}</p>

      <p v-else-if="empty" class="text-base text-ink-muted">{{ emptyText }}</p>

      <slot v-else />
    </div>

    <footer
      v-if="available && !loading && caveats.length"
      class="flex gap-2 border-t border-border px-4 py-2.5"
    >
      <svg
        class="mt-0.5 h-4 w-4 flex-none text-warning"
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        stroke-width="1.8"
        aria-hidden="true"
      >
        <circle cx="10" cy="10" r="8" />
        <path d="M10 6.2v4.4M10 13.4h.01" stroke-linecap="round" />
      </svg>
      <div class="min-w-0">
        <p v-for="caveat in caveats" :key="caveat.code" class="text-sm leading-snug text-ink-muted">
          {{ caveat.message }}
        </p>
      </div>
    </footer>
  </section>
</template>
