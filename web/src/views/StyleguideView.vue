<script setup lang="ts">
/**
 * Every token, primitive and state in one place, in both themes. Dev only —
 * `router.ts` drops the route and this import from a production build.
 *
 * It exists so a change to the design system can be judged in one screen
 * instead of by clicking through nine of them, and so the states that are hard
 * to reach on a real page — loading, empty, error, a toast that will not
 * auto-dismiss — are always one click away.
 */
import { ref } from 'vue'

import ThemeToggle from '../ui/ThemeToggle.vue'
import UiBadge from '../ui/UiBadge.vue'
import UiButton from '../ui/UiButton.vue'
import UiDialog from '../ui/UiDialog.vue'
import UiSheet from '../ui/UiSheet.vue'
import UiSkeleton from '../ui/UiSkeleton.vue'
import UiTabs from '../ui/UiTabs.vue'
import { toast } from '../ui/toast'

const dialogOpen = ref(false)
const sheetOpen = ref(false)
const tab = ref('all')

/**
 * Written out in full rather than interpolated.
 *
 * Tailwind v4 generates a utility only when it finds the literal string in the
 * source, so `bg-${name}` yields an unstyled swatch — which on a page whose
 * whole job is showing colors is the one bug that hides itself.
 */
const SURFACES = [
  { name: '--bg', box: 'bg-bg' },
  { name: '--surface', box: 'bg-surface' },
  { name: '--raised', box: 'bg-raised' },
] as const

const ROLES = [
  { name: 'primary', solid: 'bg-primary text-primary-fg', subtle: 'bg-primary-subtle' },
  { name: 'success', solid: 'bg-success text-success-fg', subtle: 'bg-success-subtle' },
  { name: 'warning', solid: 'bg-warning text-warning-fg', subtle: 'bg-warning-subtle' },
  { name: 'danger', solid: 'bg-danger text-danger-fg', subtle: 'bg-danger-subtle' },
  { name: 'info', solid: 'bg-info text-info-fg', subtle: 'bg-info-subtle' },
] as const

const CHARTS = [
  { n: 1, swatch: 'bg-chart-1' },
  { n: 2, swatch: 'bg-chart-2' },
  { n: 3, swatch: 'bg-chart-3' },
  { n: 4, swatch: 'bg-chart-4' },
  { n: 5, swatch: 'bg-chart-5' },
  { n: 6, swatch: 'bg-chart-6' },
] as const
const SIZES = [
  ['text-xs', '13px — the floor'],
  ['text-sm', '14px'],
  ['text-base', '16px — body'],
  ['text-lg', '17px'],
  ['text-xl', '21px'],
  ['text-2xl', '27px'],
] as const
</script>

