<script setup lang="ts">
/**
 * A breakdown, biggest first, with the share drawn behind the row.
 *
 * A bar chart with ten product names on the axis is unreadable at 375px, and a
 * table of ten numbers hides which one matters. A ranked list with the share as
 * a tint behind the line reads as both.
 */
import type { BreakdownRow } from '../api/types'
import { money, percent } from '../lib/format'

const props = defineProps<{
  rows: BreakdownRow[]
  currency: string
  limit?: number
}>()

const shown = () => (props.limit ? props.rows.slice(0, props.limit) : props.rows)

/** Scaled to the biggest row, so the top line always fills its width. */
function width(row: BreakdownRow): string {
  const biggest = Math.max(...props.rows.map((one) => Math.abs(Number(one.net_sales))), 1)
  return `${Math.max(0, (Number(row.net_sales) / biggest) * 100)}%`
}
</script>

<template>
  <ol class="space-y-px">
    <li
      v-for="(row, index) in shown()"
      :key="row.key ?? row.label"
      class="relative flex items-baseline justify-between gap-3 rounded-sm px-2 py-2 text-base"
    >
      <span
        class="absolute inset-y-0 left-0 rounded-sm bg-primary-subtle"
        :style="{ width: width(row) }"
        aria-hidden="true"
      />
      <span class="relative flex min-w-0 items-baseline gap-2">
        <span class="tabular w-5 shrink-0 text-sm text-ink-muted">{{ index + 1 }}</span>
        <span class="truncate text-ink">{{ row.label }}</span>
      </span>
      <span class="relative flex shrink-0 items-baseline gap-3">
        <span v-if="row.share_of_net_sales !== null" class="tabular text-sm text-ink-muted">
          {{ percent(row.share_of_net_sales, 0) }}
        </span>
        <span class="tabular font-medium text-ink">{{ money(row.net_sales, currency) }}</span>
      </span>
    </li>
  </ol>
</template>
