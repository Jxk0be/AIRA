<script setup lang="ts">
/**
 * The numbers you look at monthly rather than daily.
 *
 * "Sales" holds the breakdowns that used to sit on the dashboard: top products,
 * category mix, and the splits by location and till. They are a monthly
 * question, so they are here rather than on the first screen of the day.
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import UiTabs from '../ui/UiTabs.vue'
import MonthEndView from './MonthEndView.vue'
import SalesView from './SalesView.vue'
import StaffingView from './StaffingView.vue'

const route = useRoute()
const router = useRouter()

const TABS = [
  { value: 'month-end', label: 'Month end' },
  { value: 'sales', label: 'Sales' },
  { value: 'busy-hours', label: 'Busy hours' },
]

const tab = computed({
  get: () => {
    const wanted = String(route.query.tab ?? 'month-end')
    return TABS.some((one) => one.value === wanted) ? wanted : 'month-end'
  },
  set: (value) => void router.replace({ query: { ...route.query, tab: value } }),
})
</script>

<template>
  <div>
    <!-- The container owns the page heading, not the three panels inside it:
         one <h1> per screen, and it names the tab you are actually on so the
         shell can announce and focus it on navigation. -->
    <h1 class="sr-only">Reports — {{ TABS.find((one) => one.value === tab)?.label }}</h1>
    <div class="px-4 pt-3 sm:px-6">
      <UiTabs v-model="tab" :tabs="TABS" label="Reports" />
    </div>
    <MonthEndView v-if="tab === 'month-end'" />
    <SalesView v-else-if="tab === 'sales'" />
    <StaffingView v-else />
  </div>
</template>
