<script setup lang="ts">
/**
 * Where the numbers come from, and how well they came through.
 *
 * This is the only screen that admits a platform exists, and the only one whose
 * findings are jobs rather than facts: "112 items have no cost, so margin
 * covers 88% of sales" is something the owner can go and fix in their own POS,
 * and fixing it improves every other screen. That is why the report is written
 * in their words and not in ours.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../api/client'
import type { DataScreen, JobRun, JobsScreen, SyncRun } from '../api/types'
import SectionCard from '../components/SectionCard.vue'
import UiButton from '../ui/UiButton.vue'
import { toast } from '../ui/toast'
import { count, duration, percent, shopDateTime, sinceNow } from '../lib/format'

const route = useRoute()

const slug = computed(() => String(route.params.tenant ?? ''))
const screen = ref<DataScreen | null>(null)
const jobs = ref<JobsScreen | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

let poll: ReturnType<typeof setInterval> | undefined

async function load() {
  try {
    // The worker is a separate process, and this screen has to be readable
    // when it is not running at all. A jobs call that fails leaves the rest of
    // the page intact and shows its own empty state.
    const [data, rounds] = await Promise.all([
      api.data(slug.value),
      api.jobs(slug.value).catch(() => null),
    ])
    screen.value = data
    jobs.value = rounds
    error.value = null
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

/** While a sync is running, keep asking: it finishes on its own schedule. */
function watchSync() {
  clearInterval(poll)
  poll = setInterval(async () => {
    await load()
    if (!screen.value?.syncing) clearInterval(poll)
  }, 2000)
}

