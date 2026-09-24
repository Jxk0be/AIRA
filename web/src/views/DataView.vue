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
import type { DataScreen, SyncRun } from '../api/types'
import PanelCard from '../components/PanelCard.vue'
import { count, duration, percent, shopDateTime, sinceNow } from '../lib/format'

const route = useRoute()

const slug = computed(() => String(route.params.tenant ?? ''))
const screen = ref<DataScreen | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

let poll: ReturnType<typeof setInterval> | undefined

async function load() {
  try {
    screen.value = await api.data(slug.value)
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
  notice.value = null
  try {
    const started = await api.startSync(slug.value, mode)
    notice.value = started.detail
    if (started.started) {
      await load()
      watchSync()
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  }
}

async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  uploading.value = true
  notice.value = null
  try {
    const result = await api.uploadDocument(slug.value, file)
    notice.value = `Added "${result.title}" — ${result.chunks_embedded} passages indexed.`
    await load()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    uploading.value = false
    if (fileInput.value) fileInput.value.value = ''
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

const severityClass: Record<string, string> = {
  error: 'border-down text-ink',
  warning: 'border-note-rule text-ink',
  info: 'border-rule text-ink-muted',
}
</script>

<template>
  <div class="mx-auto max-w-4xl px-4 py-5 sm:px-6 lg:py-8">
    <header class="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="display text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
          Data &amp; sync
        </h1>
        <p class="mt-0.5 text-xs text-ink-faint">
          Everything on the other screens is read from here.
        </p>
      </div>
      <div class="flex items-center gap-2">
        <button
          type="button"
          class="bg-brand px-3 py-1.5 text-sm font-medium text-paper disabled:opacity-40"
          :disabled="screen?.syncing"
          @click="syncNow('incremental')"
        >
          {{ screen?.syncing ? 'Syncing…' : 'Sync now' }}
        </button>
        <button
          type="button"
          class="border border-rule bg-panel px-3 py-1.5 text-sm text-ink-muted hover:bg-sunk disabled:opacity-40"
          :disabled="screen?.syncing"
          @click="syncNow('backfill')"
        >
          Re-read everything
        </button>
      </div>
    </header>

    <p v-if="error" class="mb-4 border-l-2 border-down bg-panel px-4 py-3 text-sm" role="alert">
      {{ error }}
    </p>
    <p v-if="notice" class="mb-4 border-l-2 border-brand bg-panel px-4 py-2 text-sm text-ink-muted">
      {{ notice }}
    </p>

    <div class="grid gap-4 lg:grid-cols-2">
      <PanelCard title="Connected system" :loading="loading">
        <div v-for="integration in screen?.integrations ?? []" :key="integration.id" class="text-sm">
          <p class="flex items-baseline justify-between gap-3">
            <span class="font-medium text-ink">{{ integration.adapter }}</span>
            <span class="text-xs" :class="integration.is_active ? 'text-up' : 'text-ink-faint'">
              {{ integration.is_active ? 'active' : 'paused' }}
            </span>
          </p>
          <p class="tabular mt-1 text-xs break-all text-ink-faint">
            {{
              Object.entries(integration.config)
                .map(([key, value]) => `${key}=${value}`)
                .join(' · ') || 'no configuration'
            }}
          </p>

          <ul class="mt-3 space-y-0.5">
            <li
              v-for="(able, capability) in integration.capabilities"
              :key="capability"
              class="flex items-baseline gap-2 text-xs"
            >
              <span :class="able ? 'text-up' : 'text-ink-faint'">{{ able ? '✓' : '·' }}</span>
              <span :class="able ? 'text-ink-muted' : 'text-ink-faint'">
                {{ String(capability).replace(/_/g, ' ') }}
              </span>
            </li>
          </ul>
        </div>
        <p v-if="!screen?.integrations.length" class="text-sm text-ink-faint">
          No system is connected to this shop yet.
        </p>
      </PanelCard>

      <PanelCard
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
                'bg-brand working': screen.last_sync.status === 'running',
              }"
              aria-hidden="true"
            />
            <span class="text-ink">{{ screen.last_sync.mode }} · {{ screen.last_sync.status }}</span>
            <span class="tabular ml-auto text-xs text-ink-faint">
              {{ duration(screen.last_sync.duration_ms) }}
            </span>
          </p>
          <p class="mt-1.5 text-xs text-ink-muted">{{ broughtIn(screen.last_sync) }}</p>
          <p
            v-for="(fault, index) in screen.last_sync.errors"
            :key="index"
            class="mt-1 text-xs text-down"
          >
            {{ JSON.stringify(fault) }}
          </p>
        </div>
      </PanelCard>

      <PanelCard
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
            <p v-if="finding.share !== null" class="tabular mt-0.5 text-xs text-ink-faint">
              {{ percent(finding.share, 0) }} · {{ count(finding.count) }} affected
            </p>
          </li>
        </ul>
      </PanelCard>

      <PanelCard title="Sync history" :loading="loading" :empty="!screen?.history.length">
        <ol class="space-y-1.5">
          <li
            v-for="run in screen?.history ?? []"
            :key="run.id"
            class="flex items-baseline justify-between gap-3 text-xs"
          >
            <span class="tabular shrink-0 text-ink-faint">
              {{ shopDateTime(run.started_at, screen?.timezone ?? 'UTC') }}
            </span>
            <span class="min-w-0 flex-1 truncate text-ink-muted">{{ broughtIn(run) }}</span>
            <span
              class="tabular shrink-0"
              :class="run.status === 'succeeded' ? 'text-ink-faint' : 'text-down'"
            >
              {{ run.status === 'succeeded' ? duration(run.duration_ms) : run.status }}
            </span>
          </li>
        </ol>
      </PanelCard>

      <PanelCard
        title="Documents"
        subtitle="Policies, FAQs, event schedules"
        :loading="loading"
        :empty="!screen?.documents.length"
        empty-text="Nothing uploaded yet."
      >
        <template #actions>
          <label class="cursor-pointer text-xs text-brand hover:underline">
            {{ uploading ? 'Uploading…' : 'Upload' }}
            <input
              ref="fileInput"
              type="file"
              accept=".md,.txt,.pdf"
              class="sr-only"
              :disabled="uploading"
              @change="upload"
            />
          </label>
        </template>
        <ul class="space-y-1.5">
          <li
            v-for="document in screen?.documents ?? []"
            :key="document.id"
            class="flex items-baseline justify-between gap-3 text-sm"
          >
            <span class="min-w-0 truncate text-ink">{{ document.title }}</span>
            <span class="tabular shrink-0 text-xs text-ink-faint">
              {{ count(document.chunks) }} passages
            </span>
          </li>
        </ul>
      </PanelCard>
    </div>

    <p class="mt-4 text-xs text-ink-faint">
      An upload is searchable straight away: only its own passages are embedded, so adding one
      policy does not re-embed the catalogue.
    </p>
  </div>
</template>
