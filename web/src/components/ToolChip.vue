<script setup lang="ts">
/**
 * One tool call, collapsed to a line.
 *
 * A question that takes six seconds has to show its working or it looks broken.
 * Collapsed it says what is happening in words; opened it shows the arguments
 * and exactly what came back — which is the only way to answer "where did that
 * number come from" without reading the server log.
 *
 * It is quiet on purpose. This is the model's working, not the answer, so it
 * sits in muted text at the size of a caption and never competes with the
 * sentence underneath it. The old version set it in 11px, which made "show your
 * working" something you had to squint at.
 */
import { ref } from 'vue'

import { duration, toolLabel } from '../lib/format'

const props = defineProps<{
  tool: string
  args?: Record<string, unknown>
  ms?: number | null
  error?: string | null
  result?: string | null
  running?: boolean
}>()

const open = ref(false)

const pretty = (value: unknown): string => JSON.stringify(value, null, 2)

/** Long results are still shown in full — just not all at once. */
const preview = () => (props.result ?? '').slice(0, 4000)
</script>

<template>
  <div class="text-sm">
    <button
      type="button"
      class="flex min-h-11 w-full items-center gap-2 rounded-sm text-left text-ink-muted hover:text-ink"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span
        class="inline-block h-2 w-2 shrink-0 rounded-full"
        :class="error ? 'bg-danger' : running ? 'working bg-primary' : 'bg-primary/60'"
        aria-hidden="true"
      />
      <span class="truncate">{{ toolLabel(tool) }}</span>
      <span v-if="error" class="shrink-0 font-medium text-danger">failed</span>
      <span v-if="ms" class="tabular ml-auto shrink-0">{{ duration(ms) }}</span>
      <svg
        class="h-4 w-4 shrink-0 transition-transform"
        :class="open ? 'rotate-90' : ''"
        :style="{ transitionDuration: 'var(--duration-fast)' }"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        aria-hidden="true"
      >
        <path d="m6 4 4 4-4 4" />
      </svg>
    </button>

    <div v-if="open" class="mt-1 mb-2 space-y-2">
      <pre
        v-if="args && Object.keys(args).length"
        class="tabular overflow-x-auto rounded-md bg-raised px-3 py-2 text-sm leading-snug text-ink-muted"
        tabindex="0"
        role="region"
        :aria-label="`What ${toolLabel(tool)} was asked`"
        >{{ pretty(args) }}</pre
      >
      <p v-if="error" class="text-sm text-danger">{{ error }}</p>
      <pre
        v-else-if="result"
        class="tabular max-h-64 overflow-auto rounded-md bg-raised px-3 py-2 text-sm leading-snug text-ink-muted"
        tabindex="0"
        role="region"
        :aria-label="`What ${toolLabel(tool)} returned`"
        >{{ preview() }}</pre
      >
    </div>
  </div>
</template>
