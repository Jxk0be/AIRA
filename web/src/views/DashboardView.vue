<script setup lang="ts">
/**
 * How trade is going.
 *
 * Every widget is driven by what the shop's own system can support: a
 * single-location shop is never shown an empty location split, it is shown the
 * sentence explaining there is nothing to split. That is the whole argument of
 * this product on one screen, so it is worth the envelope the API sends.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../api/client'
import type { ChartSpec, Dashboard, PinnedChart } from '../api/types'
import ChartRenderer from '../components/ChartRenderer.vue'
import KpiRow from '../components/KpiRow.vue'
import PanelCard from '../components/PanelCard.vue'
import RankedList from '../components/RankedList.vue'
import StockTable from '../components/StockTable.vue'
import { money, shopDate } from '../lib/format'
import { useTenantStore } from '../stores/tenant'

const route = useRoute()
const shop = useTenantStore()

const board = ref<Dashboard | null>(null)
const pinned = ref<PinnedChart[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const days = ref(30)

const slug = computed(() => String(route.params.tenant ?? ''))

const PERIODS = [
  { days: 7, label: '7 days' },
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
]

async function load() {
  loading.value = true
  error.value = null
  try {
    const [dashboard, charts] = await Promise.all([
      api.dashboard(slug.value, days.value),
      api.charts(slug.value),
    ])
    board.value = dashboard
    pinned.value = charts
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch([slug, days], load)

/**
 * The weekly series, as the chart format both screens share.
 *
 * The API labels a bucket "week of 2026-02-16", which is right in a sentence
 * and far too long under an axis tick, so the axis gets the date itself.
 */
const salesChart = computed<ChartSpec | null>(() => {
  const series = board.value?.sales_over_time.data
  if (!series) return null
  return {
    type: 'line',
    title: '',
    x: 'week',
    y: ['net_sales'],
    data: series.points.map((point) => ({
      week: shopDate(point.bucket, series.timezone).replace(/, \d{4}$/, ''),
      net_sales: point.net_sales,
    })),
  }
})

const categoryChart = computed<ChartSpec | null>(() => {
  const rows = board.value?.category_mix.data?.rows
  if (!rows?.length) return null
  return {
    type: 'pie',
    title: '',
    x: 'label',
    y: ['net_sales'],
    data: rows.map((row) => ({ label: row.label, net_sales: row.net_sales })),
  }
})

async function unpin(chart: PinnedChart) {
  await api.unpinChart(slug.value, chart.id)
  pinned.value = pinned.value.filter((row) => row.id !== chart.id)
}
</script>

