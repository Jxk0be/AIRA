<script setup lang="ts">
/**
 * One ChartSpec, drawn.
 *
 * The same component renders a chart the assistant produced mid-conversation
 * and a chart pinned to the dashboard, because they are the same object. If
 * this ever forked into "chat charts" and "dashboard charts", a pinned chart
 * would start to look different from the one that was pinned.
 *
 * Every number in a spec has already been checked against what the tools
 * actually returned, server-side. Nothing here computes anything: it reads the
 * values it is given, in the order it is given them.
 */
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'
import { getInstanceByDom, use } from 'echarts/core'
import { SVGRenderer } from 'echarts/renderers'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import VChart from 'vue-echarts'

import type { ChartSpec } from '../api/types'
import { money, quantity } from '../lib/format'

use([
  SVGRenderer,
  LineChart,
  BarChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
])

const props = withDefaults(
  defineProps<{
    spec: ChartSpec
    currency?: string
    height?: number
    /** Drag across the plot to zoom. Only the time series asks for it. */
    zoomable?: boolean
  }>(),
  { currency: 'USD', height: 260, zoomable: false },
)

/**
 * Read the palette off the page, so a chart is never a different product.
 *
 * The six series colours are tokens rather than literals here. They are chosen
 * by `scripts/check-contrast.ts`, which holds them to 3:1 against the surface
 * and 20 CIEDE2000 apart from each other — 11 apart after simulating
 * protanopia, deuteranopia and tritanopia. Hand-picked hues passed the contrast
 * half of that and still left two series indistinguishable to a deuteranope,
 * which is why they are not hand-picked any more.
 */
const ink = ref('#1a1a17')
const muted = ref('#5c5c55')
const rule = ref('#e4e4df')
const brand = ref('#3d46b8')
const panel = ref('#ffffff')
const palette = ref<string[]>(['#3d46b8', '#794060', '#bb754b', '#9280b1', '#605535', '#119681'])

const series = computed(() => palette.value)

const holder = ref<HTMLElement | null>(null)
let watcher: MediaQueryList | null = null
let observer: ResizeObserver | null = null
let themeWatcher: MutationObserver | null = null

/**
 * Re-measure, through the ECharts instance itself.
 *
 * ECharts sizes its container the moment it is created, and in a grid cell
 * that is one layout pass too early: the chart lays out for whatever width the
 * cell had before the grid resolved, and then keeps that layout even though
 * the element around it is now full width. The fix is to ask it to measure
 * again once the DOM has settled — and to keep asking whenever the element
 * changes size, which is what makes the panel survive a window drag.
 *
 * It goes through `getInstanceByDom` rather than the component ref because the
 * instance is the thing that owns the layout; the ref is a wrapper whose shape
 * is vue-echarts' business, not ours.
 */
function fit() {
  const el = holder.value?.querySelector<HTMLElement>('[_echarts_instance_]')
  if (el) getInstanceByDom(el)?.resize()
}

function readTheme() {
  const styles = getComputedStyle(document.documentElement)
  const pick = (name: string, fallback: string) =>
    styles.getPropertyValue(name).trim() || fallback
  ink.value = pick('--ink', ink.value)
  muted.value = pick('--ink-muted', muted.value)
  rule.value = pick('--border', rule.value)
  brand.value = pick('--primary', brand.value)
  panel.value = pick('--surface', panel.value)
  palette.value = palette.value.map((fallback, index) => pick(`--chart-${index + 1}`, fallback))
}

onMounted(() => {
  readTheme()
  requestAnimationFrame(fit)
  // The theme is an attribute on <html> now, not just an OS preference: a
  // matchMedia listener alone would leave every chart in the old palette when
  // the owner flips the toggle by hand.
  themeWatcher = new MutationObserver(readTheme)
  themeWatcher.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme'],
  })
  watcher = window.matchMedia('(prefers-color-scheme: dark)')
  watcher.addEventListener('change', readTheme)
  if (holder.value) {
    observer = new ResizeObserver(fit)
    observer.observe(holder.value)
  }
})

watch(
  () => props.spec,
  async () => {
    await nextTick()
    fit()
  },
)

