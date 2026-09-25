<script setup lang="ts">
/**
 * What to deal with, then how trade is going.
 *
 * Actions first, numbers second — that order is the whole argument of the
 * redesign. Before this, the dashboard opened with four KPIs and buried a
 * three-row preview of the inbox below them, and the full list lived on a
 * different screen entirely. An owner with five minutes before opening got the
 * week's figures and had to go looking for the jobs.
 *
 * What left this page, and where it went:
 *   Top products, Category mix, By location, By channel  ->  Reports > Sales.
 *       Monthly questions. Four charts nobody asked for is not a first screen.
 *   Running out, Sitting still  ->  deleted, not moved.
 *       They were ten-row previews of Stock > Reorder and Stock > Not selling,
 *       with no link to either, so the only way to find the real screen was to
 *       already know it existed (audit U3).
 *
 * What stayed: the jobs, one number each for the four things that matter, one
 * trend, and whatever the owner pinned themselves.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../api/client'
import type { Dashboard, PinnedChart } from '../api/types'
import ChartRenderer from '../components/ChartRenderer.vue'
import KpiRow from '../components/KpiRow.vue'
import NetSales from '../components/NetSales.vue'
import SectionCard from '../components/SectionCard.vue'
import WorthDoingList from '../components/WorthDoingList.vue'
import { shopDate } from '../lib/format'
import { useTenantStore } from '../stores/tenant'
import UiButton from '../ui/UiButton.vue'
import { withToast } from '../ui/toast'

const route = useRoute()
const shop = useTenantStore()

const slug = computed(() => String(route.params.tenant ?? ''))
const board = ref<Dashboard | null>(null)
const pinned = ref<PinnedChart[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const days = ref(30)

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
      api.charts(slug.value).catch(() => [] as PinnedChart[]),
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

async function unpin(chart: PinnedChart) {
  const done = await withToast(() => api.unpinChart(slug.value, chart.id), {
    success: `Unpinned “${chart.title}”`,
    failure: 'Could not unpin that',
  })
  if (done !== undefined) pinned.value = pinned.value.filter((row) => row.id !== chart.id)
}

/** Deduped: two widgets often carry the same caveat, and it needs saying once. */
const kpiCaveats = computed(() => board.value?.kpi_caveats ?? [])
</script>

<template>
  <div class="mx-auto max-w-3xl px-4 py-5 sm:px-6">
    <!-- The page heading. The shell's top bar says "Home"; this says whose. -->
    <header class="mb-5">
      <h1 class="display text-2xl font-bold text-ink">{{ shop.name }}</h1>
      <p v-if="board" class="tabular mt-0.5 text-sm text-ink-muted">
        {{ shopDate(board.period.start, board.timezone) }} –
        {{ shopDate(board.period.end, board.timezone) }}
      </p>
    </header>

    <div v-if="error" class="rounded-lg border border-danger bg-danger-subtle p-4" role="alert">
      <p class="text-base font-medium text-ink">We could not reach {{ shop.name }}.</p>
      <p class="mt-1 text-sm text-ink-muted">{{ error }}</p>
      <UiButton class="mt-3" size="sm" variant="secondary" @click="load">Try again</UiButton>
    </div>

    <template v-else>
      <!-- Actions first. -->
      <WorthDoingList :tenant="slug" />

      <section class="mt-8" aria-labelledby="this-week-heading">
        <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 id="this-week-heading" class="text-xl font-bold text-ink">How trade is going</h2>
          <div
            class="flex overflow-hidden rounded-md border border-border-strong"
            role="group"
            aria-label="Period"
          >
            <button
              v-for="option in PERIODS"
              :key="option.days"
              type="button"
              class="min-h-11 border-r border-border-strong px-3 text-sm font-semibold last:border-r-0"
              :class="
                days === option.days
                  ? 'bg-primary text-primary-fg'
                  : 'bg-surface text-ink-muted hover:bg-raised hover:text-ink'
              "
              :aria-pressed="days === option.days"
              @click="days = option.days"
            >
              {{ option.label }}
            </button>
          </div>
        </div>

        <KpiRow
          :kpis="board?.kpis ?? []"
          :currency="board?.currency ?? shop.currency"
          :compared-to="`the ${board?.previous.days ?? days} days before`"
          :loading="loading"
        />

        <div v-if="kpiCaveats.length" class="mt-2 flex gap-2">
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
            <p
              v-for="caveat in kpiCaveats"
              :key="caveat.code"
              class="text-sm leading-snug text-ink-muted"
            >
              {{ caveat.message }}
            </p>
          </div>
        </div>

        <div class="mt-4">
          <NetSales
            :tenant="slug"
            :currency="board?.currency ?? shop.currency"
            :initial="board?.sales_over_time ?? null"
          />
        </div>
      </section>

      <section v-if="pinned.length" class="mt-8" aria-labelledby="pinned-heading">
        <h2 id="pinned-heading" class="mb-3 text-xl font-bold text-ink">Pinned</h2>
        <div class="space-y-4">
          <SectionCard v-for="chart in pinned" :key="chart.id" :title="chart.title">
            <template #actions>
              <UiButton size="sm" variant="ghost" @click="unpin(chart)">Unpin</UiButton>
            </template>
            <ChartRenderer
              :spec="chart.spec"
              :currency="board?.currency ?? shop.currency"
              :height="220"
            />
          </SectionCard>
        </div>
      </section>
      <p v-else class="mt-8 text-sm text-ink-muted">
        Charts you pin from Ask show up here.
      </p>
    </template>
  </div>
</template>
