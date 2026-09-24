<script setup lang="ts">
/**
 * Policies, FAQs, event schedules — the things the assistant can quote that no
 * POS knows about.
 *
 * Split out of the Data & sync screen, where it sat in a corner as a card with
 * an "Upload" link for a label. Uploading a document is its own job, so it gets
 * its own tab and a real control.
 *
 * An upload is searchable straight away: only its own passages are embedded, so
 * adding one policy does not re-embed the catalogue.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../api/client'
import type { DataScreen } from '../api/types'
import SectionCard from '../components/SectionCard.vue'
import { count } from '../lib/format'
import UiButton from '../ui/UiButton.vue'
import { toast } from '../ui/toast'

const route = useRoute()
const slug = computed(() => String(route.params.tenant ?? ''))

const screen = ref<DataScreen | null>(null)
const loading = ref(true)
const uploading = ref(false)
const error = ref<string | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)

async function load() {
  loading.value = true
  try {
    screen.value = await api.data(slug.value)
    error.value = null
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  uploading.value = true
  try {
    const result = await api.uploadDocument(slug.value, file)
    toast.success(`Added “${result.title}”`, {
      detail: `${result.chunks_embedded} passages indexed and searchable now.`,
    })
    await load()
  } catch (cause) {
    toast.danger('Could not add that document', {
      detail: cause instanceof Error ? cause.message : undefined,
    })
  } finally {
    uploading.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

onMounted(load)
watch(slug, load)
</script>

<template>
  <div class="mx-auto max-w-3xl px-4 py-5 sm:px-6">
    <div v-if="error" class="rounded-lg border border-danger bg-danger-subtle p-4" role="alert">
      <p class="font-medium text-ink">We could not load your documents.</p>
      <p class="mt-1 text-sm text-ink-muted">{{ error }}</p>
      <UiButton class="mt-3" size="sm" variant="secondary" @click="load">Try again</UiButton>
    </div>

    <SectionCard
      v-else
      title="Documents"
      subtitle="Policies, FAQs, event schedules"
      :loading="loading"
      :empty="!loading && !screen?.documents.length"
      empty-text="Nothing uploaded yet. Add a policy or an event schedule and the assistant can quote it."
    >
      <template #actions>
        <!-- A label styled as the button, so the file input stays native and
             the control is still a 44px target. -->
        <label
          class="inline-flex min-h-11 cursor-pointer items-center rounded-md border border-border-strong bg-surface px-4 text-base font-semibold text-ink hover:bg-raised focus-within:outline-3 focus-within:outline-offset-2 focus-within:outline-focus"
        >
          {{ uploading ? 'Uploading…' : 'Add a document' }}
          <input
            ref="fileInput"
            type="file"
            accept=".md,.txt,.pdf"
            class="sr-only"
            :disabled="uploading"
            @change="upload"
          />
        </label>
      </template>

      <ul class="divide-y divide-border">
        <li
          v-for="document in screen?.documents ?? []"
          :key="document.id"
          class="flex items-baseline justify-between gap-3 py-2.5"
        >
          <span class="min-w-0 truncate text-base text-ink">{{ document.title }}</span>
          <span class="tabular shrink-0 text-sm text-ink-muted">
            {{ count(document.chunks) }} passages
          </span>
        </li>
      </ul>
    </SectionCard>

    <p class="mt-3 text-sm text-ink-muted">
      Markdown, plain text or PDF. An upload is searchable straight away — only its own passages are
      embedded, so adding one policy does not re-embed the catalogue.
    </p>
  </div>
</template>