<template>
  <div class="mx-auto max-w-4xl px-4 py-8 sm:px-6">
    <header class="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="display text-2xl font-semibold text-ink">Styleguide</h1>
        <p class="mt-1 text-ink-muted">
          Direction A — “Counter”. Every color here passed
          <code class="rounded-sm bg-raised px-1 py-0.5 font-mono text-sm">tasks.py ui-check</code>.
        </p>
      </div>
      <ThemeToggle />
    </header>

    <section class="mb-10">
      <h2 class="mb-3 text-lg font-semibold text-ink">Surfaces and ink</h2>
      <div class="grid gap-3 sm:grid-cols-3">
        <div
          v-for="surface in SURFACES"
          :key="surface.name"
          class="rounded-md border border-border p-4"
          :class="surface.box"
        >
          <p class="font-mono text-sm text-ink-muted">{{ surface.name }}</p>
          <p class="mt-1 text-ink">ink</p>
          <p class="text-ink-muted">ink-muted</p>
        </div>
      </div>
      <div class="mt-3 flex flex-wrap gap-3">
        <span class="rounded-md border border-border px-3 py-2 text-sm">border</span>
        <span class="rounded-md border border-border-strong px-3 py-2 text-sm">border-strong (3:1)</span>
        <button class="min-h-11 rounded-md border border-border-strong px-3 text-sm">Focus me (Tab)</button>
      </div>
    </section>

    <section class="mb-10">
      <h2 class="mb-3 text-lg font-semibold text-ink">Roles</h2>
      <div class="grid gap-3 sm:grid-cols-5">
        <div v-for="role in ROLES" :key="role.name">
          <div class="rounded-t-md px-3 py-3 text-sm font-semibold" :class="role.solid">
            {{ role.name }}
          </div>
          <div
            class="rounded-b-md border border-t-0 border-border px-3 py-2 text-sm"
            :class="role.subtle"
          >
            <span class="text-ink">subtle</span>
          </div>
        </div>
      </div>
    </section>

    <section class="mb-10">
      <h2 class="mb-3 text-lg font-semibold text-ink">Chart series</h2>
      <p class="mb-3 text-sm text-ink-muted">
        Each ≥3:1 on the surface, ≥20 ΔE apart, ≥11 ΔE apart under protanopia,
        deuteranopia and tritanopia.
      </p>
      <div class="flex flex-wrap gap-3">
        <span v-for="series in CHARTS" :key="series.n" class="flex items-center gap-2 text-sm">
          <i class="h-5 w-5 rounded-sm" :class="series.swatch" />chart-{{ series.n }}
        </span>
      </div>
    </section>

    <section class="mb-10">
      <h2 class="mb-3 text-lg font-semibold text-ink">Type scale</h2>
      <p v-for="[cls, note] in SIZES" :key="cls" :class="cls" class="text-ink">
        {{ cls }} — <span class="text-ink-muted">{{ note }}</span>
      </p>
      <p class="tabular mt-3 text-2xl font-semibold text-ink">$12,160.45 · 412 · $29.52</p>
      <p class="text-sm text-ink-muted">Tabular figures, so a column lines up.</p>
    </section>

    <section class="mb-10">
      <h2 class="mb-3 text-lg font-semibold text-ink">Buttons</h2>
      <div class="flex flex-wrap items-center gap-2">
        <UiButton>Count these items</UiButton>
        <UiButton variant="secondary">See what to do</UiButton>
        <UiButton variant="ghost">Dismiss</UiButton>
        <UiButton variant="danger">Delete</UiButton>
        <UiButton loading>Working</UiButton>
        <UiButton disabled>Disabled</UiButton>
        <UiButton size="sm" variant="secondary">Small</UiButton>
      </div>
    </section>

    <section class="mb-10">
      <h2 class="mb-3 text-lg font-semibold text-ink">Badges</h2>
      <div class="flex flex-wrap gap-2">
        <UiBadge tone="danger">Urgent</UiBadge>
        <UiBadge tone="warning">Worth a look</UiBadge>
        <UiBadge tone="info">Note</UiBadge>
        <UiBadge tone="success">Acted on</UiBadge>
        <UiBadge>Dismissed</UiBadge>
      </div>
    </section>

    <section class="mb-10">
      <h2 class="mb-3 text-lg font-semibold text-ink">Toasts</h2>
      <p class="mb-3 text-sm text-ink-muted">
        Errors do not auto-dismiss; the rest clear themselves. Every one carries
        an icon and a spoken word, never color alone.
      </p>
      <div class="flex flex-wrap gap-2">
        <UiButton size="sm" variant="secondary" @click="toast.success('2 draft orders ready to review')">
          Success
        </UiButton>
        <UiButton size="sm" variant="secondary" @click="toast.info('Nothing new since the last check')">
          Info
        </UiButton>
        <UiButton size="sm" variant="secondary" @click="toast.warning('3 lines have no cost on file')">
          Warning
        </UiButton>
        <UiButton
          size="sm"
          variant="secondary"
          @click="
            toast.danger('Could not reach the shop', {
              detail: 'The API did not answer.',
              action: { label: 'Try again', run: () => toast.success('Reconnected') },
            })
          "
        >
          Error, with retry
        </UiButton>
      </div>
    </section>

    <section class="mb-10">
      <h2 class="mb-3 text-lg font-semibold text-ink">Overlays</h2>
      <div class="flex flex-wrap gap-2">
        <UiButton variant="secondary" @click="dialogOpen = true">Open dialog</UiButton>
        <UiButton variant="secondary" @click="sheetOpen = true">Open sheet</UiButton>
      </div>

      <UiDialog
        v-model:open="dialogOpen"
        title="Delete this conversation?"
        description="This cannot be undone."
      >
        <p class="text-ink-muted">Focus is trapped here and returns to the button on close.</p>
        <template #footer="{ close }">
          <UiButton variant="ghost" @click="close()">Keep it</UiButton>
          <UiButton variant="danger" @click="close(); toast.success('Conversation deleted')">
            Delete
          </UiButton>
        </template>
      </UiDialog>

      <UiSheet v-model:open="sheetOpen" title="History">
        <ul class="divide-y divide-border">
          <li v-for="n in 6" :key="n" class="px-4 py-3">
            <p class="font-medium text-ink">December sales</p>
            <p class="text-sm text-ink-muted">15 minutes ago</p>
          </li>
        </ul>
      </UiSheet>
    </section>

    <section class="mb-10">
      <h2 class="mb-3 text-lg font-semibold text-ink">Tabs</h2>
      <UiTabs
        v-model="tab"
        label="Stock"
        :tabs="[
          { value: 'all', label: 'All items' },
          { value: 'reorder', label: 'Reorder' },
          { value: 'not-selling', label: 'Not selling' },
        ]"
      >
        <p class="py-4 text-ink-muted">Showing: {{ tab }}</p>
      </UiTabs>
    </section>

    <section class="mb-10">
      <h2 class="mb-3 text-lg font-semibold text-ink">Loading and empty</h2>
      <div class="grid gap-4 sm:grid-cols-2">
        <div class="rounded-md border border-border bg-surface p-4" aria-busy="true">
          <UiSkeleton :lines="4" />
        </div>
        <div class="rounded-md border border-border bg-surface p-6 text-center">
          <p class="font-medium text-ink">Nothing is sitting still</p>
          <p class="mt-1 text-sm text-ink-muted">Unusual, and good.</p>
          <UiButton class="mt-3" size="sm" variant="secondary">Check again</UiButton>
        </div>
      </div>
    </section>
  </div>
</template>
