<script setup lang="ts">
/**
 * A low-stock or dead-stock list.
 *
 * Both are the same rows sorted by a different worry, so they are the same
 * table with a different last column: what is running out shows how long the
 * shelf lasts, what is sitting still shows how long it has sat.
 */
import type { StockRow } from '../api/types'
import { days, money, quantity, shopDate } from '../lib/format'

defineProps<{
  rows: StockRow[]
  currency: string
  timezone: string
  /** 'cover' for reordering, 'idle' for dead stock. */
  mode: 'cover' | 'idle'
}>()

function name(row: StockRow): string {
  if (!row.variant_name || row.variant_name === row.product_name) return row.product_name
  return `${row.product_name} — ${row.variant_name}`
}
</script>

<template>
  <div class="-mx-1 overflow-x-auto">
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b border-rule text-left text-xs text-ink-faint">
          <th class="px-1 pb-1.5 font-medium">Item</th>
          <th class="px-1 pb-1.5 text-right font-medium">On hand</th>
          <th class="px-1 pb-1.5 text-right font-medium">Value</th>
          <th class="px-1 pb-1.5 text-right font-medium">
            {{ mode === 'cover' ? 'Cover' : 'Last sold' }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.variant_id" class="border-b border-rule/50 last:border-0">
          <td class="max-w-[13rem] px-1 py-1.5">
            <span class="block truncate text-ink">{{ name(row) }}</span>
            <span v-if="row.sku" class="tabular block truncate text-xs text-ink-faint">
              {{ row.sku }}
            </span>
          </td>
          <td class="tabular px-1 py-1.5 text-right whitespace-nowrap">
            {{ quantity(row.units_on_hand) }}
          </td>
          <td class="tabular px-1 py-1.5 text-right whitespace-nowrap">
            {{ money(row.retail_value, currency) }}
          </td>
          <td class="tabular px-1 py-1.5 text-right whitespace-nowrap text-ink-muted">
            <template v-if="mode === 'cover'">
              <!-- No sales in the window means no rate, so no projection. -->
              {{ row.days_of_cover === null ? '—' : days(row.days_of_cover) }}
            </template>
            <template v-else>
              {{ row.last_sold_at ? shopDate(row.last_sold_at, timezone) : 'never' }}
            </template>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
