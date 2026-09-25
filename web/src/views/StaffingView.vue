<script setup lang="ts">
/**
 * When the shop is busy, against when somebody is in.
 *
 * Eight weeks of the shop's own tills, by weekday and hour in the shop's own
 * timezone, with convention days pulled out — one con weekend would otherwise
 * be the busiest hour of the week and every observation below it would be about
 * that weekend rather than about the shop.
 *
 * The suggestions are phrased as observations and that is not politeness.
 * "Tuesdays 11–1 averaged 1.2 orders an hour over eight weeks" is a fact about
 * the tills. "Cut a shift on Tuesday" is a decision about somebody's job, and
 * this screen does not have the standing to make it.
 *
 * ## The heatmap had to be rebuilt, not restyled
 *
 * It encoded everything in `opacity`, with the number hidden in a `title` on a
 * `<div>` — which is not keyboard-reachable and is read out inconsistently at
 * best (audit A5). A color ramp is a fine *summary*, but it cannot be the only
 * copy of the data.
 *
 * So: the grid is a real `<table>` with row and column headers, every cell
 * states its own figure in text for a screen reader, and the busyness is given
 * as five named bands — "Busiest", "Busy", "Steady", "Quiet", "Closed" — so the
 * pattern survives being unable to compare two shades of green. Rostered hours
 * carry a mark and the word "rostered" rather than a hairline nobody can see.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../api/client'
import type { Heatmap, StaffingScreen } from '../api/types'
import SectionCard from '../components/SectionCard.vue'
import { money, quantity } from '../lib/format'
import { useTenantStore } from '../stores/tenant'
import UiButton from '../ui/UiButton.vue'
import { withToast } from '../ui/toast'

const route = useRoute()
const shop = useTenantStore()

const slug = computed(() => String(route.params.tenant ?? ''))
const screen = ref<StaffingScreen | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

const HOURS = Array.from({ length: 15 }, (_, index) => index + 8) // 08:00–22:00

const draft = ref({ weekday: 0, start_time: '10:00', end_time: '18:00', staff_count: 1 })

const grid = computed<Heatmap | null>(() => screen.value?.heatmaps[0] ?? null)

/** The busiest cell sets the scale, so the bands mean something. */
const peak = computed(() =>
  Math.max(1, ...(grid.value?.cells ?? []).map((cell) => Number(cell.orders_per_week))),
)

function cell(weekday: number, hour: number) {
  return grid.value?.cells.find((row) => row.weekday === weekday && row.hour === hour) ?? null
}

/**
 * Five named bands rather than a continuous ramp.
 *
 * A reader who cannot tell 40%-opacity green from 55% can still tell "Busy"
 * from "Steady", and the exact figure is in the cell's own label either way.
 */
const BANDS = [
  { name: 'Busiest', at: 0.75, shade: 'bg-primary', text: 'text-primary-fg' },
  { name: 'Busy', at: 0.5, shade: 'bg-primary/70', text: 'text-ink' },
  { name: 'Steady', at: 0.25, shade: 'bg-primary/45', text: 'text-ink' },
  { name: 'Quiet', at: 0.01, shade: 'bg-primary/20', text: 'text-ink' },
  { name: 'Closed', at: -1, shade: 'bg-raised', text: 'text-ink-muted' },
] as const

function band(weekday: number, hour: number) {
  const found = cell(weekday, hour)
  const share = found ? Number(found.orders_per_week) / peak.value : 0
  return BANDS.find((one) => share > one.at) ?? BANDS[BANDS.length - 1]!
}

/** What a screen reader reads for one cell. */
function cellLabel(weekday: number, hour: number): string {
  const found = cell(weekday, hour)
  const day = grid.value?.weekday_names[weekday] ?? String(weekday)
  const rate = found ? `${quantity(found.orders_per_week)} orders a week` : 'no orders'
  const who = staffed(weekday, hour) ? ', rostered' : ''
  return `${day} ${hour}:00, ${band(weekday, hour).name}, ${rate}${who}`
}

function staffed(weekday: number, hour: number): boolean {
  return (screen.value?.shifts ?? []).some(
    (shift) =>
      shift.weekday === weekday &&
      Number(shift.start_time.slice(0, 2)) <= hour &&
      hour < Number(shift.end_time.slice(0, 2)),
  )
}

