<script setup lang="ts">
/**
 * Stock that has stopped earning its space, and a plan for each item.
 *
 * Sorted by cash tied up, because that is the order to work through it in.
 * Every row carries one specific thing to do — mark down to this price, bundle
 * with that, move it there, send it back — rather than a status, and one click
 * says it was done. That click is the whole feature: without it this is a list
 * of things to feel bad about, and with it the product can say a month later
 * what the advice was worth.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../api/client'
import type { DeadStockScreen, RescueAction, StaleItem } from '../api/types'
import PanelCard from '../components/PanelCard.vue'
import { money, moneyShort, percent, quantity, shopDate } from '../lib/format'
import { useTenantStore } from '../stores/tenant'

const route = useRoute()
const shop = useTenantStore()

const slug = computed(() => String(route.params.tenant ?? ''))
const screen = ref<DeadStockScreen | null>(null)
const actions = ref<RescueAction[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)
const expanded = ref<string | null>(null)

const GRADE: Record<string, string> = {
  dead: 'Nothing for four months',
  stale: 'Nothing for two months',
  slowing: 'Half the rate it used to be',
}

const done = computed(() => new Set(actions.value.map((row) => row.variant_id)))

async function load() {
  try {
    const [next, logged] = await Promise.all([
      api.deadStock(slug.value),
      api.rescueActions(slug.value),
    ])
    screen.value = next
    actions.value = logged
    error.value = null
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

/**
 * "I did this."
 *
 * The price is sent along for a markdown so the outcome job has the before and
 * after without having to guess which rung the owner took.
 */
async function log(item: StaleItem, priceAfter?: string) {
  notice.value = null
  try {
    await api.logRescue(slug.value, {
      variant_id: item.variant_id,
      kind: item.play,
      detail: item.detail,
      price_after: priceAfter ?? null,
    })
    notice.value = `Logged. We will check what happened to ${item.label} in 30 days.`
    await load()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  }
}

function result(item: StaleItem) {
  return actions.value.find((row) => row.variant_id === item.variant_id) ?? null
}

onMounted(load)
watch(slug, load)
</script>

<template>
  <div class="mx-auto max-w-5xl px-4 py-6 lg:px-8">
    <header class="mb-5">
      <h1 class="display text-2xl font-semibold tracking-tight text-ink">Dead stock</h1>
      <p class="mt-0.5 text-sm text-ink-muted">
        Money sitting still, worst first, with something specific to do about each one.
      </p>
    </header>

    <p v-if="error" class="mb-4 border-l-2 border-down bg-panel px-4 py-3 text-sm text-ink" role="alert">
      {{ error }}
    </p>
    <p v-if="notice" class="mb-4 border-l-2 border-brand bg-panel px-4 py-3 text-sm text-ink">
      {{ notice }}
    </p>

    <div
      v-if="screen"
      class="mb-5 flex flex-wrap items-baseline gap-x-6 gap-y-1 border border-rule bg-panel px-4 py-3"
    >
      <span class="tabular text-2xl font-semibold text-ink">
        {{ moneyShort(screen.total_cash, shop.currency) }}
      </span>
      <span class="text-sm text-ink-muted">
        across {{ screen.item_count }} items
        <template v-if="screen.counts.dead">· {{ screen.counts.dead }} dead</template>
        <template v-if="screen.counts.stale">· {{ screen.counts.stale }} stale</template>
        <template v-if="screen.counts.slowing">· {{ screen.counts.slowing }} slowing</template>
      </span>
      <span v-if="screen.cost_coverage" class="ml-auto text-xs text-ink-faint">
        {{ percent(screen.cost_coverage, 0) }} valued at cost, the rest at retail
      </span>
    </div>

    <PanelCard
      title="Worth starting on"
      :subtitle="screen ? `As of ${shopDate(screen.as_of, shop.timezone)}` : undefined"
      :loading="loading"
      :empty="!loading && !screen?.items.length"
      empty-text="Nothing is sitting still. Unusual, and good."
    >
      <ul class="-my-1 divide-y divide-rule">
        <li v-for="item in screen?.items ?? []" :key="item.variant_id" class="py-3.5">
          <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <div class="min-w-0">
              <span class="font-medium text-ink">{{ item.label }}</span>
              <span class="ml-2 text-xs text-ink-faint">
                {{ quantity(item.units_on_hand) }} on hand ·
                {{ item.never_sold ? 'never sold' : `${item.days_since_last_sale} days` }}
              </span>
            </div>
            <span class="tabular text-sm text-ink">
              {{ money(item.cash_tied_up, shop.currency) }}
            </span>
          </div>

          <p class="mt-1 text-sm text-ink">{{ item.headline }}</p>
          <p class="text-sm text-ink-muted">{{ item.why }}</p>

          <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs">
            <template v-if="done.has(item.variant_id)">
              <span class="text-ink-faint">
                Logged {{ shopDate(result(item)?.taken_at ?? null, shop.timezone) }}
                <template v-if="result(item)?.measured_at"> · measured</template>
                <template v-else> · we will check back in 30 days</template>
              </span>
            </template>
            <template v-else>
              <button
                type="button"
                class="rounded-sm bg-brand px-2.5 py-1 font-medium text-white"
                @click="log(item, item.ladder[0]?.price)"
              >
                I did this
              </button>
              <button
                v-if="item.ladder.length"
                type="button"
                class="text-ink-muted hover:text-ink"
                @click="expanded = expanded === item.variant_id ? null : item.variant_id"
              >
                {{ expanded === item.variant_id ? 'Hide' : 'Other prices' }}
              </button>
            </template>
            <span class="text-ink-faint">{{ GRADE[item.kind] }}</span>
          </div>

          <div v-if="expanded === item.variant_id" class="mt-2 border-t border-rule pt-2">
            <p class="mb-1 text-xs text-ink-faint">
              Break-even is {{ money(item.cost, shop.currency) }} a copy.
            </p>
            <div class="flex flex-wrap gap-2">
              <button
                v-for="rung in item.ladder"
                :key="rung.discount"
                type="button"
                class="rounded-sm border px-2.5 py-1 text-xs"
                :class="
                  rung.clears_cost ? 'border-rule text-ink hover:border-rule-strong' : 'border-down text-down'
                "
                @click="log(item, rung.price)"
              >
                {{ Math.round(Number(rung.discount) * 100) }}% off →
                {{ money(rung.price, shop.currency) }}
                <span v-if="rung.margin_per_unit" class="text-ink-faint">
                  ({{ money(rung.margin_per_unit, shop.currency) }} a copy)
                </span>
              </button>
            </div>
          </div>
        </li>
      </ul>
    </PanelCard>
  </div>
</template>
