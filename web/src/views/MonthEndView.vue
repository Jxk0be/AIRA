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
 *
 * Recipients, the Monday email preview and the send log used to sit underneath
 * all this. They are settings about who hears from us, not month-end figures,
 * so they moved to Settings › Notifications.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../api/client'
import type { MonthEndPacket } from '../api/types'
import SectionCard from '../components/SectionCard.vue'
import { shopDateTime } from '../lib/format'
import { useTenantStore } from '../stores/tenant'
import UiButton from '../ui/UiButton.vue'
import { withToast } from '../ui/toast'

const route = useRoute()
const shop = useTenantStore()

const slug = computed(() => String(route.params.tenant ?? ''))
const packets = ref<MonthEndPacket[]>([])
const loading = ref(true)
const working = ref(false)
const error = ref<string | null>(null)

async function load() {
  loading.value = true
  try {
    packets.value = await api.packets(slug.value)
    error.value = null
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

async function generate() {
  working.value = true
  const packet = await withToast(() => api.generatePacket(slug.value), {
    success: (made) => `${made.label} is ready`,
    failure: 'Could not build that packet',
  })
  if (packet) await load()
  working.value = false
}

onMounted(load)
watch(slug, load)
</script>

<template>
  <div class="mx-auto max-w-3xl px-4 py-5 sm:px-6">
    <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <p class="text-sm text-ink-muted">
        One PDF to file and one workbook to work with, built automatically on the 1st.
      </p>
      <UiButton :loading="working" @click="generate">Build last month</UiButton>
    </div>

    <div v-if="error" class="rounded-lg border border-danger bg-danger-subtle p-4" role="alert">
      <p class="font-medium text-ink">We could not load your packets.</p>
      <p class="mt-1 text-sm text-ink-muted">{{ error }}</p>
      <UiButton class="mt-3" size="sm" variant="secondary" @click="load">Try again</UiButton>
    </div>

    <SectionCard
      v-else
      title="Month-end packets"
      :loading="loading"
      :empty="!loading && !packets.length"
      empty-text="No packets yet. One is built automatically on the 1st, or build last month's now."
    >
      <ul class="divide-y divide-border">
        <li v-for="packet in packets" :key="packet.id" class="py-3">
          <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <div class="min-w-0">
              <p class="text-base font-semibold text-ink">{{ packet.label }}</p>
              <p class="text-sm text-ink-muted">
                built {{ shopDateTime(packet.generated_at, shop.timezone) }}
                <template v-if="packet.emailed_to"> · sent to {{ packet.emailed_to }}</template>
              </p>
            </div>
            <!-- Real buttons, not 11px links: these are the whole point of the
                 screen and were the smallest targets on it. -->
            <div class="flex shrink-0 gap-2">
              <UiButton as="a" as-child size="sm" variant="secondary">
                <a :href="api.packetFile(slug, packet.id, 'pdf')" target="_blank" rel="noopener">
                  Download PDF
                </a>
              </UiButton>
              <UiButton as="a" as-child size="sm" variant="secondary">
                <a :href="api.packetFile(slug, packet.id, 'xlsx')">Download spreadsheet</a>
              </UiButton>
            </div>
          </div>
          <ul v-if="packet.notes.length" class="mt-2 space-y-1">
            <li
              v-for="note in packet.notes"
              :key="note"
              class="text-sm leading-snug text-ink-muted"
            >
              {{ note }}
            </li>
          </ul>
        </li>
      </ul>
    </SectionCard>
  </div>
</template>
