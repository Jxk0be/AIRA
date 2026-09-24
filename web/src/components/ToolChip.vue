<script setup lang="ts">
/**
 * One tool call, collapsed.
 *
 * A question that takes six seconds has to show its working, or it looks
 * broken. Collapsed it says what is happening in words; opened it shows the
 * arguments and exactly what came back — which is the only way to answer "where
 * did that number come from" without reading the server log.
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

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

/** Long results are still shown in full — just not all at once. */
const preview = () => (props.result ?? '').slice(0, 4000)
</script>

<template>
  <div class="border-l border-rule pl-3 text-xs">
    <button
      type="button"
      class="flex w-full items-baseline gap-2 py-0.5 text-left text-ink-muted hover:text-ink"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span
        class="inline-block h-1.5 w-1.5 shrink-0 translate-y-[-1px] rounded-full"
        :class="error ? 'bg-down' : running ? 'bg-brand working' : 'bg-brand/60'"
        aria-hidden="true"
      />
      <span class="truncate">{{ toolLabel(tool) }}</span>
      <span v-if="ms" class="tabular ml-auto shrink-0 text-ink-faint">{{ duration(ms) }}</span>
    </button>

    <div v-if="open" class="mt-1 mb-2 space-y-1.5">
      <pre
        v-if="args && Object.keys(args).length"
        class="tabular overflow-x-auto bg-sunk px-2 py-1.5 text-[0.7rem] leading-snug text-ink-muted"
        >{{ pretty(args) }}</pre
      >
      <p v-if="error" class="text-down">{{ error }}</p>
      <pre
        v-else-if="result"
        class="tabular max-h-60 overflow-auto bg-sunk px-2 py-1.5 text-[0.7rem] leading-snug text-ink-muted"
        >{{ preview() }}</pre
      >
    </div>
  </div>
</template>
