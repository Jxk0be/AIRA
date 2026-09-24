/**
 * Five destinations, and every old path still works.
 *
 * Routes are `/:tenant/<screen>`, so a link carries the shop with it.
 *
 * `/` redirects to the first shop this install knows about rather than to a
 * hardcoded slug: the same build has to work for a demo with two fake shops and
 * for a deployment with one real one.
 *
 * ## Why the redirects are not optional
 *
 * Every finding the backend writes carries a `suggested_action.route` — a bare
 * string like `reorder` or `dashboard?day=2026-09-17` — and those strings are
 * already sitting in the `insights` table. `digest/build.py` builds the weekly
 * email's links from the same values, so they are also in inboxes we cannot
 * edit. Renaming a route without leaving a redirect behind silently breaks
 * findings the owner has not actioned yet and every link in every email already
 * sent. The detectors stay untouched; this file absorbs the rename.
 */

import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { useTenantStore } from './stores/tenant'

/** Old path -> where it lives now. Query strings are carried across. */
const MOVED: Record<string, { name: string; query?: Record<string, string> }> = {
  // `worth-doing` existed only between steps 5 and 6, while the findings list
  // was being folded into Home. Anyone who bookmarked it in that window still
  // lands somewhere sensible.
  'worth-doing': { name: 'home' },
  dashboard: { name: 'home' },
  insights: { name: 'home' },
  assistant: { name: 'ask' },
  inventory: { name: 'stock', query: { tab: 'all' } },
  reorder: { name: 'stock', query: { tab: 'reorder' } },
  'dead-stock': { name: 'stock', query: { tab: 'not-selling' } },
  staffing: { name: 'reports', query: { tab: 'busy-hours' } },
  'month-end': { name: 'reports', query: { tab: 'month-end' } },
  data: { name: 'settings', query: { tab: 'data' } },
}

const moved = (from: string): RouteRecordRaw => ({
  path: from,
  redirect: (to) => {
    const target = MOVED[from]!
    return {
      name: target.name,
      params: { tenant: to.params.tenant },
      query: { ...target.query, ...to.query },
    }
  },
})

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'root',
    // Only ever rendered when there is nowhere to send you: no shops connected,
    // or the API is not answering.
    component: () => import('./views/ShopPicker.vue'),
  },
  {
    path: '/:tenant',
    children: [
      {
        path: '',
        name: 'home',
        component: () => import('./views/HomeView.vue'),
        meta: { title: 'Home' },
      },
      {
        path: 'ask',
        name: 'ask',
        // `fills` means the screen manages its own height and scrolling. Ask is
        // the only one: the conversation scrolls while the composer stays put,
        // which is what keeps it above the mobile keyboard.
        component: () => import('./views/AskView.vue'),
        meta: { title: 'Ask', fills: true },
      },
      {
        path: 'stock',
        name: 'stock',
        component: () => import('./views/StockView.vue'),
        meta: { title: 'Stock' },
      },
      {
        path: 'reports',
        name: 'reports',
        component: () => import('./views/ReportsView.vue'),
        meta: { title: 'Reports' },
      },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('./views/SettingsView.vue'),
        meta: { title: 'Settings' },
      },
      ...Object.keys(MOVED).map(moved),
    ],
  },
  // Dev only: every token, primitive and state in one place, in both themes.
  // `import.meta.env.DEV` is statically replaced, so the route and the view it
  // lazily imports are both dropped from a production build.
  ...(import.meta.env.DEV
    ? [
        {
          path: '/styleguide',
          name: 'styleguide',
          component: () => import('./views/StyleguideView.vue'),
          meta: { title: 'Styleguide' },
        } as RouteRecordRaw,
      ]
    : []),
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
  /**
   * `main` is the scroll container, not the window — that is what keeps the Ask
   * composer above the mobile keyboard — so the browser's own restoration has
   * nothing to restore. `AppShell` records and replays the position instead;
   * this only handles an in-page anchor.
   */
  scrollBehavior: (to) => (to.hash ? { el: to.hash } : false),
})

router.beforeEach(async (to) => {
  const store = useTenantStore()
  const wanted = typeof to.params.tenant === 'string' ? to.params.tenant : null

  // The styleguide is not a shop screen: it has no tenant and must not be
  // bounced to one.
  if (to.name === 'styleguide') return true

  // `/` points at a slug that may not exist here. Send it to whatever does.
  if (to.name === 'root' || !wanted) {
    const tenants = await store.loadTenants().catch(() => [])
    const first = tenants[0]
    return first ? { name: 'home', params: { tenant: first.tenant } } : true
  }

  await store.select(wanted)
  document.title = `${store.name || wanted} — ${String(to.meta.title ?? 'AIRA')}`
  return true
})
