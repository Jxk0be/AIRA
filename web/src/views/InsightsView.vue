<script setup lang="ts">
/**
 * The inbox: everything the product noticed, in the order worth dealing with.
 *
 * One list, not one per feature. A shop owner does not experience "the reorder
 * assistant" and "the anomaly detector" — they experience five minutes before
 * opening and a question about what to do with them. Ranking is severity then
 * money, and it is decided in the semantic layer so this screen, the Monday
 * email and the assistant all agree about what matters most.
 *
 * Every row can be expanded to its evidence. "Where did that number come
 * from?" is the first thing an owner asks of a figure they disagree with, and
 * an answer that lives only in a log is not an answer.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '../api/client'
import type { Inbox, Insight, ValueLedger } from '../api/types'
import PanelCard from '../components/PanelCard.vue'
import { moneyShort, shopDate, sinceNow } from '../lib/format'
import { useTenantStore } from '../stores/tenant'

const route = useRoute()
const router = useRouter()
const shop = useTenantStore()

const slug = computed(() => String(route.params.tenant ?? ''))
const inbox = ref<Inbox | null>(null)
const ledger = ref<ValueLedger | null>(null)
const loading = ref(true)
const running = ref(false)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)
const status = ref<'open' | 'acted' | 'dismissed'>('open')
const kind = ref<string>('')
const expanded = ref<string | null>(null)

const KIND_WORDS: Record<string, string> = {
  reorder: 'Reordering',
  dead_stock: 'Dead stock',
  sales_anomaly: 'Sales',
  possible_shrink: 'Shrink',
  refund_spike: 'Refunds',
  stale_data: 'Connection',
  impossible_values: 'Data',
  staffing: 'Staffing',
}

const SEVERITY_STYLE: Record<string, string> = {
  urgent: 'border-down text-down',
  warn: 'border-rule-strong text-ink',
  info: 'border-rule text-ink-faint',
}

function kindLabel(value: string) {
  return KIND_WORDS[value] ?? value.replace(/_/g, ' ')
}

async function load() {
  try {
    const [next, value] = await Promise.all([
      api.insights(slug.value, { status: status.value, kind: kind.value || undefined }),
      api.value(slug.value),
    ])
    inbox.value = next
    ledger.value = value
    error.value = null
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

async function checkAgain() {
  running.value = true
  notice.value = null
  try {
    const runs = await api.runDetectors(slug.value)
    const found = runs.reduce((total, run) => total + run.created, 0)
    const failed = runs.filter((run) => run.error)
    notice.value = failed.length
      ? `${failed.length} of ${runs.length} checks could not run: ${failed[0]?.error ?? ''}`
      : found
        ? `${found} new thing${found === 1 ? '' : 's'} to look at.`
        : 'Nothing new since the last check.'
    await load()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    running.value = false
  }
}

/** Acting on a finding takes you to the screen that does the thing. */
async function act(insight: Insight) {
  await decide(insight, 'acted')
  const target = insight.suggested_action.route
  if (target) {
    const [path, search] = target.split('?')
    void router.push({ path: `/${slug.value}/${path}`, query: Object.fromEntries(new URLSearchParams(search ?? '')) })
  }
}

async function decide(insight: Insight, next: 'seen' | 'acted' | 'dismissed' | 'snoozed') {
  try {
    await api.setInsightStatus(slug.value, insight.id, next, next === 'snoozed' ? 7 : undefined)
    await load()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  }
}

