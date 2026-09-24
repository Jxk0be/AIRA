<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { shopDate } from './lib/format'
import { useTenantStore } from './stores/tenant'

const shop = useTenantStore()
const route = useRoute()
const router = useRouter()

const screens = [
  { name: 'dashboard', label: 'Dashboard', hint: 'How trade is going' },
  { name: 'insights', label: 'Worth doing', hint: 'What we noticed' },
  { name: 'assistant', label: 'Assistant', hint: 'Ask about the shop' },
  { name: 'reorder', label: 'Reorder', hint: 'What to buy' },
  { name: 'dead-stock', label: 'Dead stock', hint: 'Money sitting still' },
  { name: 'inventory', label: 'Inventory', hint: 'What is on the shelf' },
  { name: 'staffing', label: 'Staffing', hint: 'When you are busy' },
  { name: 'month-end', label: 'Reports', hint: 'For the bookkeeper' },
  { name: 'data', label: 'Data & sync', hint: 'Where this comes from' },
] as const

const slug = computed(() => (typeof route.params.tenant === 'string' ? route.params.tenant : ''))
const onShell = computed(() => Boolean(slug.value))

/**
 * `main` is the scroll container, not the window.
 *
 * That is what lets the assistant keep its composer on screen while only the
 * conversation scrolls — including on a phone, where the nav is a bar above it
 * rather than a rail beside it. The cost is having to reset the scroll on a
 * route change, which the window would have done by itself.
 */
const scroller = ref<HTMLElement | null>(null)
watch(
  () => route.fullPath,
  () => scroller.value?.scrollTo({ top: 0 }),
)

function switchShop(event: Event) {
  const next = (event.target as HTMLSelectElement).value
  // Stay on the same screen: switching shops mid-demo should not bounce you
  // back to the dashboard.
  const name = typeof route.name === 'string' ? route.name : 'dashboard'
  void router.push({ name, params: { tenant: next } })
}
</script>

<template>
  <div v-if="!onShell"><RouterView /></div>

  <div v-else class="flex h-[100dvh] flex-col lg:grid lg:grid-cols-[15rem_minmax(0,1fr)]">
    <header
      class="shrink-0 border-b border-rule bg-panel/70 lg:h-[100dvh] lg:border-r lg:border-b-0 lg:bg-transparent"
    >
      <div class="flex h-full flex-col gap-5 px-4 py-4 lg:px-5 lg:py-6">
        <div class="flex items-center justify-between gap-3">
          <RouterLink
            :to="{ name: 'dashboard', params: { tenant: slug } }"
            class="display text-[1.45rem] leading-none font-semibold tracking-tight text-ink"
          >
            AIRA<span class="text-brand">.</span>
          </RouterLink>
          <span
            class="tabular hidden text-[0.62rem] tracking-[0.18em] text-ink-faint uppercase lg:inline"
          >
            Analyst
          </span>
        </div>

        <!-- Dev-only: real deployments get one shop per login. -->
        <label class="block">
          <span class="sr-only">Shop</span>
          <div class="relative">
            <select
              :value="slug"
              class="w-full appearance-none rounded-sm border border-rule bg-panel py-2 pr-8 pl-3 text-sm font-medium text-ink shadow-[0_1px_0_var(--rule)] hover:border-rule-strong focus:border-brand focus:outline-none"
              @change="switchShop"
            >
              <option v-for="option in shop.tenants" :key="option.tenant" :value="option.tenant">
                {{ option.name }}
              </option>
            </select>
            <svg
              class="pointer-events-none absolute top-1/2 right-3 h-3 w-3 -translate-y-1/2 text-ink-faint"
              viewBox="0 0 12 12"
              aria-hidden="true"
            >
              <path d="M2 4.5 6 8.5 10 4.5" fill="none" stroke="currentColor" stroke-width="1.4" />
            </svg>
          </div>
        </label>

        <nav class="-mx-1 flex gap-1 overflow-x-auto lg:mx-0 lg:flex-col lg:gap-0.5">
          <RouterLink
            v-for="screen in screens"
            :key="screen.name"
            :to="{ name: screen.name, params: { tenant: slug } }"
            class="group relative shrink-0 rounded-sm px-3 py-1.5 text-sm whitespace-nowrap text-ink-muted transition-colors hover:bg-sunk hover:text-ink lg:py-2"
            active-class="!text-ink bg-sunk lg:bg-transparent font-medium"
          >
            <span
              class="absolute top-1/2 -left-px hidden h-4 w-[2px] -translate-y-1/2 bg-brand opacity-0 transition-opacity lg:block"
              :class="{ 'opacity-100': route.name === screen.name }"
            />
            {{ screen.label }}
            <span class="hidden text-xs text-ink-faint lg:block">{{ screen.hint }}</span>
          </RouterLink>
        </nav>

        <div class="mt-auto hidden lg:block">
          <div class="border-t border-rule pt-3 text-xs text-ink-faint">
            <p v-if="shop.profile?.data_to" class="tabular">
              Data through {{ shopDate(shop.profile.data_to, shop.timezone) }}
            </p>
            <p class="mt-1">{{ shop.timezone.replace('_', ' ') }}</p>
          </div>
        </div>
      </div>
    </header>

    <main ref="scroller" class="min-h-0 min-w-0 flex-1 overflow-y-auto lg:h-[100dvh]">
      <p
        v-if="shop.error"
        class="m-4 border-l-2 border-down bg-panel px-4 py-3 text-sm text-ink"
        role="alert"
      >
        {{ shop.error }}
      </p>
      <RouterView v-else v-slot="{ Component }">
        <component :is="Component" :key="slug" />
      </RouterView>
    </main>
  </div>
</template>