onBeforeUnmount(() => {
  watcher?.removeEventListener('change', readTheme)
  themeWatcher?.disconnect()
  observer?.disconnect()
})

/** Money-ish series get a currency axis; counts and units do not. */
const isMoney = computed(() =>
  props.spec.y.every((key) => /sales|value|total|revenue|margin|cost|profit|price/i.test(key)),
)

function label(key: string): string {
  return key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

function value(row: Record<string, unknown>, key: string): number {
  const raw = row[key]
  return raw === null || raw === undefined ? 0 : Number(raw)
}

function axisText(raw: number): string {
  if (isMoney.value) {
    return Math.abs(raw) >= 1000
      ? `$${(raw / 1000).toFixed(raw % 1000 === 0 ? 0 : 1)}k`
      : `$${raw}`
  }
  return quantity(raw)
}

function readable(raw: unknown): string {
  return isMoney.value ? money(String(raw ?? 0), props.currency) : quantity(String(raw ?? 0))
}

const categories = computed(() => props.spec.data.map((row) => String(row[props.spec.x] ?? '')))

/**
 * A chart is a picture, and a picture needs words.
 *
 * `role="img"` plus this label is what a screen reader reads instead of the
 * SVG's several hundred meaningless nodes; the table toggle underneath is for
 * everyone else who wants the actual figures. The `table` spec type already
 * renders through this same component, so the toggle is a flag, not a fork.
 */
const asTable = ref(false)

const summary = computed(() => {
  const rows = props.spec.data
  const key = props.spec.y[0]
  if (!rows.length || !key) return props.spec.title || 'Chart with no data.'
  const values = rows.map((row) => value(row, key))
  const lowest = Math.min(...values)
  const highest = Math.max(...values)
  const peak = categories.value[values.indexOf(highest)]
  const what = props.spec.title || label(key)
  return (
    `${what}: ${rows.length} points from ${categories.value[0]} to ` +
    `${categories.value[categories.value.length - 1]}, ` +
    `ranging from ${readable(lowest)} to ${readable(highest)}, highest at ${peak}.`
  )
})

const option = computed(() => {
  const base = {
    backgroundColor: 'transparent',
    color: series.value,
    animationDuration: 420,
    textStyle: { fontFamily: 'Inter, system-ui, sans-serif', color: muted.value, fontSize: 13 },
    tooltip: {
      trigger: props.spec.type === 'pie' ? 'item' : 'axis',
      backgroundColor: panel.value,
      borderColor: rule.value,
      borderWidth: 1,
      padding: [6, 10],
      textStyle: { color: ink.value, fontSize: 13 },
      valueFormatter: (raw: unknown) => readable(raw),
    },
    legend:
      props.spec.y.length > 1 || props.spec.type === 'pie'
        ? {
            bottom: 0,
            itemWidth: 8,
            itemHeight: 8,
            icon: 'rect',
            textStyle: { color: muted.value, fontSize: 13 },
          }
        : undefined,
  }

  if (props.spec.type === 'pie') {
    const key = props.spec.y[0] ?? ''
    return {
      ...base,
      series: [
        {
          type: 'pie',
          radius: ['52%', '76%'],
          center: ['50%', '46%'],
          itemStyle: { borderColor: panel.value, borderWidth: 2 },
          label: { show: false },
          data: props.spec.data.map((row) => ({
            name: String(row[props.spec.x] ?? ''),
            value: value(row, key),
          })),
        },
      ],
    }
  }

  return {
    ...base,
    grid: {
      top: 16,
      right: 8,
      // Room for the zoom slider when there is one to make room for.
      bottom: (props.spec.y.length > 1 ? 34 : 18) + (props.zoomable ? 26 : 0),
      left: 4,
      containLabel: true,
    },
    /**
     * `inside` is the drag-to-zoom on the plot itself; the slider underneath is
     * what makes it discoverable and, more to the point, operable without a
     * mouse — its handles are focusable.
     */
    dataZoom: props.zoomable
      ? [
          { type: 'inside', throttle: 50 },
          {
            type: 'slider',
            height: 18,
            bottom: 2,
            borderColor: rule.value,
            fillerColor: 'transparent',
            handleStyle: { color: brand.value, borderColor: brand.value },
            moveHandleStyle: { color: brand.value },
            dataBackground: {
              lineStyle: { color: muted.value, opacity: 0.5 },
              areaStyle: { color: muted.value, opacity: 0.15 },
            },
            selectedDataBackground: {
              lineStyle: { color: brand.value },
              areaStyle: { color: brand.value, opacity: 0.2 },
            },
            textStyle: { color: muted.value, fontSize: 13 },
          },
        ]
      : undefined,
    xAxis: {
      type: 'category',
      data: categories.value,
      axisLine: { lineStyle: { color: rule.value } },
      axisTick: { show: false },
      axisLabel: {
        color: muted.value,
        fontSize: 13,
        hideOverlap: true,
      },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: rule.value, type: 'dashed' } },
      axisLabel: { color: muted.value, fontSize: 13, formatter: axisText },
    },
    series: props.spec.y.map((key, index) => ({
      name: label(key),
      type: props.spec.type === 'line' ? 'line' : 'bar',
      smooth: false,
      symbol: 'none',
      lineStyle: { width: 1.8 },
      barMaxWidth: 22,
      itemStyle: { color: series.value[index % series.value.length] },
      data: props.spec.data.map((row) => value(row, key)),
    })),
  }
})
</script>