async function syncNow(mode: 'incremental' | 'backfill') {
  try {
    const started = await api.startSync(slug.value, mode)
    if (started.started) {
      toast.success(started.detail)
      await load()
      watchSync()
    } else {
      toast.warning(started.detail)
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  }
}


onMounted(load)
watch(slug, load)
onBeforeUnmount(() => clearInterval(poll))

/** What one sync actually brought in, as a sentence rather than a blob. */
function broughtIn(run: SyncRun): string {
  const parts = Object.entries(run.counts)
    .map(([entity, counts]) => [entity, counts?.upserted ?? 0] as const)
    .filter(([, upserted]) => upserted > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
    .map(([entity, upserted]) => `${count(upserted)} ${entity.replace(/_/g, ' ')}`)
  return parts.length ? parts.join(', ') : 'nothing changed'
}

/** What a job round actually did, in the worker's own terms. */
function jobDetail(run: JobRun): string {
  if (run.error) return run.error
  const parts = Object.entries(run.detail)
    // Scalars only. The detectors job also reports a per-detector breakdown,
    // which is a whole paragraph and belongs on Worth doing, not in a
    // one-line history row.
    .filter(([, value]) => value !== null && value !== 0 && value !== '')
    .filter(([, value]) => typeof value !== 'object')
    .map(([key, value]) => `${key.replace(/_/g, ' ')} ${String(value)}`)
  return parts.length ? parts.join(' · ') : 'nothing to do'
}

const jobStatusClass: Record<string, string> = {
  succeeded: 'text-ink-muted',
  skipped: 'text-ink-muted',
  running: 'text-primary',
  failed: 'text-danger',
}

/**
 * The owner's words for our internal keys.
 *
 * Everything on this screen came from a slug — `has_online_channel`,
 * `month_end_packet`, a JSON blob — and a slug with the underscores taken out
 * is still a slug (audit U19).
 */
const CAPABILITY_WORDS: Record<string, string> = {
  has_costs: 'Knows what things cost',
  has_customers: 'Knows who bought',
  has_inventory_history: 'Keeps stock history',
  multi_location: 'More than one location',
  has_online_channel: 'Sells online as well as in store',
  supports_incremental: 'Can send just what changed',
}

const JOB_WORDS: Record<string, string> = {
  sync: 'Reading from the till system',
  incremental_sync: 'Reading what changed',
  backfill: 're-reading everything',
  detectors: 'Looking for things worth doing',
  month_end_packet: "Building the bookkeeper's packet",
  weekly_digest: "Writing Monday's email",
  ingest: 'Indexing documents',
  outcomes: 'Checking what the advice was worth',
}

const capabilityWords = (key: string) =>
  CAPABILITY_WORDS[key] ?? key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

const jobWords = (key: string) =>
  JOB_WORDS[key] ?? key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

/** A connection's settings as a sentence, not `key=value · key=value`. */
function describeConfig(config: Record<string, unknown>): string {
  const entries = Object.entries(config ?? {})
  if (!entries.length) return 'No extra settings.'
  const parts = entries.map(([key, value]) => {
    const words = key.replace(/_/g, ' ')
    return Array.isArray(value) ? `${words}: ${value.join(', ')}` : `${words}: ${String(value)}`
  })
  return parts.join(' · ')
}

/** One sync fault, in a sentence the owner could act on. */
function faultSentence(fault: unknown): string {
  if (typeof fault === 'string') return fault
  if (fault && typeof fault === 'object') {
    const row = fault as Record<string, unknown>
    const what = typeof row.entity === 'string' ? row.entity.replace(/_/g, ' ') : 'some records'
    const why = typeof row.message === 'string' ? row.message : typeof row.code === 'string' ? row.code : null
    return why ? `${what} did not come through: ${why}` : `${what} did not come through.`
  }
  return 'Something did not come through.'
}

const severityClass: Record<string, string> = {
  error: 'border-danger text-ink',
  warning: 'border-warning text-ink',
  info: 'border-border text-ink-muted',
}
</script>

<template>
  <div class="mx-auto max-w-4xl px-4 py-5 sm:px-6 lg:py-8">
    <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <p class="text-sm text-ink-muted">Everything on the other screens is read from here.</p>
      <div class="flex items-center gap-2">
        <UiButton :loading="screen?.syncing" @click="syncNow('incremental')">
          {{ screen?.syncing ? 'Syncing' : 'Sync now' }}
        </UiButton>
        <UiButton variant="secondary" :disabled="screen?.syncing" @click="syncNow('backfill')">
          Re-read everything
        </UiButton>
      </div>
    </div>

    <div v-if="error" class="mb-4 rounded-lg border border-danger bg-danger-subtle p-4" role="alert">
      <p class="font-medium text-ink">We could not read the sync status.</p>
      <p class="mt-1 text-sm text-ink-muted">{{ error }}</p>
      <UiButton class="mt-3" size="sm" variant="secondary" @click="load">Try again</UiButton>
    </div>

    <div class="grid gap-4 lg:grid-cols-2">
      <SectionCard title="Connected system" :loading="loading">
        <div v-for="integration in screen?.integrations ?? []" :key="integration.id" class="text-sm">
          <p class="flex items-baseline justify-between gap-3">
            <span class="font-medium text-ink">{{ integration.adapter }}</span>
            <span class="text-sm" :class="integration.is_active ? 'text-success' : 'text-ink-muted'">
              {{ integration.is_active ? 'active' : 'paused' }}
            </span>
          </p>
          <p class="mt-1 text-sm text-ink-muted">{{ describeConfig(integration.config) }}</p>

          <ul class="mt-3 space-y-0.5">
            <li
              v-for="(able, capability) in integration.capabilities"
              :key="capability"
              class="flex items-baseline gap-2 text-sm"
            >
              <span :class="able ? 'text-success' : 'text-ink-muted'">{{ able ? '✓' : '·' }}</span>
              <span :class="able ? 'text-ink' : 'text-ink-muted'">
                {{ capabilityWords(String(capability)) }}
              </span>
            </li>
          </ul>
        </div>
        <p v-if="!screen?.integrations.length" class="text-sm text-ink-muted">
          No system is connected to this shop yet.
        </p>
      </SectionCard>

      <SectionCard
        title="Last sync"
        :subtitle="screen?.last_sync ? sinceNow(screen.last_sync.started_at) : ''"
        :loading="loading"
        :empty="!screen?.last_sync"
        empty-text="This shop has never been synced."
      >
        <div v-if="screen?.last_sync" class="text-sm">
          <p class="flex items-baseline gap-2">
            <span
              class="inline-block h-1.5 w-1.5 rounded-full"
              :class="{
                'bg-up': screen.last_sync.status === 'succeeded',
                'bg-down': screen.last_sync.status === 'failed',
                'bg-primary working': screen.last_sync.status === 'running',
              }"
              aria-hidden="true"
            />
            <span class="text-ink">{{ screen.last_sync.mode }} · {{ screen.last_sync.status }}</span>
            <span class="tabular ml-auto text-sm text-ink-muted">
              {{ duration(screen.last_sync.duration_ms) }}
            </span>
          </p>
          <p class="mt-1.5 text-sm text-ink-muted">{{ broughtIn(screen.last_sync) }}</p>
          <!-- A sync fault used to be printed as raw JSON. The owner cannot
               act on `{"entity":"orders","code":"429"}`; they can act on a
               sentence naming what did not come through (audit U19). -->
          <p
            v-for="(fault, index) in screen.last_sync.errors"
            :key="index"
            class="mt-1 text-sm text-danger"
          >
            {{ faultSentence(fault) }}
          </p>
        </div>
      </SectionCard>

      <SectionCard
        class="lg:col-span-2"
        title="What we had to work with"
        :subtitle="screen?.quality ? `checked ${sinceNow(screen.quality.generated_at)}` : ''"
        :loading="loading"
        :empty="!screen?.quality?.findings.length"
        empty-text="Nothing to flag — the data came through clean."
      >
        <ul class="space-y-2">
          <li
            v-for="finding in screen?.quality?.findings ?? []"
            :key="finding.code"
            class="border-l-2 pl-3"
            :class="severityClass[finding.severity] ?? severityClass.info"
          >
            <p class="text-sm leading-snug">{{ finding.message }}</p>
            <p v-if="finding.share !== null" class="tabular mt-0.5 text-sm text-ink-muted">
              {{ percent(finding.share, 0) }} · {{ count(finding.count) }} affected
            </p>
          </li>
        </ul>
      </SectionCard>

      <SectionCard title="Sync history" :loading="loading" :empty="!screen?.history.length">
        <ol class="space-y-1.5">
          <li
            v-for="run in screen?.history ?? []"
            :key="run.id"
            class="flex items-baseline justify-between gap-3 text-sm"
          >
            <span class="tabular shrink-0 text-ink-muted">
              {{ shopDateTime(run.started_at, screen?.timezone ?? 'UTC') }}
            </span>
            <span class="min-w-0 flex-1 truncate text-ink-muted">{{ broughtIn(run) }}</span>
            <span
              class="tabular shrink-0"
              :class="run.status === 'succeeded' ? 'text-ink-muted' : 'text-danger'"
            >
              {{ run.status === 'succeeded' ? duration(run.duration_ms) : run.status }}
            </span>
          </li>
        </ol>
      </SectionCard>

      <SectionCard
        class="lg:col-span-2"
        title="What runs on a schedule"
        subtitle="Reading, watching and writing, without you asking"
        :loading="loading"
        :empty="!jobs?.schedules.length"
        empty-text="Nothing is scheduled yet."
      >
        <ul class="space-y-1.5">
          <li
            v-for="plan in jobs?.schedules ?? []"
            :key="plan.job"
            class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-sm"
          >
            <span class="font-medium text-ink">{{ jobWords(plan.job) }}</span>
            <span class="min-w-0 flex-1 truncate text-sm text-ink-muted">{{ plan.when }}</span>
            <span
              class="tabular shrink-0 text-sm"
              :class="plan.overdue ? 'text-danger' : 'text-ink-muted'"
            >
              {{
                plan.last_succeeded_at
                  ? `ran ${sinceNow(plan.last_succeeded_at)}`
                  : 'has never run'
              }}
            </span>
          </li>
        </ul>
        <p v-if="jobs?.schedules.some((plan) => plan.overdue)" class="mt-3 text-sm text-ink-muted">
          Anything shown in red has not run for the last moment it was due. We pick one up late
          where that is still worth doing — an hourly read missed by a few hours, a month-end packet
          by a few days — and let it go where it is not, rather than sending you last Monday's email
          on a Thursday.
        </p>
      </SectionCard>

      <SectionCard
        class="lg:col-span-2"
        title="What has run"
        :loading="loading"
        :empty="!jobs?.runs.length"
        empty-text="Nothing has run for this shop yet."
      >
        <ol class="space-y-1.5">
          <li
            v-for="run in jobs?.runs ?? []"
            :key="run.id"
            class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-sm"
          >
            <span class="tabular shrink-0 text-ink-muted">
              {{ shopDateTime(run.started_at, jobs?.timezone ?? 'UTC') }}
            </span>
            <span class="shrink-0 font-medium text-ink">{{ jobWords(run.job) }}</span>
            <span
              class="min-w-0 flex-1 truncate"
              :class="run.error ? 'text-danger' : 'text-ink-muted'"
            >
              {{ jobDetail(run) }}
            </span>
            <span class="tabular shrink-0" :class="jobStatusClass[run.status] ?? 'text-ink-muted'">
              {{ run.status === 'succeeded' ? duration(run.duration_ms) : run.status }}
            </span>
          </li>
        </ol>
      </SectionCard>
</div>
</div>
</template>
