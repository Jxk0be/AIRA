/**
 * Routes are `/:tenant/<screen>`, so a link carries the shop with it.
 *
 * `/` redirects to the first shop this install knows about rather than to a
 * hardcoded slug: the same build has to work for a demo with two fake shops
 * and for a deployment with one real one.
 */

import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { useTenantStore } from './stores/tenant'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'home',
    // Only ever rendered when there is nowhere to send you: no shops
    // connected, or the API is not answering.
    component: () => import('./views/ShopPicker.vue'),
  },
  {
    path: '/:tenant',
    children: [
      { path: '', redirect: { name: 'dashboard' } },
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('./views/DashboardView.vue'),
        meta: { title: 'Dashboard' },
      },
      {
        path: 'assistant',
        name: 'assistant',
        component: () => import('./views/AssistantView.vue'),
        meta: { title: 'Assistant' },
      },
      {
        path: 'insights',
        name: 'insights',
        component: () => import('./views/InsightsView.vue'),
        meta: { title: 'Worth doing' },
      },
      {
        path: 'reorder',
        name: 'reorder',
        component: () => import('./views/ReorderView.vue'),
        meta: { title: 'Reorder' },
      },
      {
        path: 'dead-stock',
        name: 'dead-stock',
        component: () => import('./views/DeadStockView.vue'),
        meta: { title: 'Dead stock' },
      },
      {
        path: 'staffing',
        name: 'staffing',
        component: () => import('./views/StaffingView.vue'),
        meta: { title: 'Staffing' },
      },
      {
        path: 'month-end',
        name: 'month-end',
        component: () => import('./views/MonthEndView.vue'),
        meta: { title: 'Reports' },
      },
      {
        path: 'inventory',
        name: 'inventory',
        component: () => import('./views/InventoryView.vue'),
        meta: { title: 'Inventory' },
      },
      {
        path: 'data',
        name: 'data',
        component: () => import('./views/DataView.vue'),
        meta: { title: 'Data & sync' },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const store = useTenantStore()
  const wanted = typeof to.params.tenant === 'string' ? to.params.tenant : null

  // `/` points at a slug that may not exist here. Send it to whatever does.
  if (to.name === 'home' || !wanted) {
    const tenants = await store.loadTenants().catch(() => [])
    const first = tenants[0]
    return first ? { name: 'dashboard', params: { tenant: first.tenant } } : true
  }

  await store.select(wanted)
  document.title = `${store.name || wanted} — ${String(to.meta.title ?? 'AIRA')}`
  return true
})
