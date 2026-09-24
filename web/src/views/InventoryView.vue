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
 *
 * Two things changed in the rebuild. The table becomes cards below `sm`, so a
 * phone stops showing five columns of money with no item names (audit U8). And
 * the paging controls sit above the rows as well as below, because paging
 * through 333 items used to mean scrolling past fifty of them to reach Next and
 * then scrolling back up (audit U9).
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../api/client'
import type { CatalogPage, CatalogSort } from '../api/types'
import DataTable, { type Column } from '../components/DataTable.vue'
import { money, percent, quantity, shopDate } from '../lib/format'
import { useTenantStore } from '../stores/tenant'
import UiButton from '../ui/UiButton.vue'
import UiSkeleton from '../ui/UiSkeleton.vue'

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
const location = ref('')
const PAGE = 50

const columns: Column[] = [
  { key: 'name', label: 'Item', sortable: true },
  { key: 'category', label: 'Category', sortable: true, desktopOnly: true },
  { key: 'on_hand', label: 'On hand', numeric: true, sortable: true },
  { key: 'price', label: 'Price', numeric: true, sortable: true },
  { key: 'cost', label: 'Cost', numeric: true, sortable: true },
  { key: 'margin', label: 'Margin', numeric: true, sortable: true },
  { key: 'retail_value', label: 'Value', numeric: true, sortable: true },
  { key: 'last_sold', label: 'Last sold', numeric: true, sortable: true },
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

function sortBy(key: string) {
  const next = key as CatalogSort
  if (sort.value === next) {
    descending.value = !descending.value
  } else {
    sort.value = next
    // Money and dates are asked about biggest-first; names are not.
    descending.value = next !== 'name' && next !== 'category' && next !== 'sku'
  }
  offset.value = 0
}

const showing = computed(() => {
  if (!page.value?.total) return 'Nothing matches'
  const first = page.value.offset + 1
  const last = page.value.offset + page.value.rows.length
  return `${first}–${last} of ${page.value.total.toLocaleString()}`
})

const hasMore = computed(() => !!page.value && offset.value + PAGE < page.value.total)
</script>

<template>
  <div class="mx-auto max-w-5xl px-4 py-5 sm:px-6">
    <p class="mb-3 text-sm text-ink-muted">
      Stock as of the last sync, priced from {{ shop.name }}'s own catalogue.
    </p>

    <div class="mb-3 flex flex-wrap items-center gap-2">
      <label class="min-w-0 flex-1">
        <span class="sr-only">Search the catalogue</span>
        <input
          v-model="search"
          type="search"
          placeholder="Name, SKU, barcode or category…"
          class="min-h-11 w-full rounded-md border border-border-strong bg-surface px-3 text-base text-ink outline-none placeholder:text-ink-muted focus:border-primary"
        />
      </label>

      <label v-if="shop.can('multi_location')" class="shrink-0">
        <span class="sr-only">Location</span>
        <select
          v-model="location"
          class="min-h-11 rounded-md border border-border-strong bg-surface px-3 text-base text-ink"
        >
          <option value="">Every location</option>
          <option v-for="place in shop.profile?.locations ?? []" :key="place.id" :value="place.id">
            {{ place.name }}
          </option>
        </select>
      </label>
    </div>

    <!-- Paging above the rows as well as below: 50 rows is a long way to
         scroll to reach Next, and a long way back (audit U9). -->
    <nav
      v-if="page && page.total > PAGE"
      aria-label="Catalogue pages"
      class="mb-3 flex items-center justify-between gap-3"
    >
      <UiButton
        size="sm"
        variant="secondary"
        :disabled="offset === 0"
        @click="offset = Math.max(0, offset - PAGE)"
      >
        Previous
      </UiButton>
      <span class="tabular text-sm text-ink-muted">{{ showing }}</span>
      <UiButton size="sm" variant="secondary" :disabled="!hasMore" @click="offset = offset + PAGE">
        Next
      </UiButton>
    </nav>
    <p v-else class="tabular mb-3 text-sm text-ink-muted">{{ showing }}</p>

    <div v-if="error" class="rounded-lg border border-danger bg-danger-subtle p-4" role="alert">
      <p class="font-medium text-ink">We could not load the catalogue.</p>
      <p class="mt-1 text-sm text-ink-muted">{{ error }}</p>
      <UiButton class="mt-3" size="sm" variant="secondary" @click="load">Try again</UiButton>
    </div>

    <div v-else-if="loading && !page" class="rounded-lg border border-border bg-surface p-4">
      <UiSkeleton :lines="6" />
    </div>

    <div v-else class="rounded-lg border border-border bg-surface sm:px-1">
      <DataTable
        :columns="columns"
        :rows="page?.rows ?? []"
        :row-key="(row) => String(row.variant_id)"
        caption="Every item in the catalogue, with what it cost and what it is worth"
        :sort-key="sort"
        :sort-descending="descending"
        :loading="loading"
        empty-text="Nothing in the catalogue matches that."
        @sort="sortBy"
      >
        <template #cell="{ row, column }">
          <template v-if="column.key === 'name'">
            <span class="block text-ink">{{ row.label }}</span>
            <span v-if="row.sku" class="tabular block text-sm text-ink-muted">{{ row.sku }}</span>
          </template>
          <template v-else-if="column.key === 'category'">
            {{ row.category ?? '—' }}
          </template>
          <template v-else-if="column.key === 'on_hand'">
            {{ quantity(row.units_on_hand) }}
          </template>
          <template v-else-if="column.key === 'price'">
            {{ money(row.price, page?.currency) }}
          </template>
          <template v-else-if="column.key === 'cost'">
            {{ money(row.cost, page?.currency) }}
          </template>
          <template v-else-if="column.key === 'margin'">
            <!-- No cost means no margin. Never 0%, which reads as "makes nothing". -->
            {{ row.unit_margin === null ? '—' : percent(row.unit_margin, 0) }}
          </template>
          <template v-else-if="column.key === 'retail_value'">
            {{ money(row.retail_value, page?.currency) }}
          </template>
          <template v-else-if="column.key === 'last_sold'">
            {{ row.last_sold_at ? shopDate(row.last_sold_at, shop.timezone) : 'never' }}
          </template>
        </template>
      </DataTable>
    </div>

    <div v-if="page?.caveats.length" class="mt-3 flex gap-2">
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
        <p v-for="caveat in page.caveats" :key="caveat.code" class="text-sm leading-snug text-ink-muted">
          {{ caveat.message }}
        </p>
      </div>
    </div>

    <nav
      v-if="page && page.total > PAGE"
      aria-label="Catalogue pages, bottom"
      class="mt-4 flex items-center justify-between gap-3"
    >
      <UiButton
        size="sm"
        variant="secondary"
        :disabled="offset === 0"
        @click="offset = Math.max(0, offset - PAGE)"
      >
        Previous
      </UiButton>
      <span class="tabular text-sm text-ink-muted">{{ showing }}</span>
      <UiButton size="sm" variant="secondary" :disabled="!hasMore" @click="offset = offset + PAGE">
        Next
      </UiButton>
    </nav>
  </div>
</template>