async function rate(insight: Insight, useful: boolean) {
  try {
    await api.rateInsight(slug.value, insight.id, useful)
    if (inbox.value) {
      const row = inbox.value.insights.find((item) => item.id === insight.id)
      if (row) row.was_useful = useful
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  }
}

function toggle(insight: Insight) {
  expanded.value = expanded.value === insight.id ? null : insight.id
  if (expanded.value && insight.status === 'new') void decide(insight, 'seen')
}

function evidenceLines(insight: Insight): [string, string][] {
  return Object.entries(insight.evidence)
    .filter(([, value]) => value !== null && typeof value !== 'object')
    .map(([key, value]) => [key.replace(/_/g, ' '), String(value)])
}

function evidenceItems(insight: Insight): Record<string, unknown>[] {
  const items = insight.evidence.items
  return Array.isArray(items) ? (items as Record<string, unknown>[]) : []
}

onMounted(load)
watch([slug, status, kind], load)
</script>

<template>
  <div class="mx-auto max-w-5xl px-4 py-6 lg:px-8">
    <header class="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="display text-2xl font-semibold tracking-tight text-ink">Worth doing</h1>
        <p class="mt-0.5 text-sm text-ink-muted">
          What we noticed since the last sync, most important first.
        </p>
      </div>
      <button
        type="button"
        class="rounded-sm border border-rule px-3 py-1.5 text-sm text-ink hover:border-rule-strong disabled:opacity-50"
        :disabled="running"
        @click="checkAgain"
      >
        {{ running ? 'Checking…' : 'Check again' }}
      </button>
    </header>

    <p v-if="error" class="mb-4 border-l-2 border-down bg-panel px-4 py-3 text-sm text-ink" role="alert">
      {{ error }}
    </p>
    <p v-if="notice" class="mb-4 border-l-2 border-brand bg-panel px-4 py-3 text-sm text-ink">
      {{ notice }}
    </p>

    <div
      v-if="ledger?.has_anything_to_show"
      class="mb-5 flex flex-wrap items-baseline gap-x-6 gap-y-1 border border-rule bg-panel px-4 py-3 text-sm"
    >
      <span class="text-xs tracking-[0.12em] text-ink-faint uppercase">This month</span>
      <span class="text-ink">{{ ledger.insights_acted }} acted on</span>
      <span v-if="Number(ledger.attributed_revenue) > 0" class="text-ink">
        {{ moneyShort(ledger.attributed_revenue, shop.currency) }} of sales we can tie to them
      </span>
      <span v-if="Number(ledger.cash_recovered) > 0" class="text-ink">
        {{ moneyShort(ledger.cash_recovered, shop.currency) }} of stuck stock turned back into cash
      </span>
    </div>

    <div class="mb-4 flex flex-wrap items-center gap-2">
      <label class="sr-only" for="status">Status</label>
      <select
        id="status"
        v-model="status"
        class="rounded-sm border border-rule bg-panel px-2.5 py-1.5 text-sm text-ink"
      >
        <option value="open">Open</option>
        <option value="acted">Acted on</option>
        <option value="dismissed">Dismissed</option>
      </select>

      <label class="sr-only" for="kind">Kind</label>
      <select
        id="kind"
        v-model="kind"
        class="rounded-sm border border-rule bg-panel px-2.5 py-1.5 text-sm text-ink"
      >
        <option value="">Everything</option>
        <option v-for="value in inbox?.kinds ?? []" :key="value" :value="value">
          {{ kindLabel(value) }}
        </option>
      </select>
    </div>

    <PanelCard
      title="Findings"
      :loading="loading"
      :empty="!loading && !inbox?.insights.length"
      empty-text="Nothing open. That is a good sign, not a broken screen."
    >
      <ul class="-my-1 divide-y divide-rule">
        <li v-for="insight in inbox?.insights ?? []" :key="insight.id" class="py-3.5">
          <div class="flex items-start gap-3">
            <span
              class="mt-0.5 shrink-0 rounded-sm border px-1.5 py-0.5 text-[0.62rem] tracking-[0.1em] uppercase"
              :class="SEVERITY_STYLE[insight.severity]"
            >
              {{ kindLabel(insight.kind) }}
            </span>

            <div class="min-w-0 flex-1">
              <button
                type="button"
                class="block w-full text-left"
                :aria-expanded="expanded === insight.id"
                @click="toggle(insight)"
              >
                <span class="flex flex-wrap items-baseline gap-x-2">
                  <span class="font-medium text-ink">{{ insight.title }}</span>
                  <span v-if="insight.dollar_impact" class="tabular text-sm text-ink-muted">
                    {{ moneyShort(insight.dollar_impact, shop.currency) }}
                  </span>
                </span>
                <span class="mt-1 block text-sm leading-relaxed text-ink-muted">
                  {{ insight.summary }}
                </span>
              </button>

              <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs">
                <button
                  v-if="insight.status !== 'acted'"
                  type="button"
                  class="rounded-sm bg-brand px-2.5 py-1 font-medium text-white"
                  @click="act(insight)"
                >
                  {{ insight.suggested_action.label ?? 'Open it' }}
                </button>
                <button
                  v-if="insight.status !== 'dismissed'"
                  type="button"
                  class="text-ink-muted hover:text-ink"
                  @click="decide(insight, 'dismissed')"
                >
                  Dismiss
                </button>
                <button
                  v-if="insight.status !== 'snoozed' && insight.status !== 'dismissed'"
                  type="button"
                  class="text-ink-muted hover:text-ink"
                  @click="decide(insight, 'snoozed')"
                >
                  Snooze a week
                </button>
                <span class="text-ink-faint">{{ sinceNow(insight.created_at) }}</span>

                <span class="ml-auto flex items-center gap-2 text-ink-faint">
                  <template v-if="insight.was_useful === null">
                    Useful?
                    <button type="button" class="hover:text-ink" @click="rate(insight, true)">Yes</button>
                    <button type="button" class="hover:text-ink" @click="rate(insight, false)">No</button>
                  </template>
                  <span v-else>{{ insight.was_useful ? 'Marked useful' : 'Marked not useful' }}</span>
                </span>
              </div>

              <div v-if="expanded === insight.id" class="mt-3 border-t border-rule pt-3">
                <p class="mb-2 text-xs tracking-[0.12em] text-ink-faint uppercase">
                  What this is worked out from
                </p>
                <dl class="grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-3">
                  <div v-for="[label, value] in evidenceLines(insight)" :key="label">
                    <dt class="text-ink-faint">{{ label }}</dt>
                    <dd class="tabular text-ink">{{ value }}</dd>
                  </div>
                </dl>

                <div v-if="evidenceItems(insight).length" class="mt-3 overflow-x-auto">
                  <table class="w-full text-xs">
                    <thead>
                      <tr class="text-left text-ink-faint">
                        <th class="py-1 pr-3 font-normal">Item</th>
                        <th class="py-1 pr-3 font-normal">Why</th>
                      </tr>
                    </thead>
                    <tbody class="divide-y divide-rule">
                      <tr v-for="(item, index) in evidenceItems(insight).slice(0, 10)" :key="index">
                        <td class="py-1 pr-3 text-ink">{{ item.label ?? item.item ?? '—' }}</td>
                        <td class="py-1 pr-3 text-ink-muted">
                          {{ item.why ?? item.headline ?? '' }}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>

                <p class="mt-3 text-xs text-ink-faint">
                  Found on {{ shopDate(insight.as_of, shop.timezone) }}.
                </p>
              </div>
            </div>
          </div>
        </li>
      </ul>
    </PanelCard>
  </div>
</template>