async function load() {
  try {
    screen.value = await api.staffing(slug.value)
    error.value = null
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

async function addShift() {
  const done = await withToast(() => api.saveShift(slug.value, { ...draft.value }), {
    success: 'Rota updated',
    failure: 'Could not save that shift',
  })
  if (done !== undefined) await load()
}

async function removeShift(id: string | null) {
  if (!id) return
  const done = await withToast(() => api.deleteShift(slug.value, id), {
    success: 'Shift removed',
    failure: 'Could not remove that shift',
  })
  if (done !== undefined) await load()
}

onMounted(load)
watch(slug, load)
</script>

<template>
  <div class="mx-auto max-w-4xl px-4 py-5 sm:px-6">
    <p class="mb-4 text-sm text-ink-muted">
      Eight weeks of your own tills, by day and hour. Event days are left out.
    </p>

    <div v-if="error" class="rounded-lg border border-danger bg-danger-subtle p-4" role="alert">
      <p class="font-medium text-ink">We could not load the pattern.</p>
      <p class="mt-1 text-sm text-ink-muted">{{ error }}</p>
      <UiButton class="mt-3" size="sm" variant="secondary" @click="load">Try again</UiButton>
    </div>

    <template v-else>
      <SectionCard
        class="mb-4"
        title="When people come in"
        :subtitle="grid ? `${grid.location_name} · ${grid.weeks} weeks` : undefined"
        :loading="loading"
        :empty="!loading && !grid"
        empty-text="Not enough history yet to draw a pattern."
      >
        <!-- A real table: row headers are hours, column headers are days, and
             every cell says its own figure. Focusable, so it can be scrolled
             and read without a mouse. -->
        <div class="overflow-x-auto" tabindex="0" role="region" aria-label="Busy hours grid">
          <table class="w-full text-sm">
            <caption class="sr-only">
              Orders per week by weekday and hour, over
              {{ grid?.weeks ?? 8 }} weeks. Each cell gives its band and figure.
            </caption>
            <thead>
              <tr>
                <th scope="col" class="py-1 pr-2 text-left font-medium text-ink-muted">Hour</th>
                <th
                  v-for="name in grid?.weekday_names ?? []"
                  :key="name"
                  scope="col"
                  class="px-1 py-1 text-center font-medium text-ink-muted"
                >
                  <abbr :title="name" class="no-underline">{{ name.slice(0, 3) }}</abbr>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="hour in HOURS" :key="hour">
                <th scope="row" class="tabular py-0.5 pr-2 text-right font-normal text-ink-muted">
                  {{ String(hour).padStart(2, '0') }}
                </th>
                <td v-for="weekday in 7" :key="weekday" class="px-0.5 py-0.5">
                  <div
                    class="relative grid h-7 min-w-9 place-items-center rounded-sm text-xs font-semibold"
                    :class="[band(weekday - 1, hour).shade, band(weekday - 1, hour).text]"
                  >
                    <span class="sr-only">{{ cellLabel(weekday - 1, hour) }}</span>
                    <!-- A mark, not a hairline: rostered has to survive being
                         unable to see a 1px line. -->
                    <span v-if="staffed(weekday - 1, hour)" aria-hidden="true">•</span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <span v-for="one in BANDS" :key="one.name" class="flex items-center gap-1.5">
            <i class="h-4 w-4 rounded-sm" :class="one.shade" aria-hidden="true" />
            <span class="text-ink-muted">{{ one.name }}</span>
          </span>
          <span class="flex items-center gap-1.5">
            <span class="text-ink" aria-hidden="true">•</span>
            <span class="text-ink-muted">somebody rostered on</span>
          </span>
        </div>

        <p v-if="grid?.event_days_excluded" class="mt-2 text-sm text-ink-muted">
          {{ grid.event_days_excluded }} event day{{ grid.event_days_excluded === 1 ? '' : 's' }}
          left out.
        </p>
      </SectionCard>

      <SectionCard
        class="mb-4"
        title="Worth noticing"
        :loading="loading"
        :empty="!loading && !screen?.observations.length"
        empty-text="Nothing stands out — or no rota has been entered to compare against."
      >
        <ul class="space-y-2">
          <li v-for="(observation, index) in screen?.observations ?? []" :key="index" class="text-base">
            <span class="text-ink">{{ observation.sentence }}</span>
            <span v-if="observation.labour_cost" class="text-ink-muted">
              ({{ money(observation.labour_cost, shop.currency) }} a week)
            </span>
          </li>
        </ul>
        <p class="mt-3 text-sm text-ink-muted">
          These are observations about your tills, not recommendations about anybody's hours.
        </p>
      </SectionCard>

      <SectionCard title="Who is on" subtitle="Typed in by you — no POS knows the rota">
        <ul v-if="screen?.shifts.length" class="mb-3 divide-y divide-border">
          <li
            v-for="shift in screen.shifts"
            :key="shift.id ?? `${shift.weekday}-${shift.start_time}`"
            class="flex items-center justify-between gap-3 py-1.5"
          >
            <span class="text-base text-ink">
              {{ (grid?.weekday_names ?? [])[shift.weekday] ?? shift.weekday }}
              {{ shift.start_time.slice(0, 5) }}–{{ shift.end_time.slice(0, 5) }}
            </span>
            <span class="text-base text-ink-muted">{{ shift.staff_count }} on</span>
            <UiButton size="sm" variant="ghost" @click="removeShift(shift.id)">Remove</UiButton>
          </li>
        </ul>

        <form class="flex flex-wrap items-end gap-3" @submit.prevent="addShift">
          <label class="text-sm text-ink-muted">
            Day
            <select
              v-model.number="draft.weekday"
              class="mt-1 block min-h-11 rounded-md border border-border-strong bg-surface px-2 text-base text-ink"
            >
              <option v-for="(name, index) in grid?.weekday_names ?? []" :key="name" :value="index">
                {{ name }}
              </option>
            </select>
          </label>
          <label class="text-sm text-ink-muted">
            From
            <input
              v-model="draft.start_time"
              type="time"
              class="mt-1 block min-h-11 rounded-md border border-border-strong bg-surface px-2 text-base text-ink"
            />
          </label>
          <label class="text-sm text-ink-muted">
            To
            <input
              v-model="draft.end_time"
              type="time"
              class="mt-1 block min-h-11 rounded-md border border-border-strong bg-surface px-2 text-base text-ink"
            />
          </label>
          <label class="text-sm text-ink-muted">
            People
            <input
              v-model.number="draft.staff_count"
              type="number"
              min="1"
              max="20"
              class="tabular mt-1 block min-h-11 w-20 rounded-md border border-border-strong bg-surface px-2 text-base text-ink"
            />
          </label>
          <UiButton type="submit">Add</UiButton>
        </form>
      </SectionCard>
    </template>
  </div>
</template>
