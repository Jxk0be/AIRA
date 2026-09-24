<script setup lang="ts">
/**
 * The headline numbers, set as a ledger line rather than four floating cards.
 *
 * Each one carries its own definition from the semantic layer, shown on hover
 * and readable by a screen reader, because "net sales" is only trustworthy if
 * the owner can check it means what their POS means by it.
 */
import type { Kpi } from '../api/types'
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
    class="grid grid-cols-2 border border-rule bg-panel md:grid-cols-4"
    :aria-busy="loading ? 'true' : 'false'"
  >
    <template v-if="loading">
      <div
        v-for="index in 4"
        :key="`skeleton-${index}`"
        class="border-r border-b border-rule px-4 py-4 last:border-r-0 md:border-b-0"
      >
        <div class="h-2.5 w-20 bg-sunk working"></div>
        <div class="mt-3 h-7 w-28 bg-sunk working"></div>
      </div>
    </template>

    <div
      v-for="(kpi, index) in loading ? [] : kpis"
      :key="kpi.key"
      class="rise border-rule px-4 py-4 not-last:border-r [&:nth-child(-n+2)]:border-b md:[&:nth-child(-n+2)]:border-b-0"
      :style="{ animationDelay: `${index * 45}ms` }"
    >
      <p
        class="text-[0.68rem] tracking-[0.14em] text-ink-faint uppercase"
        :title="`${kpi.definition} (${kpi.formula})`"
      >
        {{ kpi.label }}
      </p>
      <p class="tabular display mt-1.5 text-[1.75rem] leading-none font-semibold text-ink">
        {{ kpiValue(kpi.value, kpi.unit, currency) }}
      </p>
      <p class="mt-1.5 flex items-baseline gap-1.5 text-xs">
        <span
          v-if="kpi.change !== null"
          class="tabular font-medium"
          :class="{
            'text-up': direction(kpi) === 'up',
            'text-down': direction(kpi) === 'down',
            'text-ink-faint': direction(kpi) === 'flat',
          }"
        >
          {{ signedPercent(kpi.change) }}
        </span>
        <span class="text-ink-faint">
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
