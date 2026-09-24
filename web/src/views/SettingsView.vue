<script setup lang="ts">
/**
 * Where the data comes from, and how this looks.
 *
 * "Data & sync" was a top-level destination, which is where nobody looks for a
 * theme or an email address. It is settings, so it lives under Settings.
 *
 * Notifications and Documents were buried at the bottom of the month-end and
 * data screens, which is where nobody looks for an email address or a policy
 * document. They are settings, so they are tabs here. Appearance is new,
 * because until now there was nowhere at all to choose a theme.
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ThemeToggle from '../ui/ThemeToggle.vue'
import UiTabs from '../ui/UiTabs.vue'
import { useTenantStore } from '../stores/tenant'
import DataView from './DataView.vue'
import DocumentsView from './DocumentsView.vue'
import NotificationsView from './NotificationsView.vue'

const route = useRoute()
const router = useRouter()
const shop = useTenantStore()

const TABS = [
  { value: 'data', label: 'Data & sync' },
  { value: 'documents', label: 'Documents' },
  { value: 'notifications', label: 'Notifications' },
  { value: 'appearance', label: 'Appearance' },
]

const tab = computed({
  get: () => {
    const wanted = String(route.query.tab ?? 'data')
    return TABS.some((one) => one.value === wanted) ? wanted : 'data'
  },
  set: (value) => void router.replace({ query: { ...route.query, tab: value } }),
})

/**
 * Dev-only, and no longer occupying the top of every screen on a phone.
 *
 * A real deployment is one shop per login, so this must not shape the
 * navigation. When Supabase Auth lands it becomes "the shops you may see" and
 * moves into the account menu; see docs/ui/ia.md.
 */
function switchShop(event: Event) {
  const next = (event.target as HTMLSelectElement).value
  void router.push({ name: 'settings', params: { tenant: next }, query: route.query })
}
</script>

<template>
  <div>
    <!-- The container owns the page heading, not the three panels inside it:
         one <h1> per screen, and it names the tab you are actually on so the
         shell can announce and focus it on navigation. -->
    <h1 class="sr-only">Settings — {{ TABS.find((one) => one.value === tab)?.label }}</h1>
    <div class="px-4 pt-3 sm:px-6">
      <UiTabs v-model="tab" :tabs="TABS" label="Settings" />
    </div>

    <DataView v-if="tab === 'data'" />
    <DocumentsView v-else-if="tab === 'documents'" />
    <NotificationsView v-else-if="tab === 'notifications'" />

    <div v-else class="mx-auto max-w-2xl px-4 py-6 sm:px-6">
      <section class="rounded-md border border-border bg-surface p-5">
        <h2 class="text-lg font-semibold text-ink">Theme</h2>
        <p class="mt-1 mb-4 text-ink-muted">
          Light unless you say otherwise. “System” follows the phone or laptop.
        </p>
        <ThemeToggle />
      </section>

      <section class="mt-4 rounded-md border border-border bg-surface p-5">
        <h2 class="text-lg font-semibold text-ink">Business colour</h2>
        <p class="mt-1 text-ink-muted">
          Not built yet. It will be stored per shop, so it follows you to your
          phone — and any colour you pick has to clear 4.5:1 against both themes
          before it is accepted, or the buttons become unreadable in the dark.
        </p>
      </section>

      <section v-if="shop.tenants.length > 1" class="mt-4 rounded-md border border-border bg-surface p-5">
        <h2 class="text-lg font-semibold text-ink">Shop</h2>
        <p class="mt-1 mb-3 text-ink-muted">
          A development convenience. A real deployment is one shop per login.
        </p>
        <label class="block">
          <span class="sr-only">Choose a shop</span>
          <select
            :value="shop.slug ?? ''"
            class="min-h-11 w-full rounded-md border border-border-strong bg-surface px-3 text-base text-ink"
            @change="switchShop"
          >
            <option v-for="option in shop.tenants" :key="option.tenant" :value="option.tenant">
              {{ option.name }}
            </option>
          </select>
        </label>
      </section>
    </div>
  </div>
</template>
