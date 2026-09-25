<script setup lang="ts" generic="Row extends object">
/**
 * A table on a desktop, a stack of cards on a phone.
 *
 * The inventory table used to set `min-w-[46rem]` inside a horizontal scroller,
 * which at 375px meant five columns of money and dates and *no item names* —
 * every cell unidentifiable, and the only fix scrolling sideways to find out
 * what you were looking at (audit U8).
 *
 * A table that wide is not a table on a phone, so below `sm` each row becomes a
 * card: the first column is the heading, the rest are label/value pairs. Same
 * data, same order, no sideways scrolling.
 *
 * The column spec is the point. One declaration drives the header, the
 * alignment, the sort affordance and the mobile labels, so a money column
 * cannot be right-aligned in the header and left-aligned in the body — which is
 * how a list stops lining up (audit U6, U7).
 */
export interface Column<Key extends string = string> {
  key: Key
  label: string
  /** Numeric columns are right-aligned and set in tabular figures. */
  numeric?: boolean
  /** Omitted from the card view — for columns that only make sense in a row. */
  desktopOnly?: boolean
  sortable?: boolean
}

const props = defineProps<{
  columns: Column[]
  rows: readonly Row[]
  rowKey: (row: Row, index: number) => string
  /** Read out before the table; required, because an unnamed table is a maze. */
  caption: string
  sortKey?: string
  sortDescending?: boolean
  loading?: boolean
  emptyText?: string
}>()

const emit = defineEmits<{ sort: [key: string] }>()

defineSlots<{
  /** One slot for every cell; switch on `column.key` at the call site. */
  cell(props: { row: Row; column: Column }): unknown
}>()

/** What a screen reader should hear on the header cell. */
function ariaSort(column: Column): 'ascending' | 'descending' | 'none' | undefined {
  if (!column.sortable) return undefined
  if (props.sortKey !== column.key) return 'none'
  return props.sortDescending ? 'descending' : 'ascending'
}
</script>

<template>
  <div>
    <!-- ---------------------------------------------------------- desktop -->
    <div class="hidden sm:block">
      <table class="w-full text-base">
        <caption class="sr-only">{{ caption }}</caption>
        <thead>
          <tr class="border-b border-border">
            <th
              v-for="column in columns"
              :key="column.key"
              scope="col"
              class="px-3 py-2 text-sm font-semibold text-ink-muted"
              :class="column.numeric ? 'text-right' : 'text-left'"
              :aria-sort="ariaSort(column)"
            >
              <button
                v-if="column.sortable"
                type="button"
                class="inline-flex min-h-11 items-center gap-1 rounded-sm hover:text-ink"
                @click="emit('sort', column.key)"
              >
                {{ column.label }}
                <span v-if="sortKey === column.key" class="text-primary" aria-hidden="true">
                  {{ sortDescending ? '↓' : '↑' }}
                </span>
              </button>
              <template v-else>{{ column.label }}</template>
            </th>
          </tr>
        </thead>
        <tbody :aria-busy="loading ? 'true' : 'false'">
          <tr v-if="!loading && !rows.length">
            <td :colspan="columns.length" class="px-3 py-8 text-center text-ink-muted">
              {{ emptyText ?? 'Nothing matches that.' }}
            </td>
          </tr>
          <tr
            v-for="(row, index) in rows"
            :key="rowKey(row, index)"
            class="border-b border-border last:border-0 hover:bg-raised"
          >
            <td
              v-for="column in columns"
              :key="column.key"
              class="px-3 py-2.5 align-top"
              :class="[column.numeric ? 'tabular text-right whitespace-nowrap' : 'text-left']"
            >
              <slot name="cell" :row="row" :column="column" />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- ----------------------------------------------------------- mobile -->
    <div class="sm:hidden">
      <p v-if="!loading && !rows.length" class="px-1 py-8 text-center text-ink-muted">
        {{ emptyText ?? 'Nothing matches that.' }}
      </p>
      <ul v-else class="space-y-2" :aria-busy="loading ? 'true' : 'false'">
        <li
          v-for="(row, index) in rows"
          :key="rowKey(row, index)"
          class="rounded-lg border border-border bg-surface p-3"
        >
          <!-- The first column is what identifies the row, so it is the title. -->
          <div class="text-base font-semibold text-ink">
            <slot name="cell" :row="row" :column="columns[0]!" />
          </div>
          <dl class="mt-2 grid grid-cols-2 gap-x-4 gap-y-1.5">
            <div
              v-for="column in columns.slice(1).filter((one) => !one.desktopOnly)"
              :key="column.key"
              class="flex items-baseline justify-between gap-2"
            >
              <dt class="text-sm text-ink-muted">{{ column.label }}</dt>
              <dd class="text-base text-ink" :class="column.numeric ? 'tabular' : ''">
                <slot name="cell" :row="row" :column="column" />
              </dd>
            </div>
          </dl>
        </li>
      </ul>
    </div>
  </div>
</template>
