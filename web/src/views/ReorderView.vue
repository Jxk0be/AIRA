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
 *
 * What the rebuild fixed:
 *
 *   A supplier's name was `text-sm font-semibold`, one notch above the item
 *   names beside it — not enough to read as a divider, so the group heading
 *   looked like another row (audit U7). It is a heading on its own rule now.
 *
 *   "GONE FIRST" was a 9.6px badge sitting *inside* the item cell, wrapping the
 *   name onto three lines and knocking every figure in the row out of line with
 *   its header. It is a labelled badge under the name, and the figures keep
 *   their columns.
 *
 *   The quantity input had no label at all — a screen reader read "spin button"
 *   with no clue which line it belonged to (audit A6).
 *
 *   Every write said so in a paragraph that shoved the page down. Now they are
 *   toasts (audit U13).
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../api/client'
import type { PurchaseOrder, ReorderScreen } from '../api/types'
import SectionCard from '../components/SectionCard.vue'
import { money, moneyShort, percent, quantity, shopDate } from '../lib/format'
import { useTenantStore } from '../stores/tenant'
import UiBadge from '../ui/UiBadge.vue'
import UiButton from '../ui/UiButton.vue'
import UiSkeleton from '../ui/UiSkeleton.vue'
import { toast, withToast } from '../ui/toast'

const route = useRoute()
const shop = useTenantStore()

const slug = computed(() => String(route.params.tenant ?? ''))
const screen = ref<ReorderScreen | null>(null)
const orders = ref<PurchaseOrder[]>([])
const open = ref<PurchaseOrder | null>(null)
const loading = ref(true)
const working = ref(false)
const error = ref<string | null>(null)

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
  const created = await withToast(() => api.createDrafts(slug.value, vendorId ?? undefined), {
    success: (made) =>
      made.length
        ? `${made.length} draft order${made.length === 1 ? '' : 's'} ready to review`
        : 'Nothing to order from that supplier right now',
    failure: 'Could not build that draft',
  })
  if (created !== undefined) await load()
  working.value = false
}

async function review(order: PurchaseOrder) {
  try {
    open.value = await api.purchaseOrder(slug.value, order.id)
  } catch {
    toast.danger('Could not open that draft')
  }
}

async function setQuantity(lineId: string, value: string) {
  if (!open.value) return
  const done = await withToast(
    () => api.updatePurchaseOrderLine(slug.value, lineId, { quantity: value }),
    { success: 'Quantity updated', failure: 'Could not change that quantity' },
  )
  if (done !== undefined) open.value = await api.purchaseOrder(slug.value, open.value.id)
}

async function removeLine(lineId: string) {
  if (!open.value) return
  const done = await withToast(
    () => api.updatePurchaseOrderLine(slug.value, lineId, { remove: true }),
    { success: 'Line removed', failure: 'Could not remove that line' },
  )
  if (done !== undefined) open.value = await api.purchaseOrder(slug.value, open.value.id)
}

async function mark(status: 'sent' | 'received') {
  if (!open.value) return
  const updated = await withToast(
    () => api.setPurchaseOrderStatus(slug.value, open.value!.id, status),
    {
      success: status === 'sent' ? 'Marked as sent' : 'Marked as arrived',
      failure: 'Could not update that order',
    },
  )
  if (updated) {
    open.value = updated
    await load()
  }
}

onMounted(load)
watch(slug, load)
</script>

