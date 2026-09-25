<script setup lang="ts">
/**
 * The frame every screen lives in.
 *
 * On a phone this is most of what makes an app feel like an app, so the shape
 * is different at each size rather than one layout squeezed: a bottom tab bar
 * under the thumb on mobile, a sidebar on desktop. Both render the same five
 * destinations in the same order, so the two teach the same structure.
 *
 * Four things here are not decoration:
 *
 *   The skip link is the first tab stop on every screen. Without it, reaching
 *   the content by keyboard meant tabbing past the whole navigation, every time.
 *
 *   Focus moves to the page heading on a route change and the new title is
 *   announced. A router that only swaps the DOM leaves a screen-reader user's
 *   focus where it was, on a control that no longer exists, with no sign
 *   anything happened.
 *
 *   The active tab is marked by a filled icon, a heavier label and a rule —
 *   never color alone.
 *
 *   `main` is the scroll container rather than the window, which is what keeps
 *   the Ask composer above the mobile keyboard. The cost is that the browser
 *   has no scroll position to restore, so this records one per history entry.
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, type RouteLocationRaw } from 'vue-router'

import { useTenantStore } from '../stores/tenant'
import { api } from '../api/client'
import ThemeToggle from './ThemeToggle.vue'

const shop = useTenantStore()
const route = useRoute()

const slug = computed(() => (typeof route.params.tenant === 'string' ? route.params.tenant : ''))

interface Destination {
  name: string
  label: string
  /** Two paths: outline when resting, filled when current. */
  icon: string
  filled?: string
}

const DESTINATIONS: Destination[] = [
  { name: 'home', label: 'Home', icon: 'M3 10.5 12 3l9 7.5M5.5 9.5V21h13V9.5' },
  { name: 'ask', label: 'Ask', icon: 'M21 12a8 8 0 1 1-3.2-6.4M12 8v4l3 2' },
  { name: 'stock', label: 'Stock', icon: 'M4 7h16v13H4zM4 7l2-3h12l2 3M9 11h6' },
  { name: 'reports', label: 'Reports', icon: 'M5 3h10l4 4v14H5zM8 13h8M8 17h5' },
  {
    name: 'settings',
    label: 'Settings',
    icon: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M19.1 4.9 17 7M7 17l-2.1 2.1',
  },
]

/** Stock, Reports and Settings are one destination each, tabs and all. */
function isCurrent(name: string): boolean {
  return route.name === name
}

/**
 * Where a nav link points, including before there is a shop to point at.
 *
 * The shell paints before the router has resolved a tenant. On a cold load the
 * first navigation is still inside `beforeEach` waiting on the API, so `route`
 * is the start location and `route.params` is empty. Building a link as
 * `{ name, params: { tenant: '' } }` then is not something vue-router can
 * resolve: it throws `Missing required param "tenant"` from `RouterLink`'s own
 * setup — a render error, not a warning — once per link, on every cold load.
 * The app recovers when the real route arrives, which is exactly what makes it
 * worth fixing: a console that cries wolf on every load is a console nobody
 * reads.
 *
 * Point at `/` for that one frame instead. The nav keeps its shape, and `/` is
 * where an early click belongs anyway — the guard sends it to the same shop the
 * pending navigation is already on its way to.
 */
function to(name: string): RouteLocationRaw {
  return slug.value ? { name, params: { tenant: slug.value } } : '/'
}

// ---------------------------------------------------------------- the badge
const openFindings = ref(0)

async function countFindings() {
  if (!slug.value) return
  try {
    const inbox = await api.insights(slug.value, { status: 'open' })
    openFindings.value = inbox.insights.length
  } catch {
    // A badge is not worth an error message. Worth doing says so itself.
    openFindings.value = 0
  }
}

// ------------------------------------------------- focus, titles and scroll
const scroller = ref<HTMLElement | null>(null)
const announcement = ref('')
const sidebarOpen = ref(true)

/** Keyed by history entry, so Back returns to where you actually were. */
const positions = new Map<string, number>()
let leaving = ''

/**
 * Focus is only moved *between* pages, never on arrival.
 *
 * On a fresh load the right place for focus is nowhere — so the first Tab
 * reaches the skip link, which is the whole point of having one. Grabbing the
 * heading on arrival steals that. The first navigation this watcher sees is the
 * redirect from `/` to the shop, which is arrival, not a move.
 */
