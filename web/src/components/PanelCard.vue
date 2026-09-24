<script setup lang="ts">
/**
 * A single thing on a screen, with room for the reason it is missing.
 *
 * The unavailable state is the point of this component. A shop whose system
 * records no costs must not get an empty chart with a zero axis — it gets the
 * sentence its own data quality report would give, where the chart would have
 * been.
 */
import type { Caveat } from '../api/types'

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
  <section class="border border-rule bg-panel">
    <header class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-rule px-4 py-2.5">
      <h2 class="text-sm font-semibold tracking-tight text-ink">{{ title }}</h2>
      <p v-if="subtitle" class="text-xs text-ink-faint">{{ subtitle }}</p>
      <slot name="actions" />
    </header>

    <div class="px-4 py-3.5">
      <div v-if="loading" class="space-y-2" aria-hidden="true">
        <div class="h-2.5 w-2/5 bg-sunk working"></div>
        <div class="h-2.5 w-3/5 bg-sunk working"></div>
        <div class="h-2.5 w-1/3 bg-sunk working"></div>
      </div>

      <p v-else-if="!available" class="text-sm text-ink-muted">
        <span class="mr-1.5 align-middle text-ink-faint">—</span>{{ reason }}
      </p>

      <p v-else-if="empty" class="text-sm text-ink-faint">{{ emptyText }}</p>

      <slot v-else />
    </div>

    <footer v-if="available && !loading && caveats.length" class="border-t border-note-rule bg-note px-4 py-2">
      <p v-for="caveat in caveats" :key="caveat.code" class="text-xs leading-snug text-note-ink">
        {{ caveat.message }}
      </p>
    </footer>
  </section>
</template>