<template>
  <figure class="m-0">
    <figcaption v-if="spec.title" class="mb-2 text-sm font-medium text-ink">
      {{ spec.title }}
    </figcaption>

    <!-- A table is a chart type here. Sometimes the honest picture is a list. -->
    <div
      v-if="spec.type === 'table'"
      class="-mx-1 overflow-x-auto"
      tabindex="0"
      role="region"
      :aria-label="summary"
    >
      <table class="w-full text-sm">
        <caption class="sr-only">{{ summary }}</caption>
        <thead>
          <tr class="border-b border-border text-left">
            <th class="px-1 py-1.5 font-medium text-ink-muted">{{ label(spec.x) }}</th>
            <th
              v-for="key in spec.y"
              :key="key"
              class="px-1 py-1.5 text-right font-medium text-ink-muted"
            >
              {{ label(key) }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in spec.data" :key="index" class="border-b border-border/60">
            <td class="px-1 py-1.5">{{ row[spec.x] }}</td>
            <td v-for="key in spec.y" :key="key" class="tabular px-1 py-1.5 text-right">
              {{ readable(row[key]) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <template v-else>
      <div v-if="asTable" class="-mx-1 overflow-x-auto" tabindex="0" role="region" :aria-label="summary">
        <table class="w-full text-sm">
          <caption class="sr-only">{{ summary }}</caption>
          <thead>
            <tr class="border-b border-border text-left">
              <th scope="col" class="px-1 py-1.5 font-medium text-ink-muted">{{ label(spec.x) }}</th>
              <th
                v-for="key in spec.y"
                :key="key"
                scope="col"
                class="px-1 py-1.5 text-right font-medium text-ink-muted"
              >
                {{ label(key) }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in spec.data" :key="index" class="border-b border-border/60">
              <td class="px-1 py-1.5">{{ row[spec.x] }}</td>
              <td v-for="key in spec.y" :key="key" class="tabular px-1 py-1.5 text-right">
                {{ readable(row[key]) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-show="!asTable" ref="holder" class="w-full" role="img" :aria-label="summary">
        <VChart
          :option="option"
          :style="{ height: `${height}px`, width: '100%' }"
          :autoresize="true"
        />
      </div>

      <div class="mt-1 flex justify-end">
        <button
          type="button"
          class="min-h-11 rounded-sm px-1 text-sm text-ink-muted underline underline-offset-4 hover:text-ink"
          :aria-pressed="asTable"
          @click="asTable = !asTable"
        >
          {{ asTable ? 'View as chart' : 'View as table' }}
        </button>
      </div>
    </template>

    <p v-if="spec.note" class="mt-1 text-sm text-ink-muted">{{ spec.note }}</p>
  </figure>
</template>
