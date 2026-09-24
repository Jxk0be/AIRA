<script setup lang="ts">
/**
 * What is on the shelf.
 *
 * Searching, sorting and paging all happen in Postgres rather than in the
 * browser: a shop with forty thousand SKUs is a normal shop, and a table that
 * only works because the catalogue was small is a table that breaks on the
 * first real customer.
 *
 * The margin column is the honest one. An item with no cost recorded shows a
 * dash, never 0% — and the note under the table says how many of them there
 * are, because that number is a job for the owner rather than a footnote.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../api/client'
import type { CatalogPage, CatalogSort } from '../api/types'
import { money, percent, quantity, shopDate } from '../lib/format'
import { useTenantStore } from '../stores/tenant'

const route = useRoute()
const shop = useTenantStore()

const slug = computed(() => String(route.params.tenant ?? ''))
const page = ref<CatalogPage | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

const search = ref('')
const sort = ref<CatalogSort>('name')
const descending = ref(false)
const offset = ref(0)
const location = ref<string>('')
const PAGE = 50

const columns: { key: CatalogSort; label: string; numeric?: boolean }[] = [
  { key: 'name', label: 'Item' },
  { key: 'category', label: 'Category' },
  { key: 'on_hand', label: 'On hand', numeric: true },
  { key: 'price', label: 'Price', numeric: true },
  { key: 'cost', label: 'Cost', numeric: true },
  { key: 'margin', label: 'Margin', numeric: true },
  { key: 'retail_value', label: 'Value', numeric: true },
  { key: 'last_sold', label: 'Last sold', numeric: true },
]

async function load() {
  loading.value = true
  error.value = null
  try {
    page.value = await api.inventory(slug.value, {
      q: search.value.trim() || undefined,
      sort: sort.value,
      desc: descending.value,
      limit: PAGE,
      offset: offset.value,
      location: location.value || undefined,
    })
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

let typing: ReturnType<typeof setTimeout> | undefined
watch(search, () => {
  // One request per pause in typing, not one per keystroke.
  clearTimeout(typing)
  typing = setTimeout(() => {
    offset.value = 0
    void load()
  }, 250)
})

watch([slug, sort, descending, offset, location], load)
onMounted(load)

function sortBy(key: CatalogSort) {
  if (sort.value === key) {
    descending.value = !descending.value
  } else {
    sort.value = key
    // Money and dates are asked about biggest-first; names are not.
    descending.value = key !== 'name' && key !== 'category' && key !== 'sku'
  }
  offset.value = 0
}

const showing = computed(() => {
  if (!page.value?.total) return 'Nothing matches'
  const first = page.value.offset + 1
  const last = page.value.offset + page.value.rows.length
  return `${first}–${last} of ${page.value.total.toLocaleString()}`
})

const hasCosts = computed(() => shop.can('has_costs'))
</script>

<template>
  <div class="mx-auto max-w-6xl px-4 py-5 sm:px-6 lg:py-8">
    <header class="mb-4">
      <h1 class="display text-2xl font-semibold tracking-tight text-ink sm:text-3xl">Inventory</h1>
      <p class="mt-0.5 text-xs text-ink-faint">
        Stock as of the last sync, priced from {{ shop.name }}'s own catalogue.
      </p>
    </header>

    <div class="mb-3 flex flex-wrap items-center gap-2">
      <label class="min-w-0 flex-1">
        <span class="sr-only">Search the catalogue</span>
        <input
          v-model="search"
          type="search"
          placeholder="Name, SKU, barcode or category…"
          class="w-full border border-rule bg-panel px-3 py-1.5 text-sm text-ink outline-none placeholder:text-ink-faint focus:border-brand"
        />
      </label>

      <label v-if="shop.can('multi_location')" class="shrink-0">
        <span class="sr-only">Location</span>
        <select
          v-model="location"
          class="border border-rule bg-panel px-2 py-1.5 text-sm text-ink outline-none focus:border-brand"
        >
          <option value="">Every location</option>
          <option v-for="place in shop.profile?.locations ?? []" :key="place.id" :value="place.id">
            {{ place.name }}
          </option>
        </select>
      </label>

      <p class="tabular shrink-0 text-xs text-ink-faint">{{ showing }}</p>
    </div>

    <p v-if="error" class="border-l-2 border-down bg-panel px-4 py-3 text-sm" role="alert">
      {{ error }}
    </p>

    <div v-else class="overflow-x-auto border border-rule bg-panel">
      <table class="w-full min-w-[46rem] text-sm">
        <thead>
          <tr class="border-b border-rule">
            <th
              v-for="column in columns"
              :key="column.key"
              class="px-3 py-2 font-medium"
              :class="[
                column.numeric ? 'text-right' : 'text-left',
                !hasCosts && (column.key === 'cost' || column.key === 'margin')
                  ? 'text-ink-faint'
                  : 'text-ink-muted',
              ]"
            >
              <button
                type="button"
                class="inline-flex items-baseline gap-1 hover:text-ink"
                @click="sortBy(column.key)"
              >
                {{ column.label }}
                <span v-if="sort === column.key" class="text-[0.6rem] text-brand">
                  {{ descending ? '▼' : '▲' }}
                </span>
              </button>
            </th>
          </tr>
        </thead>
        <tbody :class="{ 'opacity-50': loading }">
          <tr v-if="!loading && !page?.rows.length">
            <td colspan="8" class="px-3 py-6 text-center text-sm text-ink-faint">
              Nothing in the catalogue matches that.
            </td>
          </tr>
          <tr
            v-for="row in page?.rows ?? []"
            :key="row.variant_id"
            class="border-b border-rule/50 last:border-0 hover:bg-sunk/60"
          >
            <td class="max-w-[20rem] px-3 py-2">
              <span class="block truncate text-ink">{{ row.label }}</span>
              <span v-if="row.sku" class="tabular block truncate text-xs text-ink-faint">
                {{ row.sku }}
              </span>
            </td>
            <td class="px-3 py-2 text-ink-muted">{{ row.category ?? '—' }}</td>
            <td class="tabular px-3 py-2 text-right whitespace-nowrap">
              {{ quantity(row.units_on_hand) }}
              <span
                v-if="row.stock.length > 1"
                class="block text-xs text-ink-faint"
                :title="row.stock.map((s) => `${s.location}: ${quantity(s.on_hand)}`).join(' · ')"
              >
                {{ row.stock.map((s) => quantity(s.on_hand)).join(' / ') }}
              </span>
            </td>
            <td class="tabular px-3 py-2 text-right whitespace-nowrap">
              {{ money(row.price, page?.currency) }}
            </td>
            <td class="tabular px-3 py-2 text-right whitespace-nowrap text-ink-muted">
              {{ money(row.cost, page?.currency) }}
            </td>
            <td class="tabular px-3 py-2 text-right whitespace-nowrap">
              <!-- No cost means no margin. Never 0%, which reads as "makes nothing". -->
              {{ row.unit_margin === null ? '—' : percent(row.unit_margin, 0) }}
            </td>
            <td class="tabular px-3 py-2 text-right whitespace-nowrap">
              {{ money(row.retail_value, page?.currency) }}
            </td>
            <td class="tabular px-3 py-2 text-right whitespace-nowrap text-ink-muted">
              {{ row.last_sold_at ? shopDate(row.last_sold_at, shop.timezone) : 'never' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div
      v-if="page?.caveats.length"
      class="border border-t-0 border-rule border-l-note-rule bg-note px-4 py-2"
    >
      <p v-for="caveat in page.caveats" :key="caveat.code" class="text-xs leading-snug text-note-ink">
        {{ caveat.message }}
      </p>
    </div>

    <nav v-if="page && page.total > PAGE" class="mt-3 flex items-center justify-between gap-3">
      <button
        type="button"
        class="border border-rule bg-panel px-3 py-1 text-sm text-ink-muted hover:bg-sunk disabled:opacity-40"
        :disabled="offset === 0"
        @click="offset = Math.max(0, offset - PAGE)"
      >
        Previous
      </button>
      <span class="tabular text-xs text-ink-faint">{{ showing }}</span>
      <button
        type="button"
        class="border border-rule bg-panel px-3 py-1 text-sm text-ink-muted hover:bg-sunk disabled:opacity-40"
        :disabled="offset + PAGE >= page.total"
        @click="offset = offset + PAGE"
      >
        Next
      </button>
    </nav>
  </div>
</template>