<template>
  <div class="mx-auto max-w-5xl px-4 py-5 sm:px-6 lg:py-8">
    <header class="mb-5 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div>
        <h1 class="display text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
          {{ shop.name }}
        </h1>
        <p v-if="board" class="tabular mt-0.5 text-xs text-ink-faint">
          {{ shopDate(board.period.start, board.timezone) }} –
          {{ shopDate(board.period.end, board.timezone) }}
        </p>
      </div>

      <div class="flex overflow-hidden rounded-sm border border-rule bg-panel" role="group">
        <button
          v-for="option in PERIODS"
          :key="option.days"
          type="button"
          class="border-r border-rule px-3 py-1 text-xs last:border-r-0 hover:bg-sunk"
          :class="days === option.days ? 'bg-sunk font-medium text-ink' : 'text-ink-muted'"
          :aria-pressed="days === option.days"
          @click="days = option.days"
        >
          {{ option.label }}
        </button>
      </div>
    </header>

    <p v-if="error" class="border-l-2 border-down bg-panel px-4 py-3 text-sm" role="alert">
      {{ error }}
    </p>

    <template v-else>
      <KpiRow
        :kpis="board?.kpis ?? []"
        :currency="board?.currency ?? shop.currency"
        :compared-to="`the ${board?.previous.days ?? days} days before`"
        :loading="loading"
      />

      <p
        v-if="board?.kpi_caveats.length"
        class="mt-px border border-t-0 border-rule border-l-note-rule bg-note px-4 py-2 text-xs leading-snug text-note-ink"
      >
        <span v-for="caveat in board.kpi_caveats" :key="caveat.code" class="mr-2 inline-block">
          {{ caveat.message }}
        </span>
      </p>

      <div class="mt-4 grid gap-4 lg:grid-cols-2">
        <PanelCard
          class="lg:col-span-2"
          title="Net sales"
          subtitle="By week"
          :available="board?.sales_over_time.available ?? true"
          :reason="board?.sales_over_time.reason"
          :caveats="board?.sales_over_time.data?.caveats"
          :loading="loading"
          :empty="!salesChart?.data.length"
        >
          <ChartRenderer
            v-if="salesChart"
            :spec="salesChart"
            :currency="board?.currency ?? 'USD'"
            :height="240"
          />
        </PanelCard>

        <PanelCard
          title="Top products"
          :subtitle="`Best ${board?.top_products.data?.rows.length ?? 0} by net sales`"
          :available="board?.top_products.available ?? true"
          :reason="board?.top_products.reason"
          :caveats="board?.top_products.data?.caveats"
          :loading="loading"
          :empty="!board?.top_products.data?.rows.length"
        >
          <RankedList
            v-if="board?.top_products.data"
            :rows="board.top_products.data.rows"
            :currency="board.currency"
          />
        </PanelCard>

        <PanelCard
          title="Category mix"
          :subtitle="board ? `${money(board.category_mix.data?.total_net_sales ?? '0', board.currency)} in total` : ''"
          :available="board?.category_mix.available ?? true"
          :reason="board?.category_mix.reason"
          :caveats="board?.category_mix.data?.caveats"
          :loading="loading"
          :empty="!categoryChart"
        >
          <ChartRenderer
            v-if="categoryChart"
            :spec="categoryChart"
            :currency="board?.currency ?? 'USD'"
            :height="230"
          />
        </PanelCard>

        <PanelCard
          title="By location"
          :available="board?.by_location.available ?? true"
          :reason="board?.by_location.reason"
          :caveats="board?.by_location.data?.caveats"
          :loading="loading"
          :empty="!board?.by_location.data?.rows.length"
        >
          <RankedList
            v-if="board?.by_location.data"
            :rows="board.by_location.data.rows"
            :currency="board.currency"
          />
        </PanelCard>

        <PanelCard
          title="By channel"
          :available="board?.by_channel.available ?? true"
          :reason="board?.by_channel.reason"
          :caveats="board?.by_channel.data?.caveats"
          :loading="loading"
          :empty="!board?.by_channel.data?.rows.length"
        >
          <RankedList
            v-if="board?.by_channel.data"
            :rows="board.by_channel.data.rows"
            :currency="board.currency"
          />
        </PanelCard>

        <PanelCard
          title="Running out"
          subtitle="Selling faster than the shelf holds"
          :available="board?.low_stock.available ?? true"
          :reason="board?.low_stock.reason"
          :caveats="board?.low_stock.data?.caveats"
          :loading="loading"
          :empty="!board?.low_stock.data?.rows.length"
          empty-text="Nothing is about to run out."
        >
          <StockTable
            v-if="board?.low_stock.data"
            :rows="board.low_stock.data.rows"
            :currency="board.currency"
            :timezone="board.timezone"
            mode="cover"
          />
        </PanelCard>

        <PanelCard
          title="Sitting still"
          :subtitle="
            board?.dead_stock.data
              ? `${money(board.dead_stock.data.retail_value, board.currency)} on these shelves`
              : ''
          "
          :available="board?.dead_stock.available ?? true"
          :reason="board?.dead_stock.reason"
          :caveats="board?.dead_stock.data?.caveats"
          :loading="loading"
          :empty="!board?.dead_stock.data?.rows.length"
          empty-text="Everything on the shelf has sold recently."
        >
          <StockTable
            v-if="board?.dead_stock.data"
            :rows="board.dead_stock.data.rows"
            :currency="board.currency"
            :timezone="board.timezone"
            mode="idle"
          />
        </PanelCard>
      </div>

      <section v-if="pinned.length" class="mt-8">
        <h2 class="display mb-3 text-lg font-semibold tracking-tight text-ink">Pinned</h2>
        <div class="grid gap-4 lg:grid-cols-2">
          <PanelCard v-for="chart in pinned" :key="chart.id" :title="chart.title">
            <template #actions>
              <button
                type="button"
                class="text-xs text-ink-faint underline-offset-2 hover:text-down hover:underline"
                @click="unpin(chart)"
              >
                Unpin
              </button>
            </template>
            <ChartRenderer
              :spec="chart.spec"
              :currency="board?.currency ?? shop.currency"
              :height="230"
            />
          </PanelCard>
        </div>
      </section>

      <p v-else class="mt-8 text-xs text-ink-faint">
        Charts you pin from the assistant show up here.
      </p>
    </template>
  </div>
</template>
