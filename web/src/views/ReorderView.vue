<script setup lang="ts">
/**
 * What to order, grouped by who you order it from.
 *
 * Two things this screen will not do, and both are deliberate. It will not
 * place an order — the furthest it goes is opening the owner's own mail client
 * with the body already written. And it will not show a quantity without the
 * sentence behind it: "sells 3.1/week, 4 on hand, 14-day lead time" is what
 * turns a suggestion into something an owner can disagree with, which is the
 * only way they will ever trust the ones they agree with.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../api/client'
import type { PurchaseOrder, ReorderScreen } from '../api/types'
import PanelCard from '../components/PanelCard.vue'
import { money, moneyShort, percent, quantity, shopDate } from '../lib/format'
import { useTenantStore } from '../stores/tenant'

const route = useRoute()
const shop = useTenantStore()

const slug = computed(() => String(route.params.tenant ?? ''))
const screen = ref<ReorderScreen | null>(null)
const orders = ref<PurchaseOrder[]>([])
const open = ref<PurchaseOrder | null>(null)
const loading = ref(true)
const working = ref(false)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)

const skipped = computed(() => Object.entries(screen.value?.skipped ?? {}))

async function load() {
  try {
    const [next, drafts] = await Promise.all([
      api.reorder(slug.value),
      api.purchaseOrders(slug.value),
    ])
    screen.value = next
    orders.value = drafts
    error.value = null
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

async function draft(vendorId: string | null) {
  working.value = true
  notice.value = null
  try {
    const created = await api.createDrafts(slug.value, vendorId ?? undefined)
    notice.value = created.length
      ? `${created.length} draft order${created.length === 1 ? '' : 's'} ready to review.`
      : 'Nothing to order from that supplier right now.'
    await load()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    working.value = false
  }
}

async function review(order: PurchaseOrder) {
  open.value = await api.purchaseOrder(slug.value, order.id)
}

async function setQuantity(lineId: string, value: string) {
  if (!open.value) return
  await api.updatePurchaseOrderLine(slug.value, lineId, { quantity: value })
  open.value = await api.purchaseOrder(slug.value, open.value.id)
}

async function removeLine(lineId: string) {
  if (!open.value) return
  await api.updatePurchaseOrderLine(slug.value, lineId, { remove: true })
  open.value = await api.purchaseOrder(slug.value, open.value.id)
}

async function mark(status: 'sent' | 'received') {
  if (!open.value) return
  open.value = await api.setPurchaseOrderStatus(slug.value, open.value.id, status)
  await load()
}

onMounted(load)
watch(slug, load)
</script>

<template>
  <div class="mx-auto max-w-5xl px-4 py-6 lg:px-8">
    <header class="mb-5">
      <h1 class="display text-2xl font-semibold tracking-tight text-ink">Reorder</h1>
      <p class="mt-0.5 text-sm text-ink-muted">
        From the last four weeks of sales, each supplier's lead time, and what is already on
        an open order.
      </p>
    </header>

    <p v-if="error" class="mb-4 border-l-2 border-down bg-panel px-4 py-3 text-sm text-ink" role="alert">
      {{ error }}
    </p>
    <p v-if="notice" class="mb-4 border-l-2 border-brand bg-panel px-4 py-3 text-sm text-ink">
      {{ notice }}
    </p>

    <!-- The draft under review takes over the screen: editing quantities is a
         one-thing-at-a-time job. -->
    <PanelCard
      v-if="open"
      :title="`${open.reference} — ${open.vendor_name}`"
      :subtitle="open.expected_at ? `Expected ${shopDate(open.expected_at, shop.timezone)}` : undefined"
    >
      <template #actions>
        <button type="button" class="text-xs text-ink-muted hover:text-ink" @click="open = null">
          Back to suggestions
        </button>
      </template>

      <div class="mb-3 flex flex-wrap items-baseline gap-x-5 gap-y-1 text-sm">
        <span class="tabular text-ink">{{ quantity(open.units) }} units</span>
        <span class="tabular text-ink">{{ money(open.total_at_cost, shop.currency) }} at cost</span>
        <span v-if="open.unpriced_lines" class="text-ink-faint">
          {{ open.unpriced_lines }} line{{ open.unpriced_lines === 1 ? '' : 's' }} have no cost on
          file, so the total is only the part we can price
        </span>
      </div>

      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-rule text-left text-xs text-ink-faint">
              <th class="py-1.5 pr-3 font-normal">Item</th>
              <th class="py-1.5 pr-3 text-right font-normal">On hand</th>
              <th class="py-1.5 pr-3 text-right font-normal">Order</th>
              <th class="py-1.5 pr-3 text-right font-normal">Line</th>
              <th class="py-1.5 font-normal"></th>
            </tr>
          </thead>
          <tbody class="divide-y divide-rule">
            <tr v-for="line in open.lines" :key="line.id">
              <td class="py-2 pr-3">
                <div class="text-ink">{{ line.name }}</div>
                <div class="text-xs text-ink-faint">{{ line.why }}</div>
                <div v-for="caveat in line.caveats" :key="caveat" class="text-xs text-note-ink">
                  {{ caveat }}
                </div>
              </td>
              <td class="tabular py-2 pr-3 text-right text-ink-muted">
                {{ quantity(line.on_hand_at_draft) }}
              </td>
              <td class="py-2 pr-3 text-right">
                <input
                  class="tabular w-16 rounded-sm border border-rule bg-panel px-2 py-1 text-right text-sm text-ink"
                  type="number"
                  min="0"
                  step="1"
                  :value="Number(line.quantity)"
                  @change="setQuantity(line.id, (($event.target as HTMLInputElement).value))"
                />
              </td>
              <td class="tabular py-2 pr-3 text-right text-ink">
                {{ money(line.line_cost, shop.currency) }}
              </td>
              <td class="py-2 text-right">
                <button
                  type="button"
                  class="text-xs text-ink-faint hover:text-down"
                  @click="removeLine(line.id)"
                >
                  Remove
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="mt-4 flex flex-wrap items-center gap-3">
        <a
          class="rounded-sm border border-rule px-3 py-1.5 text-sm text-ink hover:border-rule-strong"
          :href="api.purchaseOrderFile(slug, open.id, 'pdf')"
          target="_blank"
          rel="noopener"
        >
          Download PDF
        </a>
        <a
          class="rounded-sm border border-rule px-3 py-1.5 text-sm text-ink hover:border-rule-strong"
          :href="api.purchaseOrderFile(slug, open.id, 'csv')"
        >
          Download CSV
        </a>
        <a
          v-if="open.mailto"
          class="rounded-sm bg-brand px-3 py-1.5 text-sm font-medium text-white"
          :href="open.mailto"
        >
          Email {{ open.vendor_name }}
        </a>
        <button
          v-if="open.status === 'draft'"
          type="button"
          class="text-sm text-ink-muted hover:text-ink"
          @click="mark('sent')"
        >
          I sent this
        </button>
        <button
          v-if="open.status === 'sent'"
          type="button"
          class="text-sm text-ink-muted hover:text-ink"
          @click="mark('received')"
        >
          It arrived
        </button>
        <span class="text-xs text-ink-faint">
          We never send an order ourselves — this opens your own mail.
        </span>
      </div>
    </PanelCard>

    <template v-else>
      <PanelCard
        v-if="orders.length"
        title="Drafts you have not finished"
        class="mb-4"
      >
        <ul class="divide-y divide-rule text-sm">
          <li
            v-for="order in orders"
            :key="order.id"
            class="flex flex-wrap items-baseline justify-between gap-2 py-2"
          >
            <span class="text-ink">{{ order.reference }} — {{ order.vendor_name }}</span>
            <span class="tabular text-ink-muted">
              {{ quantity(order.units) }} units · {{ money(order.total_at_cost, shop.currency) }}
            </span>
            <span class="text-xs text-ink-faint">{{ order.status }}</span>
            <button type="button" class="text-xs text-brand" @click="review(order)">Review</button>
          </li>
        </ul>
      </PanelCard>

      <PanelCard
        title="Suggestions"
        :subtitle="screen ? `As of ${shopDate(screen.as_of, shop.timezone)}` : undefined"
        :loading="loading"
        :empty="!loading && !screen?.groups.length"
        empty-text="Nothing needs ordering at the moment."
      >
        <template #actions>
          <span v-if="screen" class="tabular text-xs text-ink-faint">
            {{ moneyShort(screen.total_at_cost, shop.currency) }} at cost
            <template v-if="screen.cost_coverage">
              · {{ percent(screen.cost_coverage, 0) }} of lines priced
            </template>
          </span>
        </template>

        <div v-for="group in screen?.groups ?? []" :key="group.vendor_name" class="mb-6 last:mb-0">
          <div class="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <h3 class="text-sm font-semibold text-ink">{{ group.vendor_name }}</h3>
            <div class="flex items-center gap-3">
              <span class="tabular text-xs text-ink-muted">
                {{ group.lines.length }} lines · {{ money(group.total_at_cost, shop.currency) }}
              </span>
              <button
                type="button"
                class="rounded-sm border border-rule px-2.5 py-1 text-xs text-ink hover:border-rule-strong disabled:opacity-50"
                :disabled="working"
                @click="draft(group.vendor_id)"
              >
                Make a draft
              </button>
            </div>
          </div>

          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="border-b border-rule text-left text-xs text-ink-faint">
                  <th class="py-1.5 pr-3 font-normal">Item</th>
                  <th class="py-1.5 pr-3 text-right font-normal">On hand</th>
                  <th class="py-1.5 pr-3 text-right font-normal">Cover</th>
                  <th class="py-1.5 pr-3 text-right font-normal">Order</th>
                  <th class="py-1.5 text-right font-normal">At cost</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-rule">
                <tr v-for="line in group.lines" :key="line.variant_id">
                  <td class="py-2 pr-3">
                    <div class="flex items-baseline gap-2">
                      <span class="text-ink">{{ line.label }}</span>
                      <span
                        v-if="line.urgent"
                        class="rounded-sm border border-down px-1 text-[0.6rem] tracking-wide text-down uppercase"
                      >
                        gone first
                      </span>
                    </div>
                    <div class="text-xs text-ink-faint">{{ line.why }}</div>
                  </td>
                  <td class="tabular py-2 pr-3 text-right text-ink-muted">
                    {{ quantity(line.on_hand) }}
                  </td>
                  <td class="tabular py-2 pr-3 text-right text-ink-muted">
                    {{ line.days_of_cover ? `${Number(line.days_of_cover).toFixed(0)}d` : '—' }}
                  </td>
                  <td class="tabular py-2 pr-3 text-right font-medium text-ink">
                    {{ quantity(line.suggested_qty) }}
                  </td>
                  <td class="tabular py-2 text-right text-ink-muted">
                    {{ money(line.line_cost, shop.currency) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </PanelCard>

      <div v-if="screen && (screen.caveats.length || skipped.length)" class="mt-4 space-y-1">
        <p v-for="caveat in screen.caveats" :key="caveat" class="text-xs text-note-ink">
          {{ caveat }}
        </p>
        <p v-if="skipped.length" class="text-xs text-ink-faint">
          Left out:
          <span v-for="([reason, total], index) in skipped" :key="reason">
            {{ total }} {{ reason }}{{ index < skipped.length - 1 ? ', ' : '' }}
          </span>
        </p>
      </div>
    </template>
  </div>
</template>