let arrived = false

function rememberScroll() {
  if (leaving && scroller.value) positions.set(leaving, scroller.value.scrollTop)
}

watch(
  () => route.fullPath,
  async (path) => {
    rememberScroll()
    leaving = path

    await nextTick()
    const restored = positions.get(path) ?? 0
    scroller.value?.scrollTo({ top: restored })

    if (!arrived) {
      arrived = true
      void countFindings()
      return
    }

    // Announce first, then move focus: a screen reader that is mid-sentence on
    // the heading will not also read the live region.
    announcement.value = String(route.meta.title ?? '')
    await nextTick()

    const heading = scroller.value?.querySelector<HTMLElement>('h1')
    if (heading) {
      // -1, not 0: the heading should take focus on navigation without joining
      // the tab order afterwards.
      heading.setAttribute('tabindex', '-1')
      heading.focus({ preventScroll: true })
    }

    void countFindings()
  },
)

onMounted(() => {
  leaving = route.fullPath
  void countFindings()
})
</script>

<template>
  <!--
    One root element on purpose. With the skip link, the live region and the
    layout as three sibling roots, Vue's fragment patching duplicated the first
    two on every re-render — four skip links and four live regions on a page
    that should have one of each.

    It also owns the height and clips. Before, nothing *told* the document not
    to scroll; it simply happened not to, because the layout was exactly one
    viewport tall and `main` clipped its own overflow. Anything that made the
    page a pixel taller — a browser resolving `dvh` differently, a chart tooltip
    placed outside its container — scrolled the whole document instead, taking
    the sidebar up with it and leaving dead space below. `overflow-hidden` here
    makes that impossible rather than merely unlikely.
  -->
  <div class="h-[100dvh] overflow-hidden">
    <a
      href="#main"
      class="sr-only rounded-md bg-primary font-semibold text-primary-fg focus:not-sr-only focus:absolute focus:top-3 focus:left-3 focus:z-50 focus:px-4 focus:py-3"
    >
      Skip to content
    </a>

    <!-- Polite, and only ever the page name: anything chattier competes with the
         heading that is about to be read. -->
    <p aria-live="polite" class="sr-only">{{ announcement }}</p>

    <div class="flex h-full flex-col lg:flex-row">
      <!-- ------------------------------------------------------- desktop rail
           Its own scroll container, so the rail never moves when the page does. -->
      <aside
        class="hidden shrink-0 overflow-y-auto border-r border-border bg-surface lg:flex lg:flex-col"
        :class="sidebarOpen ? 'lg:w-60' : 'lg:w-16'"
      >
        <div class="flex items-center gap-2 px-3 py-4" :class="sidebarOpen ? '' : 'justify-center'">
          <RouterLink
            v-if="sidebarOpen"
            :to="to('home')"
            class="display min-w-0 truncate text-base font-bold text-ink"
          >
            {{ shop.name }}
          </RouterLink>
          <button
            type="button"
            class="ml-auto grid h-11 w-11 place-items-center rounded-md text-ink-muted hover:bg-raised hover:text-ink"
            :aria-expanded="sidebarOpen"
            :aria-label="sidebarOpen ? 'Collapse navigation' : 'Expand navigation'"
            @click="sidebarOpen = !sidebarOpen"
          >
            <svg viewBox="0 0 24 24" class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
        </div>

        <nav aria-label="Main" class="px-2">
          <RouterLink
            v-for="destination in DESTINATIONS"
            :key="destination.name"
            :to="to(destination.name)"
            class="mb-0.5 flex min-h-11 items-center gap-3 rounded-md px-3 text-base font-medium transition-colors"
            :class="
              isCurrent(destination.name)
                ? 'bg-primary-subtle font-semibold text-primary'
                : 'text-ink-muted hover:bg-raised hover:text-ink'
            "
            :aria-current="isCurrent(destination.name) ? 'page' : undefined"
            :title="sidebarOpen ? undefined : destination.label"
          >
            <svg
              class="h-5 w-5 flex-none"
              viewBox="0 0 24 24"
              :fill="isCurrent(destination.name) ? 'currentColor' : 'none'"
              :fill-opacity="isCurrent(destination.name) ? 0.14 : 0"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path :d="destination.icon" />
            </svg>
            <span v-if="sidebarOpen" class="min-w-0 flex-1 truncate">{{ destination.label }}</span>
            <span
              v-if="sidebarOpen && destination.name === 'home' && openFindings > 0"
              class="rounded-full bg-danger px-2 py-0.5 text-xs font-bold text-danger-fg"
            >
              {{ openFindings }}
              <span class="sr-only">new actions</span>
            </span>
          </RouterLink>
        </nav>

        <div v-if="sidebarOpen" class="mt-auto border-t border-border p-3">
          <ThemeToggle />
        </div>
      </aside>

      <div class="flex min-h-0 min-w-0 flex-1 flex-col">
        <!-- --------------------------------------------------------- top bar -->
        <header
          class="flex shrink-0 items-center gap-3 border-b border-border bg-bg px-4 py-2.5 sm:px-6"
          :style="{ paddingTop: 'max(0.625rem, env(safe-area-inset-top, 0px))' }"
        >
          <div class="min-w-0 flex-1">
            <p class="truncate text-lg font-bold text-ink lg:text-xl">
              {{ route.meta.title ?? shop.name }}
            </p>
            <p class="truncate text-sm text-ink-muted lg:hidden">{{ shop.name }}</p>
          </div>

          <!-- One contextual action per page. Views teleport into it in step 6. -->
          <div id="page-action" class="flex shrink-0 items-center gap-2"></div>
        </header>

        <!--
          Most screens scroll inside `main` and leave room for the tab bar. Ask
          sets `meta.fills`, takes the whole frame and does its own scrolling,
          so `main` must not scroll underneath it or pad below it — two
          scrollbars on one screen is how a conversation ends up half off-view.
        -->
        <main
          id="main"
          ref="scroller"
          class="min-h-0 min-w-0 flex-1"
          :class="
            route.meta.fills
              ? 'overflow-hidden'
              : 'overflow-y-auto pb-[calc(4.5rem+env(safe-area-inset-bottom,0px))] lg:pb-0'
          "
        >
          <p
            v-if="shop.error"
            class="m-4 rounded-md border border-danger bg-danger-subtle px-4 py-3 text-ink"
            role="alert"
          >
            {{ shop.error }}
          </p>
          <RouterView v-else v-slot="{ Component }">
            <component :is="Component" :key="slug" />
          </RouterView>
        </main>
      </div>

      <!-- ----------------------------------------------------- mobile tab bar -->
      <nav
        aria-label="Main"
        class="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-border bg-surface lg:hidden"
        :style="{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }"
      >
        <RouterLink
          v-for="destination in DESTINATIONS"
          :key="destination.name"
          :to="to(destination.name)"
          class="relative flex min-h-[3.25rem] flex-col items-center justify-center gap-0.5 pt-1.5 text-xs"
          :class="isCurrent(destination.name) ? 'font-bold text-primary' : 'font-medium text-ink-muted'"
          :aria-current="isCurrent(destination.name) ? 'page' : undefined"
        >
          <!-- Not color alone: a rule above, a filled icon, a heavier label. -->
          <span
            v-if="isCurrent(destination.name)"
            class="absolute inset-x-0 top-0 mx-auto h-0.5 w-8 rounded-b bg-primary"
            aria-hidden="true"
          />
          <span class="relative">
            <svg
              class="h-6 w-6"
              viewBox="0 0 24 24"
              :fill="isCurrent(destination.name) ? 'currentColor' : 'none'"
              :fill-opacity="isCurrent(destination.name) ? 0.16 : 0"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path :d="destination.icon" />
            </svg>
            <span
              v-if="destination.name === 'home' && openFindings > 0"
              class="absolute -top-1 -right-2 grid h-[1.15rem] min-w-[1.15rem] place-items-center rounded-full bg-danger px-1 text-xs font-bold text-danger-fg"
            >
              {{ openFindings }}
            </span>
          </span>
          {{ destination.label }}
          <span v-if="destination.name === 'home' && openFindings > 0" class="sr-only">
            {{ openFindings }} new actions
          </span>
        </RouterLink>
      </nav>
    </div>
  </div>
</template>
