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
import SectionCard from '../components/SectionCard.vue'
import { useBusy } from '../lib/busy'
import UiButton from '../ui/UiButton.vue'
import { withToast } from '../ui/toast'
import { money, moneyShort, percent, quantity, shopDate } from '../lib/format'
import { useTenantStore } from '../stores/tenant'

const route = useRoute()
const shop = useTenantStore()

const slug = computed(() => String(route.params.tenant ?? ''))
const screen = ref<DeadStockScreen | null>(null)
const actions = ref<RescueAction[]>([])
const loading = ref(true)
// Keyed by item: these buttons are one per row, and a shared flag would spin
// the whole list. Without any state at all — which is what this had — a slow
// POST looks like a dead button and gets pressed twice.
const logging = useBusy()
const error = ref<string | null>(null)
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
  const done = await logging.run(item.variant_id, () =>
    withToast(
      () =>
        api.logRescue(slug.value, {
          variant_id: item.variant_id,
          kind: item.play,
          detail: item.detail,
          price_after: priceAfter ?? null,
        }),
      {
        success: `Logged. We will check back on ${item.label} in 30 days`,
        failure: 'Could not log that',
      },
    ),
  )
  if (done !== undefined) await load()
}

function result(item: StaleItem) {
  return actions.value.find((row) => row.variant_id === item.variant_id) ?? null
}

onMounted(load)
watch(slug, load)
</script>

<template>
  <div class="mx-auto max-w-5xl px-4 py-6 lg:px-8">
    <p class="mb-4 text-sm text-ink-muted">
      Money sitting still, worst first, with something specific to do about each one.
    </p>

    <p v-if="error" class="mb-4 rounded-lg border border-danger bg-danger-subtle px-4 py-3 text-base text-ink" role="alert">
      {{ error }}
    </p>

    <div
      v-if="screen"
      class="mb-4 flex flex-wrap items-baseline gap-x-6 gap-y-1 rounded-lg border border-border bg-surface px-4 py-3"
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
      <span v-if="screen.cost_coverage" class="ml-auto text-sm text-ink-muted">
        {{ percent(screen.cost_coverage, 0) }} valued at cost, the rest at retail
      </span>
    </div>

    <SectionCard
      title="Worth starting on"
      :subtitle="screen ? `As of ${shopDate(screen.as_of, shop.timezone)}` : undefined"
      :loading="loading"
      :empty="!loading && !screen?.items.length"
      empty-text="Nothing is sitting still. Unusual, and good."
    >
      <ul class="-my-1 divide-y divide-border">
        <li v-for="item in screen?.items ?? []" :key="item.variant_id" class="py-3.5">
          <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <div class="min-w-0">
              <span class="text-base font-semibold text-ink">{{ item.label }}</span>
              <span class="ml-2 text-sm text-ink-muted">
                {{ quantity(item.units_on_hand) }} on hand ·
                {{ item.never_sold ? 'never sold' : `${item.days_since_last_sale} days` }}
              </span>
            </div>
            <span class="tabular text-base font-semibold text-ink">
              {{ money(item.cash_tied_up, shop.currency) }}
            </span>
          </div>

          <p class="mt-1 text-base text-ink">{{ item.headline }}</p>
          <p class="text-base text-ink-muted">{{ item.why }}</p>

          <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm">
            <template v-if="done.has(item.variant_id)">
              <span class="text-ink-muted">
                Logged {{ shopDate(result(item)?.taken_at ?? null, shop.timezone) }}
                <template v-if="result(item)?.measured_at"> · measured</template>
                <template v-else> · we will check back in 30 days</template>
              </span>
            </template>
            <template v-else>
              <UiButton
                size="sm"
                :loading="logging.busy(item.variant_id)"
                :disabled="logging.anyBusy.value"
                @click="log(item, item.ladder[0]?.price)"
              >
                I did this
              </UiButton>
              <UiButton
                v-if="item.ladder.length"
                size="sm"
                variant="ghost"
                @click="expanded = expanded === item.variant_id ? null : item.variant_id"
              >
                {{ expanded === item.variant_id ? 'Hide' : 'Other prices' }}
              </UiButton>
            </template>
            <span class="text-ink-muted">{{ GRADE[item.kind] }}</span>
          </div>

          <div v-if="expanded === item.variant_id" class="mt-2 border-t border-border pt-2">
            <p class="mb-1 text-sm text-ink-muted">
              Break-even is {{ money(item.cost, shop.currency) }} a copy.
            </p>
            <div class="flex flex-wrap gap-2">
              <button
                v-for="rung in item.ladder"
                :key="rung.discount"
                type="button"
                class="min-h-11 rounded-md border px-3 text-sm"
                :class="
                  rung.clears_cost
                    ? 'border-border-strong text-ink hover:bg-raised'
                    : 'border-danger text-ink'
                "
                @click="log(item, rung.price)"
              >
                {{ Math.round(Number(rung.discount) * 100) }}% off →
                {{ money(rung.price, shop.currency) }}
                <span v-if="rung.margin_per_unit" class="text-ink-muted">
                  ({{ money(rung.margin_per_unit, shop.currency) }} a copy)
                </span>
                <!-- Below cost is a word, not just a red border (audit A4). -->
                <span v-if="!rung.clears_cost" class="font-semibold text-danger">below cost</span>
              </button>
            </div>
          </div>
        </li>
      </ul>
    </SectionCard>
  </div>
</template>
