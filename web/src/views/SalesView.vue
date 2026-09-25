<script setup lang="ts">
/**
 * The breakdowns: what sold, in what category, where, through which channel, and
 * — for a shop whose tills are not all the same brand — out of which system.
 *
 * That last one is the section neither Square nor Shopify can ever show a shop,
 * because showing it would mean adding a competitor's revenue to their own. It is
 * rendered only for the shops that have more than one register, and it is
 * rendered first, because for those shops it is the reason they are here.
 *
 * These lived on the dashboard, which made the first screen of the day a wall
 * of four charts you did not ask for. They are a monthly question, not a daily
 * one, so they live under Reports now and Home keeps the one number and the one
 * trend that are worth a glance.
 *
 * Same endpoint as Home, same period, so the two can never disagree.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../api/client'
import type { ChartSpec, Dashboard } from '../api/types'
import ChartRenderer from '../components/ChartRenderer.vue'
import RankedList from '../components/RankedList.vue'
import UiButton from '../ui/UiButton.vue'
import UiSkeleton from '../ui/UiSkeleton.vue'
import SectionCard from '../components/SectionCard.vue'
import { money, shopDate } from '../lib/format'
import { useTenantStore } from '../stores/tenant'

const route = useRoute()
const shop = useTenantStore()

const slug = computed(() => String(route.params.tenant ?? ''))
const board = ref<Dashboard | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    board.value = await api.dashboard(slug.value, 30)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(slug, load)

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
</script>

<template>
  <div class="mx-auto max-w-3xl px-4 py-5 sm:px-6">
    <p v-if="board" class="tabular mb-4 text-sm text-ink-muted">
      {{ shopDate(board.period.start, board.timezone) }} –
      {{ shopDate(board.period.end, board.timezone) }}
    </p>

    <div v-if="error" class="rounded-md border border-danger bg-danger-subtle p-4" role="alert">
      <p class="font-medium text-ink">We could not load the breakdowns.</p>
      <p class="mt-1 text-sm text-ink-muted">{{ error }}</p>
      <UiButton class="mt-3" size="sm" variant="secondary" @click="load">Try again</UiButton>
    </div>

    <div v-else class="space-y-4">
      <!--
        First, and only for the shops it means anything to.

        A shop running one till has nothing to consolidate, and a permanent
        "connect another register" card on their Reports screen would be an advert
        rather than a number. A shop running two has been reading two dashboards
        and adding them up by hand, so for them this is the most useful thing on
        the page and it goes at the top.
      -->
      <SectionCard
        v-if="shop.hasMultipleSources"
        title="All your registers"
        :loading="loading"
        :available="board?.by_source.available ?? true"
        :reason="board?.by_source.reason"
        :caveats="board?.by_source.data?.caveats"
        :empty="!board?.by_source.data?.rows.length"
      >
        <p class="mb-3 text-sm text-ink-muted">
          Every system {{ shop.name }} sells through, added together. The shares are
          of the shop's whole net sales for the period.
        </p>
        <RankedList
          v-if="board?.by_source.data"
          :rows="board.by_source.data.rows"
          :currency="board.currency"
        />
      </SectionCard>

      <SectionCard
        title="Top products"
        :subtitle="board ? `Best ${board.top_products.data?.rows.length ?? 0} by net sales` : ''"
        :loading="loading"
        :available="board?.top_products.available ?? true"
        :reason="board?.top_products.reason"
        :caveats="board?.top_products.data?.caveats"
        :empty="!board?.top_products.data?.rows.length"
      >
        <RankedList
          v-if="board?.top_products.data"
          :rows="board.top_products.data.rows"
          :currency="board.currency"
        />
      </SectionCard>

      <SectionCard
        title="Category mix"
        :subtitle="
          board ? `${money(board.category_mix.data?.total_net_sales ?? '0', board.currency)} in total` : ''
        "
        :loading="loading"
        :available="board?.category_mix.available ?? true"
        :reason="board?.category_mix.reason"
        :caveats="board?.category_mix.data?.caveats"
        :empty="!categoryChart"
      >
        <ChartRenderer
          v-if="categoryChart"
          :spec="categoryChart"
          :currency="board?.currency ?? 'USD'"
          :height="240"
        />
      </SectionCard>

      <SectionCard
        title="By location"
        :loading="loading"
        :available="board?.by_location.available ?? true"
        :reason="board?.by_location.reason"
        :caveats="board?.by_location.data?.caveats"
        :empty="!board?.by_location.data?.rows.length"
      >
        <RankedList
          v-if="board?.by_location.data"
          :rows="board.by_location.data.rows"
          :currency="board.currency"
        />
      </SectionCard>

      <SectionCard
        title="In store or online"
        :loading="loading"
        :available="board?.by_channel.available ?? true"
        :reason="board?.by_channel.reason"
        :caveats="board?.by_channel.data?.caveats"
        :empty="!board?.by_channel.data?.rows.length"
      >
        <RankedList
          v-if="board?.by_channel.data"
          :rows="board.by_channel.data.rows"
          :currency="board.currency"
        />
      </SectionCard>

      <UiSkeleton v-if="loading && !board" :lines="3" />
    </div>

    <p class="mt-6 text-sm text-ink-muted">
      Every figure here uses the same definitions as {{ shop.name }}'s other screens.
    </p>
  </div>
</template>
