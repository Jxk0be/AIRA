<script setup lang="ts">
/**
 * Everything the product noticed, in the order worth dealing with.
 *
 * This is the top of Home rather than a screen of its own, because a list of
 * things to do is what the owner opens the app for. What the week *was* comes
 * second.
 *
 * Three fixes from the audit are structural rather than cosmetic:
 *
 *   The severity rail is a fixed width, so every title starts at the same x.
 *   It used to be a shrink-to-fit badge, which meant "SHRINK" and "DEAD STOCK"
 *   pushed their titles to different places and the list read as ragged
 *   (audit U6).
 *
 *   Severity is a word — "Urgent", "Worth a look" — not a colour. The old badge
 *   showed the *kind* and left the ranking to a border colour, so for anyone who
 *   could not see the difference the ordering simply vanished (audit A4).
 *
 *   A failure says so. The dashboard's old three-row preview swallowed its own
 *   errors, so "nothing to do today" and "the inbox is broken" looked identical
 *   (audit U4).
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '../api/client'
import type { Inbox, Insight } from '../api/types'
import { moneyShort, shopDate, sinceNow } from '../lib/format'
import { useTenantStore } from '../stores/tenant'
import UiBadge from '../ui/UiBadge.vue'
import UiButton from '../ui/UiButton.vue'
import UiSkeleton from '../ui/UiSkeleton.vue'
import { toast, withToast } from '../ui/toast'

const props = defineProps<{ tenant: string }>()

const router = useRouter()
const shop = useTenantStore()

const inbox = ref<Inbox | null>(null)
const loading = ref(true)
const running = ref(false)
const error = ref<string | null>(null)
const status = ref<'open' | 'acted' | 'dismissed'>('open')
const kind = ref('')
const expanded = ref<string | null>(null)

/** Plain words for what the detectors call things (audit U19). */
const KIND_WORDS: Record<string, string> = {
  reorder: 'Reordering',
  dead_stock: 'Not selling',
  sales_anomaly: 'Sales',
  possible_shrink: 'Stock going missing',
  refund_spike: 'Refunds',
  stale_data: 'Connection',
  impossible_values: 'Data',
  staffing: 'Staffing',
}

const SEVERITY: Record<string, { word: string; tone: 'danger' | 'warning' | 'info' }> = {
  urgent: { word: 'Urgent', tone: 'danger' },
  warn: { word: 'Worth a look', tone: 'warning' },
  info: { word: 'For information', tone: 'info' },
}

const kindLabel = (value: string) => KIND_WORDS[value] ?? value.replace(/_/g, ' ')
const severity = (value: string) => SEVERITY[value] ?? SEVERITY.info!

const shown = computed(() => inbox.value?.insights ?? [])

