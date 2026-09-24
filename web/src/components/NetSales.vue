<script setup lang="ts">
/**
 * Net sales over time, at the grain you ask for.
 *
 * The complaint was specific: *"I'd like to be able to drag and zoom in, or
 * change the graph to be per day instead of per week."* Both are here. The
 * grain goes to the API — `sales_series` has always supported day and month,
 * only the dashboard route was hardcoded to weeks — and the zoom is ECharts'
 * `dataZoom`, dragged on the chart itself or nudged from the keyboard.
 *
 * The window shrinks as the grain gets finer. A year of daily points is 364
 * marks on a phone-width axis, which is a smear, not a chart.
 */
import { DataZoomComponent } from 'echarts/components'
import { use } from 'echarts/core'
import { computed, ref, watch } from 'vue'

import { api } from '../api/client'
import type { ChartSpec, SalesSeries, Widget } from '../api/types'
import ChartRenderer from './ChartRenderer.vue'
import SectionCard from './SectionCard.vue'
import { shopDate } from '../lib/format'

use([DataZoomComponent])

const props = defineProps<{ tenant: string; currency: string; initial?: Widget<SalesSeries> | null }>()

type Grain = 'day' | 'week' | 'month'

/** Label, and how far back it is sensible to look at that grain. */
const GRAINS: { value: Grain; label: string; weeks: number }[] = [
  { value: 'day', label: 'Day', weeks: 8 },
  { value: 'week', label: 'Week', weeks: 52 },
  { value: 'month', label: 'Month', weeks: 104 },
]

const grain = ref<Grain>('week')
const widget = ref<Widget<SalesSeries> | null>(props.initial ?? null)
const loading = ref(false)
const error = ref<string | null>(null)

async function load() {
  const chosen = GRAINS.find((one) => one.value === grain.value)!
  loading.value = true
  error.value = null
  try {
    const board = await api.dashboard(props.tenant, 30, chosen.weeks, grain.value)
    widget.value = board.sales_over_time
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

// Not on mount: Home already fetched the weekly series with the rest of the
// first paint, and asking again for what we have is a second spinner for
// nothing.
watch(grain, load)
watch(
  () => props.initial,
  (next) => {
    if (next && grain.value === 'week') widget.value = next
  },
)

const spec = computed<ChartSpec | null>(() => {
  const series = widget.value?.data
  if (!series?.points.length) return null
  return {
    type: 'line',
    title: '',
    x: 'when',
    y: ['net_sales'],
    data: series.points.map((point) => ({
      // The API labels a weekly bucket "week of 2026-02-16", which is right in
      // a sentence and far too long under an axis tick.
      when: shopDate(point.bucket, series.timezone).replace(/, \d{4}$/, ''),
      net_sales: point.net_sales,
    })),
  }
})

const subtitle = computed(() => {
  const count = widget.value?.data?.points.length ?? 0
  if (!count) return ''
  const noun = grain.value === 'day' ? 'days' : grain.value === 'week' ? 'weeks' : 'months'
  return `Last ${count} ${noun}`
})
</script>

<template>
  <SectionCard
    title="Net sales"
    :subtitle="subtitle"
    :loading="loading && !widget"
    :available="widget?.available ?? true"
    :reason="widget?.reason"
    :caveats="widget?.data?.caveats"
    :empty="!spec"
  >
    <template #actions>
      <div
        class="flex overflow-hidden rounded-md border border-border-strong"
        role="group"
        aria-label="Bucket size"
      >
        <button
          v-for="option in GRAINS"
          :key="option.value"
          type="button"
          class="min-h-11 border-r border-border-strong px-3 text-sm font-semibold last:border-r-0"
          :class="
            grain === option.value
              ? 'bg-primary text-primary-fg'
              : 'bg-surface text-ink-muted hover:bg-raised hover:text-ink'
          "
          :aria-pressed="grain === option.value"
          @click="grain = option.value"
        >
          {{ option.label }}
        </button>
      </div>
    </template>

    <div v-if="error" role="alert">
      <p class="text-base text-ink">We could not load that view.</p>
      <p class="mt-1 text-sm text-ink-muted">{{ error }}</p>
      <button
        type="button"
        class="mt-2 min-h-11 rounded-sm text-sm font-semibold text-primary underline underline-offset-4"
        @click="load"
      >
        Try again
      </button>
    </div>

    <template v-else>
      <ChartRenderer v-if="spec" :spec="spec" :currency="currency" :height="220" zoomable />
      <p class="mt-1 text-sm text-ink-muted">
        Drag across the chart to zoom in. Double-click to reset.
      </p>
    </template>
  </SectionCard>
</template>