<template>
  <div class="mx-auto max-w-4xl px-4 py-5 sm:px-6">
    <p class="mb-4 text-sm text-ink-muted">
      From the last four weeks of sales, each supplier's lead time, and what is already on an open
      order.
    </p>

    <div v-if="error" class="rounded-lg border border-danger bg-danger-subtle p-4" role="alert">
      <p class="font-medium text-ink">We could not work out what to reorder.</p>
      <p class="mt-1 text-sm text-ink-muted">{{ error }}</p>
      <UiButton class="mt-3" size="sm" variant="secondary" @click="load">Try again</UiButton>
    </div>

    <!-- The draft under review takes over the screen: editing quantities is a
         one-thing-at-a-time job. -->
    <SectionCard
      v-else-if="open"
      :title="`${open.reference} — ${open.vendor_name}`"
      :subtitle="open.expected_at ? `Expected ${shopDate(open.expected_at, shop.timezone)}` : undefined"
    >
      <template #actions>
        <UiButton size="sm" variant="ghost" @click="open = null">Back to suggestions</UiButton>
      </template>

      <div class="mb-4 flex flex-wrap items-baseline gap-x-5 gap-y-1 text-base">
        <span class="tabular text-ink">{{ quantity(open.units) }} units</span>
        <span class="tabular text-ink">{{ money(open.total_at_cost, shop.currency) }} at cost</span>
        <span v-if="open.unpriced_lines" class="text-sm text-ink-muted">
          {{ open.unpriced_lines }} line{{ open.unpriced_lines === 1 ? '' : 's' }} have no cost on
          file, so the total is only the part we can price
        </span>
      </div>

      <ul class="divide-y divide-border">
        <li v-for="line in open.lines" :key="line.id" class="py-3">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div class="min-w-0 flex-1">
              <p class="text-base font-medium text-ink">{{ line.name }}</p>
              <p class="text-sm text-ink-muted">{{ line.why }}</p>
              <p v-for="caveat in line.caveats" :key="caveat" class="text-sm text-warning">
                {{ caveat }}
              </p>
            </div>
            <div class="flex items-center gap-3">
              <label class="flex items-center gap-2">
                <!-- Named, so a screen reader says which line this is (audit A6). -->
                <span class="sr-only">Order quantity for {{ line.name }}</span>
                <span class="text-sm text-ink-muted" aria-hidden="true">Order</span>
                <input
                  class="tabular min-h-11 w-20 rounded-md border border-border-strong bg-surface px-2 text-right text-base text-ink"
                  type="number"
                  min="0"
                  step="1"
                  :value="Number(line.quantity)"
                  @change="setQuantity(line.id, ($event.target as HTMLInputElement).value)"
                />
              </label>
              <span class="tabular w-20 text-right text-base text-ink">
                {{ money(line.line_cost, shop.currency) }}
              </span>
              <UiButton size="sm" variant="ghost" @click="removeLine(line.id)">Remove</UiButton>
            </div>
          </div>
        </li>
      </ul>

      <div class="mt-4 flex flex-wrap items-center gap-2">
        <UiButton
          v-if="open.mailto"
          as="a"
          as-child
        >
          <a :href="open.mailto">Email {{ open.vendor_name }}</a>
        </UiButton>
        <UiButton as="a" as-child variant="secondary">
          <a :href="api.purchaseOrderFile(slug, open.id, 'pdf')" target="_blank" rel="noopener">
            Download PDF
          </a>
        </UiButton>
        <UiButton as="a" as-child variant="secondary">
          <a :href="api.purchaseOrderFile(slug, open.id, 'csv')">Download spreadsheet</a>
        </UiButton>
        <UiButton v-if="open.status === 'draft'" variant="ghost" @click="mark('sent')">
          I sent this
        </UiButton>
        <UiButton v-if="open.status === 'sent'" variant="ghost" @click="mark('received')">
          It arrived
        </UiButton>
      </div>
      <p class="mt-2 text-sm text-ink-muted">
        We never send an order ourselves — this opens your own mail.
      </p>
    </SectionCard>

    <template v-else>
      <SectionCard v-if="orders.length" title="Drafts you have not finished" class="mb-4">
        <ul class="divide-y divide-border">
          <li
            v-for="order in orders"
            :key="order.id"
            class="flex flex-wrap items-center justify-between gap-3 py-2.5"
          >
            <div class="min-w-0">
              <p class="text-base text-ink">{{ order.reference }} — {{ order.vendor_name }}</p>
              <p class="tabular text-sm text-ink-muted">
                {{ quantity(order.units) }} units · {{ money(order.total_at_cost, shop.currency) }}
                · {{ order.status }}
              </p>
            </div>
            <UiButton size="sm" variant="secondary" @click="review(order)">Review</UiButton>
          </li>
        </ul>
      </SectionCard>

      <div v-if="loading" class="rounded-lg border border-border bg-surface p-4">
        <UiSkeleton :lines="6" />
      </div>

      <div
        v-else-if="!screen?.groups.length"
        class="rounded-lg border border-border bg-surface px-4 py-8 text-center"
      >
        <p class="text-base font-medium text-ink">Nothing needs ordering at the moment.</p>
      </div>

      <template v-else>
        <div class="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <p class="text-sm text-ink-muted">
            As of {{ shopDate(screen.as_of, shop.timezone) }}
          </p>
          <p class="tabular text-sm text-ink-muted">
            {{ moneyShort(screen.total_at_cost, shop.currency) }} at cost
            <template v-if="screen.cost_coverage">
              · {{ percent(screen.cost_coverage, 0) }} of lines priced
            </template>
          </p>
        </div>

        <!-- One section per supplier, each with a real heading. -->
        <section
          v-for="group in screen.groups"
          :key="group.vendor_name"
          class="mb-4 rounded-lg border border-border bg-surface"
        >
          <!-- Stacked on a phone, side by side from `sm`. Left to wrap, the
               button sat inline for short supplier names and dropped to its own
               line for long ones, so a column of suppliers looked ragged. -->
          <header
            class="flex flex-col items-start gap-2 border-b-2 border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-3"
          >
            <div class="min-w-0">
              <h2 class="text-lg font-bold text-ink">{{ group.vendor_name }}</h2>
              <p class="tabular text-sm text-ink-muted">
                {{ group.lines.length }} lines · {{ money(group.total_at_cost, shop.currency) }}
              </p>
            </div>
            <UiButton size="sm" :loading="working" @click="draft(group.vendor_id)">
              Make a draft
            </UiButton>
          </header>

          <ul class="divide-y divide-border">
            <li v-for="line in group.lines" :key="line.variant_id" class="px-4 py-3">
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0 flex-1">
                  <p class="text-base font-medium text-ink">{{ line.label }}</p>
                  <UiBadge v-if="line.urgent" tone="danger" class="mt-1">Gone first</UiBadge>
                  <p class="mt-1 text-sm text-ink-muted">{{ line.why }}</p>
                </div>
                <!-- Fixed columns, so every figure sits under its own label. -->
                <dl class="shrink-0 text-right">
                  <div class="flex items-baseline justify-end gap-2">
                    <dt class="text-sm text-ink-muted">Order</dt>
                    <dd class="tabular w-12 text-base font-semibold text-ink">
                      {{ quantity(line.suggested_qty) }}
                    </dd>
                  </div>
                  <div class="flex items-baseline justify-end gap-2">
                    <dt class="text-sm text-ink-muted">On hand</dt>
                    <dd class="tabular w-12 text-base text-ink-muted">
                      {{ quantity(line.on_hand) }}
                    </dd>
                  </div>
                  <div class="flex items-baseline justify-end gap-2">
                    <dt class="text-sm text-ink-muted">At cost</dt>
                    <dd class="tabular w-12 text-base text-ink-muted">
                      {{ money(line.line_cost, shop.currency) }}
                    </dd>
                  </div>
                </dl>
              </div>
            </li>
          </ul>
        </section>

        <div v-if="screen.caveats.length || skipped.length" class="mt-4 flex gap-2">
          <svg
            class="mt-0.5 h-4 w-4 flex-none text-warning"
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            aria-hidden="true"
          >
            <circle cx="10" cy="10" r="8" />
            <path d="M10 6.2v4.4M10 13.4h.01" stroke-linecap="round" />
          </svg>
          <div class="min-w-0">
            <p v-for="caveat in screen.caveats" :key="caveat" class="text-sm leading-snug text-ink-muted">
              {{ caveat }}
            </p>
            <p v-if="skipped.length" class="text-sm leading-snug text-ink-muted">
              Left out:
              <span v-for="([reason, total], index) in skipped" :key="reason">
                {{ total }} {{ reason }}{{ index < skipped.length - 1 ? ', ' : '' }}
              </span>
            </p>
          </div>
        </div>
      </template>
    </template>
  </div>
</template>
