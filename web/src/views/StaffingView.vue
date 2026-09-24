<script setup lang="ts">
/**
 * When the shop is busy, against when somebody is in.
 *
 * Eight weeks of the shop's own tills, by weekday and hour in the shop's own
 * timezone, with convention days pulled out — one con weekend would otherwise
 * be the busiest hour of the week and every observation below it would be
 * about that weekend rather than about the shop.
 *
 * The suggestions are phrased as observations and that is not politeness.
 * "Tuesdays 11–1 averaged 1.2 orders an hour over eight weeks" is a fact about
 * the tills. "Cut a shift on Tuesday" is a decision about somebody's job, and
 * this screen does not have the standing to make it.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../api/client'
import type { Heatmap, StaffingScreen } from '../api/types'
import PanelCard from '../components/PanelCard.vue'
import { money, quantity } from '../lib/format'
import { useTenantStore } from '../stores/tenant'

const route = useRoute()
const shop = useTenantStore()

const slug = computed(() => String(route.params.tenant ?? ''))
const screen = ref<StaffingScreen | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)

const HOURS = Array.from({ length: 15 }, (_, index) => index + 8) // 08:00–22:00

const draft = ref({ weekday: 0, start_time: '10:00', end_time: '18:00', staff_count: 1 })

const grid = computed<Heatmap | null>(() => screen.value?.heatmaps[0] ?? null)

/** The busiest cell sets the scale, so the colours mean something. */
const peak = computed(() =>
  Math.max(1, ...(grid.value?.cells ?? []).map((cell) => Number(cell.orders_per_week))),
)

function cell(weekday: number, hour: number) {
  return grid.value?.cells.find((row) => row.weekday === weekday && row.hour === hour) ?? null
}

function shade(weekday: number, hour: number): string {
  const found = cell(weekday, hour)
  if (!found) return 'opacity: 0.06'
  return `opacity: ${(0.1 + 0.9 * (Number(found.orders_per_week) / peak.value)).toFixed(3)}`
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
  notice.value = null
  try {
    await api.saveShift(slug.value, { ...draft.value })
    notice.value = 'Rota updated.'
    await load()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  }
}

async function removeShift(id: string | null) {
  if (!id) return
  await api.deleteShift(slug.value, id)
  await load()
}

onMounted(load)
watch(slug, load)
</script>

