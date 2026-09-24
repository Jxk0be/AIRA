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
  }>(),
  { currency: 'USD', height: 260 },
)

/** Read the palette off the page so a chart is never a different product. */
const ink = ref('#17150f')
const muted = ref('#6f675a')
const rule = ref('#e4ddd0')
const brand = ref('#1b4d3e')
const panel = ref('#ffffff')

// Five steps around the brand, warm-to-cool, distinguishable in both themes
// and still distinguishable in greyscale — a printed dashboard is a real thing
// in a back office.
const series = computed(() => [
  brand.value,
  '#c07a2c',
  '#4c6b8a',
  '#9b4b52',
  '#6d7f4a',
  '#8a6ba3',
])

const holder = ref<HTMLElement | null>(null)
let watcher: MediaQueryList | null = null
let observer: ResizeObserver | null = null

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
  rule.value = pick('--rule', rule.value)
  brand.value = pick('--brand', brand.value)
  panel.value = pick('--panel', panel.value)
}

onMounted(() => {
  readTheme()
  requestAnimationFrame(fit)
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

const option = computed(() => {
  const base = {
    backgroundColor: 'transparent',
    color: series.value,
    animationDuration: 420,
    textStyle: { fontFamily: 'IBM Plex Sans, sans-serif', color: muted.value, fontSize: 11 },
    tooltip: {
      trigger: props.spec.type === 'pie' ? 'item' : 'axis',
      backgroundColor: panel.value,
      borderColor: rule.value,
      borderWidth: 1,
      padding: [6, 10],
      textStyle: { color: ink.value, fontSize: 12 },
      valueFormatter: (raw: unknown) => readable(raw),
    },
    legend:
      props.spec.y.length > 1 || props.spec.type === 'pie'
        ? {
            bottom: 0,
            itemWidth: 8,
            itemHeight: 8,
            icon: 'rect',
            textStyle: { color: muted.value, fontSize: 11 },
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
      bottom: props.spec.y.length > 1 ? 34 : 18,
      left: 4,
      containLabel: true,
    },
    xAxis: {
      type: 'category',
      data: categories.value,
      axisLine: { lineStyle: { color: rule.value } },
      axisTick: { show: false },
      axisLabel: {
        color: muted.value,
        fontSize: 10,
        hideOverlap: true,
      },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: rule.value, type: 'dashed' } },
      axisLabel: { color: muted.value, fontSize: 10, formatter: axisText },
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
    <div v-if="spec.type === 'table'" class="-mx-1 overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-rule text-left">
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
          <tr v-for="(row, index) in spec.data" :key="index" class="border-b border-rule/60">
            <td class="px-1 py-1.5">{{ row[spec.x] }}</td>
            <td v-for="key in spec.y" :key="key" class="tabular px-1 py-1.5 text-right">
              {{ readable(row[key]) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else ref="holder" class="w-full">
      <VChart
        :option="option"
        :style="{ height: `${height}px`, width: '100%' }"
        :autoresize="true"
      />
    </div>

    <p v-if="spec.note" class="mt-1 text-xs text-ink-faint">{{ spec.note }}</p>
  </figure>
</template>