async function load() {
  try {
    inbox.value = await api.insights(props.tenant, {
      status: status.value,
      kind: kind.value || undefined,
    })
    error.value = null
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

async function checkAgain() {
  running.value = true
  try {
    const runs = await api.runDetectors(props.tenant)
    const found = runs.reduce((total, run) => total + run.created, 0)
    const failed = runs.filter((run) => run.error)
    if (failed.length) {
      toast.warning(`${failed.length} of ${runs.length} checks could not run`, {
        detail: failed[0]?.error ?? undefined,
      })
    } else {
      toast.success(
        found ? `${found} new thing${found === 1 ? '' : 's'} to look at` : 'Nothing new since the last check',
      )
    }
    await load()
  } catch (cause) {
    toast.danger('Could not run the checks', {
      detail: cause instanceof Error ? cause.message : undefined,
      action: { label: 'Try again', run: () => void checkAgain() },
    })
  } finally {
    running.value = false
  }
}

/** Acting on a finding takes you to the screen that does the thing. */
async function act(insight: Insight) {
  await decide(insight, 'acted', `Marked “${insight.title}” as done`)
  const target = insight.suggested_action.route
  if (typeof target === 'string' && target) {
    const [path, search] = target.split('?')
    void router.push({
      path: `/${props.tenant}/${path}`,
      query: Object.fromEntries(new URLSearchParams(search ?? '')),
    })
  }
}

/** Only the four a person can choose; `new` and `expired` are the API's. */
type Decision = 'seen' | 'acted' | 'dismissed' | 'snoozed'

async function decide(insight: Insight, next: Decision, said: string) {
  const done = await withToast(
    () => api.setInsightStatus(props.tenant, insight.id, next, next === 'snoozed' ? 7 : undefined),
    { success: said, failure: 'That did not save' },
  )
  if (done !== undefined) await load()
}

async function rate(insight: Insight, useful: boolean) {
  try {
    await api.rateInsight(props.tenant, insight.id, useful)
    const row = inbox.value?.insights.find((item) => item.id === insight.id)
    if (row) row.was_useful = useful
    toast.success(useful ? 'Thanks — more like this' : 'Thanks — fewer like this')
  } catch {
    toast.danger('Could not save that')
  }
}

function toggle(insight: Insight) {
  expanded.value = expanded.value === insight.id ? null : insight.id
  if (expanded.value && insight.status === 'new') void decide(insight, 'seen', '')
}

function evidenceLines(insight: Insight): [string, string][] {
  return Object.entries(insight.evidence)
    .filter(([, value]) => value !== null && typeof value !== 'object')
    .map(([key, value]) => [key.replace(/_/g, ' '), String(value)])
}

onMounted(load)
watch([() => props.tenant, status, kind], load)
</script>

<template>
  <section class="min-w-0" aria-labelledby="worth-doing-heading" :aria-busy="loading">
    <header class="mb-3 flex flex-wrap items-center justify-between gap-3">
      <h2 id="worth-doing-heading" class="text-xl font-bold text-ink">Worth doing</h2>
      <UiButton size="sm" variant="secondary" :loading="running" @click="checkAgain">
        {{ running ? 'Checking' : 'Check again' }}
      </UiButton>
    </header>

    <div class="mb-3 flex flex-wrap gap-2">
      <label class="min-w-0">
        <span class="sr-only">Show</span>
        <select
          v-model="status"
          class="min-h-11 rounded-md border border-border-strong bg-surface px-3 text-base text-ink"
        >
          <option value="open">Still to do</option>
          <option value="acted">Done</option>
          <option value="dismissed">Dismissed</option>
        </select>
      </label>
      <label class="min-w-0">
        <span class="sr-only">Kind</span>
        <select
          v-model="kind"
          class="min-h-11 rounded-md border border-border-strong bg-surface px-3 text-base text-ink"
        >
          <option value="">Everything</option>
          <option v-for="value in inbox?.kinds ?? []" :key="value" :value="value">
            {{ kindLabel(value) }}
          </option>
        </select>
      </label>
    </div>

    <!-- A broken inbox must not look like a quiet one (audit U4). -->
    <div v-if="error" class="rounded-lg border border-danger bg-danger-subtle p-4" role="alert">
      <p class="font-medium text-ink">We could not load what needs doing.</p>
      <p class="mt-1 text-sm text-ink-muted">{{ error }}</p>
      <UiButton class="mt-3" size="sm" variant="secondary" @click="load">Try again</UiButton>
    </div>

    <div v-else-if="loading" class="rounded-lg border border-border bg-surface p-4">
      <UiSkeleton :lines="4" />
    </div>

    <div
      v-else-if="!shown.length"
      class="rounded-lg border border-border bg-surface px-4 py-8 text-center"
    >
      <p class="text-base font-medium text-ink">
        {{ status === 'open' ? 'Nothing to do right now.' : 'Nothing here.' }}
      </p>
      <p class="mt-1 text-sm text-ink-muted">
        {{
          status === 'open'
            ? 'That is a good sign, not a broken screen.'
            : 'Findings you deal with show up here.'
        }}
      </p>
    </div>

    <ul v-else class="space-y-3">
      <li
        v-for="insight in shown"
        :key="insight.id"
        class="rounded-lg border border-border bg-surface"
      >
        <div class="p-4 sm:flex sm:gap-4">
          <!--
            The rail is desktop-only, and that is a deliberate split rather than
            a responsive afterthought.

            A fixed-width severity column is what makes every title start at the
            same x, which is the whole point of audit U6 — but it only pays for
            itself when the eye is scanning a column of rows. At 375px it costs
            104px of a 375px screen, and the titles wrap to four lines to pay
            for an alignment nobody can see. So on a phone the severity sits
            above the title, where it reads as a label; from `sm` up it becomes
            the rail.
          -->
          <div
            class="mb-2 flex items-center gap-2 sm:mb-0 sm:block sm:w-32 sm:shrink-0"
          >
            <UiBadge :tone="severity(insight.severity).tone">
              {{ severity(insight.severity).word }}
            </UiBadge>
            <p class="text-xs text-ink-muted sm:mt-1.5">{{ kindLabel(insight.kind) }}</p>
          </div>

          <div class="min-w-0 flex-1">
            <button
              type="button"
              class="block w-full rounded-sm text-left"
              :aria-expanded="expanded === insight.id"
              @click="toggle(insight)"
            >
              <!-- No wrapping. With `flex-wrap`, a title long enough to take two
                   lines pushed the amount onto a third and left-aligned it, so
                   the money column stopped being a column — which is the
                   alignment complaint all over again. -->
              <span class="flex items-baseline gap-3">
                <span class="min-w-0 flex-1 text-base font-semibold text-ink">
                  {{ insight.title }}
                </span>
                <span
                  v-if="insight.dollar_impact"
                  class="tabular shrink-0 font-semibold text-ink"
                >
                  {{ moneyShort(insight.dollar_impact, shop.currency) }}
                </span>
              </span>
              <span class="mt-1 block text-base leading-relaxed text-ink-muted">
                {{ insight.summary }}
              </span>
            </button>

            <div class="mt-3 flex flex-wrap items-center gap-2">
              <UiButton
                v-if="insight.status !== 'acted'"
                size="sm"
                @click="act(insight)"
              >
                {{ insight.suggested_action.label ?? 'Open it' }}
              </UiButton>
              <UiButton
                v-if="insight.status !== 'dismissed'"
                size="sm"
                variant="ghost"
                @click="decide(insight, 'dismissed', 'Dismissed')"
              >
                Dismiss
              </UiButton>
              <UiButton
                v-if="insight.status !== 'snoozed' && insight.status !== 'dismissed'"
                size="sm"
                variant="ghost"
                @click="decide(insight, 'snoozed', 'Back in a week')"
              >
                Snooze a week
              </UiButton>
              <span class="tabular ml-auto text-sm text-ink-muted">
                {{ sinceNow(insight.created_at) }}
              </span>
            </div>

            <div v-if="expanded === insight.id" class="mt-3 border-t border-border pt-3">
              <p class="mb-2 text-sm font-semibold text-ink">What this is worked out from</p>
              <dl class="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
                <div v-for="[label, value] in evidenceLines(insight)" :key="label">
                  <dt class="text-ink-muted">{{ label }}</dt>
                  <dd class="tabular text-ink">{{ value }}</dd>
                </div>
              </dl>
              <p class="mt-3 text-sm text-ink-muted">
                Found on {{ shopDate(insight.as_of, shop.timezone) }}.
              </p>
              <div class="mt-3 flex flex-wrap items-center gap-2 text-sm">
                <template v-if="insight.was_useful === null">
                  <span class="text-ink-muted">Was this useful?</span>
                  <UiButton size="sm" variant="ghost" @click="rate(insight, true)">Yes</UiButton>
                  <UiButton size="sm" variant="ghost" @click="rate(insight, false)">No</UiButton>
                </template>
                <span v-else class="text-ink-muted">
                  {{ insight.was_useful ? 'Marked useful' : 'Marked not useful' }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </li>
    </ul>
  </section>
</template>