<template>
  <div class="mx-auto max-w-5xl px-4 py-6 lg:px-8">
    <header class="mb-5">
      <h1 class="display text-2xl font-semibold tracking-tight text-ink">Staffing</h1>
      <p class="mt-0.5 text-sm text-ink-muted">
        Eight weeks of your own tills, by day and hour. Event days are left out.
      </p>
    </header>

    <p v-if="error" class="mb-4 border-l-2 border-down bg-panel px-4 py-3 text-sm text-ink" role="alert">
      {{ error }}
    </p>
    <p v-if="notice" class="mb-4 border-l-2 border-brand bg-panel px-4 py-3 text-sm text-ink">
      {{ notice }}
    </p>

    <PanelCard
      title="When people come in"
      :subtitle="grid ? `${grid.location_name} · ${grid.weeks} weeks` : undefined"
      :loading="loading"
      :empty="!loading && !grid"
      empty-text="Not enough history yet to draw a pattern."
      class="mb-4"
    >
      <div class="overflow-x-auto">
        <table class="text-xs">
          <thead>
            <tr>
              <th class="py-1 pr-2 text-left font-normal text-ink-faint">Hour</th>
              <th
                v-for="name in grid?.weekday_names ?? []"
                :key="name"
                class="px-1 py-1 text-center font-normal text-ink-faint"
                :title="name"
              >
                {{ name.slice(0, 3) }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="hour in HOURS" :key="hour">
              <th class="tabular py-0.5 pr-2 text-right font-normal text-ink-faint">
                {{ String(hour).padStart(2, '0') }}
              </th>
              <td v-for="weekday in 7" :key="weekday" class="px-0.5 py-0.5">
                <div
                  class="relative h-5 w-9 bg-sunk"
                  :title="`${grid?.weekday_names[weekday - 1]} ${hour}:00 — ${
                    cell(weekday - 1, hour)
                      ? `${quantity(cell(weekday - 1, hour)!.orders_per_week)} orders a week`
                      : 'nothing'
                  }`"
                >
                  <div class="absolute inset-0 bg-brand" :style="shade(weekday - 1, hour)"></div>
                  <!-- A hairline for an hour somebody is rostered on: the gap
                       between the two is the whole point of the screen. -->
                  <div
                    v-if="staffed(weekday - 1, hour)"
                    class="absolute inset-x-0 bottom-0 h-px bg-ink"
                  ></div>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="mt-2 text-xs text-ink-faint">
        Darker is busier. The line under an hour means somebody is rostered on.
        <template v-if="grid?.event_days_excluded">
          {{ grid.event_days_excluded }} event day{{ grid.event_days_excluded === 1 ? '' : 's' }}
          left out.
        </template>
      </p>
    </PanelCard>

    <PanelCard
      title="Worth noticing"
      :loading="loading"
      :empty="!loading && !screen?.observations.length"
      empty-text="Nothing stands out — or no rota has been entered to compare against."
      class="mb-4"
    >
      <ul class="space-y-2 text-sm">
        <li v-for="(observation, index) in screen?.observations ?? []" :key="index">
          <span class="text-ink">{{ observation.sentence }}</span>
          <span v-if="observation.labour_cost" class="ml-1 text-ink-faint">
            ({{ money(observation.labour_cost, shop.currency) }} a week)
          </span>
        </li>
      </ul>
      <p class="mt-3 text-xs text-ink-faint">
        These are observations about your tills, not recommendations about anybody's hours.
      </p>
    </PanelCard>

    <PanelCard title="Who is on" subtitle="Typed in by you — no POS knows the rota">
      <ul v-if="screen?.shifts.length" class="mb-3 divide-y divide-rule text-sm">
        <li
          v-for="shift in screen.shifts"
          :key="shift.id ?? `${shift.weekday}-${shift.start_time}`"
          class="flex items-baseline justify-between gap-3 py-1.5"
        >
          <span class="text-ink">
            {{ (grid?.weekday_names ?? [])[shift.weekday] ?? shift.weekday }}
            {{ shift.start_time.slice(0, 5) }}–{{ shift.end_time.slice(0, 5) }}
          </span>
          <span class="text-ink-muted">
            {{ shift.staff_count }} on
          </span>
          <button type="button" class="text-xs text-ink-faint hover:text-down" @click="removeShift(shift.id)">
            Remove
          </button>
        </li>
      </ul>

      <form class="flex flex-wrap items-end gap-2" @submit.prevent="addShift">
        <label class="text-xs text-ink-faint">
          Day
          <select
            v-model.number="draft.weekday"
            class="mt-0.5 block rounded-sm border border-rule bg-panel px-2 py-1 text-sm text-ink"
          >
            <option v-for="(name, index) in grid?.weekday_names ?? []" :key="name" :value="index">
              {{ name }}
            </option>
          </select>
        </label>
        <label class="text-xs text-ink-faint">
          From
          <input
            v-model="draft.start_time"
            type="time"
            class="mt-0.5 block rounded-sm border border-rule bg-panel px-2 py-1 text-sm text-ink"
          />
        </label>
        <label class="text-xs text-ink-faint">
          To
          <input
            v-model="draft.end_time"
            type="time"
            class="mt-0.5 block rounded-sm border border-rule bg-panel px-2 py-1 text-sm text-ink"
          />
        </label>
        <label class="text-xs text-ink-faint">
          People
          <input
            v-model.number="draft.staff_count"
            type="number"
            min="1"
            max="20"
            class="tabular mt-0.5 block w-16 rounded-sm border border-rule bg-panel px-2 py-1 text-sm text-ink"
          />
        </label>
        <button type="submit" class="rounded-sm bg-brand px-3 py-1.5 text-sm font-medium text-white">
          Add
        </button>
      </form>
    </PanelCard>
  </div>
</template>
