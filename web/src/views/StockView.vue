<script setup lang="ts">
/**
 * One place for the shelves: what is on them, what to buy, what is stuck.
 *
 * These were three top-level destinations, which is three-fifths of a phone's
 * navigation spent on one subject. The tab lives in the query string so a
 * finding that deep-links to `?tab=reorder` still lands where it meant to —
 * see the redirect table in router.ts.
 *
 * The panels keep their own page headers for now; step 6 rebuilds each one and
 * folds the heading into the shell's top bar.
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import UiTabs from '../ui/UiTabs.vue'
import DeadStockView from './DeadStockView.vue'
import InventoryView from './InventoryView.vue'
import ReorderView from './ReorderView.vue'

const route = useRoute()
const router = useRouter()

const TABS = [
  { value: 'all', label: 'All items' },
  { value: 'reorder', label: 'Reorder' },
  { value: 'not-selling', label: 'Not selling' },
]

const tab = computed({
  get: () => {
    const wanted = String(route.query.tab ?? 'all')
    return TABS.some((one) => one.value === wanted) ? wanted : 'all'
  },
  // `replace`, so flipping a tab does not fill the back button with the same
  // screen three times.
  set: (value) => void router.replace({ query: { ...route.query, tab: value } }),
})
</script>

<template>
  <div>
    <!-- The container owns the page heading, not the three panels inside it:
         one <h1> per screen, and it names the tab you are actually on so the
         shell can announce and focus it on navigation. -->
    <h1 class="sr-only">Stock — {{ TABS.find((one) => one.value === tab)?.label }}</h1>
    <div class="px-4 pt-3 sm:px-6">
      <UiTabs v-model="tab" :tabs="TABS" label="Stock" />
    </div>
    <InventoryView v-if="tab === 'all'" />
    <ReorderView v-else-if="tab === 'reorder'" />
    <DeadStockView v-else />
  </div>
</template>
