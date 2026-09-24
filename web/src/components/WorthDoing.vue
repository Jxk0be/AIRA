<script setup lang="ts">
/**
 * The top of the inbox, on the dashboard, plus what the month has been worth.
 *
 * Two claims, kept apart on purpose. "Acted on, and here is the money we can
 * tie to it" is a fact. "Flagged" is what the open findings say is still
 * available, which is a claim about the future — it is never added to the
 * first, and it is worded so nobody could read it as money already made.
 *
 * The whole block hides itself when there is nothing honest to say. A card
 * reading "$0 delivered this month" is worse than no card.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { api } from '../api/client'
import type { Insight, ValueLedger } from '../api/types'
import { moneyShort } from '../lib/format'

const props = defineProps<{ tenant: string; currency: string }>()

const actions = ref<Insight[]>([])
const ledger = ref<ValueLedger | null>(null)
const loaded = ref(false)

const worthShowing = computed(() => actions.value.length > 0 || ledger.value?.has_anything_to_show)

async function load() {
  if (!props.tenant) return
  try {
    const [inbox, value] = await Promise.all([
      api.insights(props.tenant, { status: 'open', limit: 3 }),
      api.value(props.tenant),
    ])
    actions.value = inbox.insights
    ledger.value = value
  } catch {
    // The dashboard is not the place to report that the inbox is unreachable;
    // the inbox itself says so plainly when you open it.
    actions.value = []
    ledger.value = null
  } finally {
    loaded.value = true
  }
}

onMounted(load)
watch(() => props.tenant, load)
</script>

<template>
  <section v-if="loaded && worthShowing" class="border border-rule bg-panel">
    <header
      class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-rule px-4 py-2.5"
    >
      <h2 class="text-sm font-semibold tracking-tight text-ink">Worth doing</h2>
      <RouterLink
        :to="{ name: 'insights', params: { tenant: tenant } }"
        class="text-xs text-brand"
      >
        See everything
      </RouterLink>
    </header>

    <ul v-if="actions.length" class="divide-y divide-rule">
      <li v-for="insight in actions" :key="insight.id" class="px-4 py-2.5">
        <div class="flex flex-wrap items-baseline justify-between gap-x-3">
          <span class="text-sm font-medium text-ink">{{ insight.title }}</span>
          <span v-if="insight.dollar_impact" class="tabular text-sm text-ink-muted">
            {{ moneyShort(insight.dollar_impact, currency) }}
          </span>
        </div>
        <p class="mt-0.5 line-clamp-2 text-xs leading-relaxed text-ink-muted">
          {{ insight.summary }}
        </p>
      </li>
    </ul>

    <footer
      v-if="ledger?.has_anything_to_show"
      class="flex flex-wrap items-baseline gap-x-5 gap-y-1 border-t border-rule px-4 py-2.5 text-xs"
    >
      <span class="tracking-[0.12em] text-ink-faint uppercase">This month</span>
      <span class="text-ink">{{ ledger.insights_acted }} acted on</span>
      <span v-if="Number(ledger.attributed_revenue) > 0" class="text-ink">
        {{ moneyShort(ledger.attributed_revenue, currency) }} of sales we can tie to them
      </span>
      <span v-if="Number(ledger.cash_recovered) > 0" class="text-ink">
        {{ moneyShort(ledger.cash_recovered, currency) }} of stuck stock turned back into cash
      </span>
    </footer>
  </section>
</template>
