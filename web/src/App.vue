<script setup lang="ts">
import { onMounted, ref } from 'vue'

interface DatabaseHealth {
  reachable: boolean
  error: string | null
  server_version: string | null
  pgvector: boolean | null
  pgvector_version: string | null
}

interface Health {
  status: 'ok' | 'degraded'
  service: string
  version: string
  database: DatabaseHealth
  embedding_model: string
  embedding_dim: number
}

const health = ref<Health | null>(null)
const error = ref<string | null>(null)
const loading = ref(true)

async function load() {
  loading.value = true
  error.value = null
  try {
    const response = await fetch('/api/health')
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    health.value = (await response.json()) as Health
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    health.value = null
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <main class="mx-auto max-w-xl p-8 font-sans">
    <h1 class="text-3xl font-bold tracking-tight">AIRA</h1>
    <p class="mt-1 text-sm text-ink-muted">Multi-tenant AI analyst — scaffold</p>

    <section class="mt-8 rounded-lg border border-hairline bg-surface-muted p-5">
      <div class="flex items-center justify-between">
        <h2 class="font-semibold">API health</h2>
        <button
          class="rounded border border-hairline px-3 py-1 text-sm hover:bg-surface"
          :disabled="loading"
          @click="load"
        >
          {{ loading ? 'Checking…' : 'Refresh' }}
        </button>
      </div>

      <p v-if="error" class="mt-4 text-sm text-red-500">
        Could not reach the API: {{ error }}
      </p>

      <dl v-else-if="health" class="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt class="text-ink-muted">Status</dt>
        <dd :class="health.status === 'ok' ? 'text-green-500' : 'text-amber-500'">
          {{ health.status }}
        </dd>
        <dt class="text-ink-muted">Database</dt>
        <dd>
          {{ health.database.reachable ? `Postgres ${health.database.server_version}` : health.database.error }}
        </dd>
        <dt class="text-ink-muted">pgvector</dt>
        <dd>
          {{ health.database.pgvector ? `enabled (${health.database.pgvector_version})` : 'not enabled' }}
        </dd>
        <dt class="text-ink-muted">Embeddings</dt>
        <dd>{{ health.embedding_model }} @ {{ health.embedding_dim }}d</dd>
      </dl>
    </section>
  </main>
</template>
