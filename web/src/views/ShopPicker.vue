<script setup lang="ts">
/**
 * The screen nobody should see.
 *
 * The router sends `/` straight to the first shop it finds, so this only
 * renders when there is no shop to send you to: a fresh install with nothing
 * connected, or an API that is not answering. Both deserve the next command to
 * run rather than a spinner.
 */
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { api } from '../api/client'
import type { TenantSummary } from '../api/types'

const tenants = ref<TenantSummary[]>([])
const error = ref<string | null>(null)
const loading = ref(true)

onMounted(async () => {
  try {
    tenants.value = await api.tenants()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <main class="mx-auto flex min-h-screen max-w-lg flex-col justify-center px-6 py-16">
    <h1 class="display text-3xl font-semibold tracking-tight text-ink">
      AIRA<span class="text-primary">.</span>
    </h1>
    <p class="mt-1 text-sm text-ink-muted">The shop's analyst, whatever the shop runs on.</p>

    <p v-if="loading" class="mt-8 text-sm text-ink-muted">Looking for connected shops…</p>

    <div v-else-if="error" class="mt-8 border border-border bg-surface p-5">
      <p class="text-sm text-ink">The API did not answer.</p>
      <p class="mt-1 text-sm text-ink-muted">{{ error }}</p>
      <pre
        class="tabular mt-3 overflow-x-auto border-l-2 border-border bg-raised px-3 py-2 text-sm text-ink-muted"
>python tasks.py db
python tasks.py api</pre
      >
    </div>

    <ul v-else-if="tenants.length" class="mt-8 border border-border bg-surface">
      <li v-for="tenant in tenants" :key="tenant.tenant" class="border-b border-border last:border-0">
        <RouterLink
          :to="{ name: 'home', params: { tenant: tenant.tenant } }"
          class="flex items-baseline justify-between px-4 py-3 hover:bg-raised"
        >
          <span class="font-medium text-ink">{{ tenant.name }}</span>
          <span class="tabular text-sm text-ink-muted">{{ tenant.timezone }}</span>
        </RouterLink>
      </li>
    </ul>

    <div v-else class="mt-8 border border-border bg-surface p-5">
      <p class="text-sm text-ink">No shops are connected yet.</p>
      <pre
        class="tabular mt-3 overflow-x-auto border-l-2 border-border bg-raised px-3 py-2 text-sm text-ink-muted"
>python tasks.py sources
python tasks.py seed
python tasks.py backfill</pre
      >
    </div>
  </main>
</template>
