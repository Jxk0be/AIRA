<script setup lang="ts">
/**
 * The headline numbers.
 *
 * Two of the complaints land here. The figures are tabular so a column of money
 * lines up, and the change against the previous period carries an arrow and a
 * sign as well as a color — red and green alone is the most common way a
 * dashboard loses the only reader who needed the hint (audit A4).
 *
 * Each label carries its definition from the semantic layer, readable rather
 * than hover-only, because "net sales" is only trustworthy if the owner can
 * check it means what their POS means by it.
 */
import type { Kpi } from '../api/types'
import UiSkeleton from '../ui/UiSkeleton.vue'
import { kpiValue, moneyShort, signedPercent } from '../lib/format'

defineProps<{
  kpis: Kpi[]
  currency: string
  comparedTo: string
  loading?: boolean
}>()

function direction(kpi: Kpi): 'up' | 'down' | 'flat' {
  if (kpi.change === null) return 'flat'
  const change = Number(kpi.change)
  if (change > 0.0005) return 'up'
  if (change < -0.0005) return 'down'
  return 'flat'
}
</script>

<template>
  <div
    class="grid grid-cols-2 gap-3 md:grid-cols-4"
    :aria-busy="loading ? 'true' : 'false'"
  >
    <template v-if="loading">
      <div v-for="index in 4" :key="index" class="rounded-lg border border-border bg-surface p-4">
        <UiSkeleton :lines="2" />
      </div>
    </template>

    <div
      v-for="kpi in loading ? [] : kpis"
      :key="kpi.key"
      class="min-w-0 rounded-lg border border-border bg-surface p-4"
    >
      <p class="text-sm text-ink-muted">{{ kpi.label }}</p>
      <p class="tabular mt-1 text-2xl leading-tight font-bold text-ink">
        {{ kpiValue(kpi.value, kpi.unit, currency) }}
      </p>
      <p class="mt-1.5 flex flex-wrap items-baseline gap-x-1.5 text-sm">
        <span
          v-if="kpi.change !== null"
          class="tabular font-semibold"
          :class="{
            'text-success': direction(kpi) === 'up',
            'text-danger': direction(kpi) === 'down',
            'text-ink-muted': direction(kpi) === 'flat',
          }"
        >
          <!-- The glyph and the sign carry the meaning; the color agrees. -->
          <span aria-hidden="true">{{ direction(kpi) === 'up' ? '▲' : direction(kpi) === 'down' ? '▼' : '—' }}</span>
          {{ signedPercent(kpi.change) }}
        </span>
        <span class="text-ink-muted">
          <template v-if="kpi.previous !== null">
            vs {{ kpi.unit === 'money' ? moneyShort(kpi.previous, currency) : kpi.previous }}
            {{ comparedTo }}
          </template>
          <template v-else>as of the last sync</template>
        </span>
      </p>
      <span class="sr-only">{{ kpi.definition }}</span>
    </div>
  </div>
</template>
