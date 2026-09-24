<script setup lang="ts">
/**
 * The numbers the bookkeeper asks for, ready on the 1st.
 *
 * Unglamorous, and the reason a shop renews in January. Two files per month: a
 * PDF to file and a workbook to work with, because a bookkeeper handed a PDF of
 * a table has been handed a worse version of nothing.
 *
 * The data notes are shown in full and never folded away. A packet whose net
 * sales do not reconcile with the semantic layer, or whose takings do not
 * reconcile with sales plus tax plus tips, says so in those notes rather than
 * quietly adjusting the difference out.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../api/client'
import type { DigestPreview, MonthEndPacket } from '../api/types'
import PanelCard from '../components/PanelCard.vue'
import { shopDate, shopDateTime } from '../lib/format'
import { useTenantStore } from '../stores/tenant'

const route = useRoute()
const shop = useTenantStore()

const slug = computed(() => String(route.params.tenant ?? ''))
const packets = ref<MonthEndPacket[]>([])
const digest = ref<DigestPreview | null>(null)
const loading = ref(true)
const working = ref(false)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)

async function load() {
  try {
    const [rows, preview] = await Promise.all([
      api.packets(slug.value),
      api.digestPreview(slug.value).catch(() => null),
    ])
    packets.value = rows
    digest.value = preview
    error.value = null
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

async function generate() {
  working.value = true
  notice.value = null
  try {
    const packet = await api.generatePacket(slug.value)
    notice.value = `${packet.label} is ready.`
    await load()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    working.value = false
  }
}

async function sendTest() {
  notice.value = null
  try {
    const result = await api.sendTestDigest(slug.value)
    notice.value =
      result.status === 'sent'
        ? `Test digest sent to ${result.to}.`
        : `Not sent to ${result.to}: ${result.detail}`
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  }
}

onMounted(load)
watch(slug, load)
</script>

<template>
  <div class="mx-auto max-w-5xl px-4 py-6 lg:px-8">
    <header class="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="display text-2xl font-semibold tracking-tight text-ink">Reports</h1>
        <p class="mt-0.5 text-sm text-ink-muted">
          The month-end packet, and what Monday's email will say.
        </p>
      </div>
      <button
        type="button"
        class="rounded-sm border border-rule px-3 py-1.5 text-sm text-ink hover:border-rule-strong disabled:opacity-50"
        :disabled="working"
        @click="generate"
      >
        {{ working ? 'Working…' : 'Build last month' }}
      </button>
    </header>

    <p v-if="error" class="mb-4 border-l-2 border-down bg-panel px-4 py-3 text-sm text-ink" role="alert">
      {{ error }}
    </p>
    <p v-if="notice" class="mb-4 border-l-2 border-brand bg-panel px-4 py-3 text-sm text-ink">
      {{ notice }}
    </p>

    <PanelCard
      title="Month-end packets"
      :loading="loading"
      :empty="!loading && !packets.length"
      empty-text="No packets yet. One is built automatically on the 1st."
      class="mb-4"
    >
      <ul class="-my-1 divide-y divide-rule">
        <li v-for="packet in packets" :key="packet.id" class="py-3">
          <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <span class="font-medium text-ink">{{ packet.label }}</span>
            <span class="text-xs text-ink-faint">
              built {{ shopDateTime(packet.generated_at, shop.timezone) }}
              <template v-if="packet.emailed_to"> · sent to {{ packet.emailed_to }}</template>
            </span>
            <span class="flex items-center gap-3 text-xs">
              <a class="text-brand" :href="api.packetFile(slug, packet.id, 'pdf')" target="_blank" rel="noopener">
                PDF
              </a>
              <a class="text-brand" :href="api.packetFile(slug, packet.id, 'xlsx')">Spreadsheet</a>
            </span>
          </div>
          <ul v-if="packet.notes.length" class="mt-1.5 space-y-0.5">
            <li v-for="note in packet.notes" :key="note" class="text-xs leading-snug text-ink-faint">
              {{ note }}
            </li>
          </ul>
        </li>
      </ul>
    </PanelCard>

    <PanelCard
      title="Monday's email"
      :subtitle="
        digest ? `${shopDate(digest.week_start, shop.timezone)} – ${shopDate(digest.week_end, shop.timezone)}` : undefined
      "
      :loading="loading"
      :empty="!loading && !digest"
      empty-text="Not enough recent data to build a digest from."
    >
      <template #actions>
        <button type="button" class="text-xs text-ink-muted hover:text-ink" @click="sendTest">
          Send me a test
        </button>
      </template>

      <p class="mb-2 text-sm font-medium text-ink">{{ digest?.subject }}</p>
      <pre
        class="max-h-96 overflow-auto border border-rule bg-sunk p-3 text-xs leading-relaxed whitespace-pre-wrap text-ink-muted"
        >{{ digest?.text }}</pre
      >
      <p class="mt-2 text-xs text-ink-faint">
        <template v-if="digest?.copy_source === 'model'">
          The wording was written by the model and every figure in it was checked back against
          the data before you saw it.
        </template>
        <template v-else>
          Written from the plain template — either the model was unavailable or its wording
          contained a figure that was not in the data, so it was thrown away.
        </template>
      </p>
    </PanelCard>
  </div>
</template>
