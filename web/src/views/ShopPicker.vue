<script setup lang="ts">
/**
 * The screen nobody should see for long.
 *
 * The router sends `/` straight to the first shop this person may open, so this
 * only renders when there is nowhere to send them. There are now three reasons
 * for that, and they want different words:
 *
 * * **Signed in, no memberships.** The normal case for a brand-new account.
 *   Nothing is broken and there is no command they can run — somebody at their
 *   shop has to add them. Sign-out is offered, because arriving here with the
 *   wrong account is the other reason to be looking at it.
 * * **No shops in the database at all.** A fresh install. The next commands to
 *   run are the useful answer.
 * * **The API is not answering.** Same.
 *
 * The list is still rendered when there is more than one shop, because a
 * bookkeeper with two clients is a real case and `/` is where they land.
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { api } from '../api/client'
import type { TenantSummary } from '../api/types'
import { useAuthStore } from '../stores/auth'
import UiButton from '../ui/UiButton.vue'

const auth = useAuthStore()
const tenants = ref<TenantSummary[]>([])
const error = ref<string | null>(null)
const loading = ref(true)

/** Signed in, the API answered, and it listed nothing for us. */
const noMemberships = computed(
  () => !loading.value && !error.value && tenants.value.length === 0 && auth.signedIn,
)

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

    <p v-if="loading" class="mt-8 text-sm text-ink-muted">Looking for your shops…</p>

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
      <li
        v-for="tenant in tenants"
        :key="tenant.tenant"
        class="border-b border-border last:border-0"
      >
        <RouterLink
          :to="{ name: 'home', params: { tenant: tenant.tenant } }"
          class="flex items-baseline justify-between px-4 py-3 hover:bg-raised"
        >
          <span class="font-medium text-ink">{{ tenant.name }}</span>
          <span class="text-sm text-ink-muted">{{ tenant.role }}</span>
        </RouterLink>
      </li>
    </ul>

    <div v-else-if="noMemberships" class="mt-8 border border-border bg-surface p-5">
      <p class="text-sm text-ink">You are signed in, but not added to a shop yet.</p>
      <p class="mt-1 text-sm text-ink-muted">
        Ask whoever owns the shop in AIRA to add
        <span class="font-medium text-ink">{{ auth.email }}</span>. Nothing else is needed —
        the account is ready.
      </p>
      <UiButton class="mt-4" variant="secondary" size="sm" @click="auth.signOut()">
        Sign out
      </UiButton>
    </div>

    <div v-else class="mt-8 border border-border bg-surface p-5">
      <p class="text-sm text-ink">No shops are connected yet.</p>
      <pre
        class="tabular mt-3 overflow-x-auto border-l-2 border-border bg-raised px-3 py-2 text-sm text-ink-muted"
>python tasks.py sources
python tasks.py seed
python tasks.py backfill
python tasks.py invite you@example.com animanga_knox owner</pre
      >
    </div>
  </main>
</template>
