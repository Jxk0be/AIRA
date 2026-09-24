/**
 * Which shop is on screen, and what it can do.
 *
 * Capabilities live here because almost every screen asks the same question of
 * them — "should this widget exist at all?" — and the answer has to be the same
 * everywhere. A shop with no customer records should not see a repeat-rate
 * card on one screen and an empty one on another.
 *
 * The tenant is part of the URL rather than a global toggle, so a link to a
 * screen is a link to that shop's screen.
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api } from '../api/client'
import type { Capabilities, ShopProfile, TenantSummary } from '../api/types'

export const useTenantStore = defineStore('tenant', () => {
  const tenants = ref<TenantSummary[]>([])
  const profile = ref<ShopProfile | null>(null)
  const slug = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const current = computed(() => tenants.value.find((row) => row.tenant === slug.value) ?? null)
  const name = computed(() => profile.value?.name ?? current.value?.name ?? slug.value ?? '')
  const currency = computed(() => profile.value?.currency ?? current.value?.currency ?? 'USD')
  const timezone = computed(() => profile.value?.timezone ?? current.value?.timezone ?? 'UTC')

  const capabilities = computed<Capabilities>(
    () =>
      profile.value?.capabilities ??
      current.value?.capabilities ?? {
        has_costs: false,
        has_customers: false,
        has_inventory_history: false,
        multi_location: false,
        has_online_channel: false,
        supports_incremental: false,
      },
  )

  function can(capability: keyof Capabilities): boolean {
    return Boolean(capabilities.value[capability])
  }

  async function loadTenants(): Promise<TenantSummary[]> {
    if (tenants.value.length) return tenants.value
    tenants.value = await api.tenants()
    return tenants.value
  }

  /** Point the app at a shop. Idempotent, so route guards can call it freely. */
  async function select(next: string): Promise<void> {
    if (slug.value === next && profile.value) return
    slug.value = next
    loading.value = true
    error.value = null
    try {
      await loadTenants()
      profile.value = await api.profile(next)
    } catch (cause) {
      profile.value = null
      error.value = cause instanceof Error ? cause.message : String(cause)
    } finally {
      loading.value = false
    }
  }

  return {
    tenants,
    profile,
    slug,
    loading,
    error,
    current,
    name,
    currency,
    timezone,
    capabilities,
    can,
    loadTenants,
    select,
  }
})
