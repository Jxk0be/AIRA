<script setup lang="ts">
/**
 * A modal, replacing `window.prompt` and `window.confirm`.
 *
 * Renaming a conversation used to open a browser prompt and deleting one a
 * browser confirm: unstyled, unthemed, and on a phone they read as the browser
 * breaking rather than the app asking. Reka handles what makes a dialog correct
 * rather than merely visible — focus moves in on open and returns to whatever
 * opened it on close, Escape closes, the page behind cannot be tabbed into or
 * scrolled, and the title is wired to `aria-labelledby` for us.
 */
import { DialogClose, DialogContent, DialogDescription, DialogOverlay, DialogPortal, DialogRoot, DialogTitle } from 'reka-ui'

defineProps<{ title: string; description?: string }>()
const open = defineModel<boolean>('open', { required: true })
</script>

<template>
  <DialogRoot v-model:open="open">
    <DialogPortal>
      <DialogOverlay class="ui-overlay fixed inset-0 z-40 bg-ink/40" />
      <DialogContent
        class="ui-panel fixed top-1/2 left-1/2 z-50 w-[min(30rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface p-5 shadow-float"
      >
        <DialogTitle class="text-xl font-semibold text-ink">{{ title }}</DialogTitle>
        <DialogDescription v-if="description" class="mt-1 text-base text-ink-muted">
          {{ description }}
        </DialogDescription>
        <div class="mt-4"><slot /></div>
        <div v-if="$slots.footer" class="mt-5 flex flex-wrap justify-end gap-2">
          <slot name="footer" :close="() => (open = false)" />
        </div>
        <DialogClose
          class="absolute top-2.5 right-2.5 grid h-11 w-11 place-items-center rounded-sm text-ink-muted hover:text-ink"
        >
          <span class="sr-only">Close</span>
          <svg viewBox="0 0 20 20" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 5l10 10M15 5L5 15" /></svg>
        </DialogClose>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>

<style scoped>
.ui-overlay[data-state='open'] { animation: fade-in var(--duration-fast) var(--ease-out); }
.ui-panel[data-state='open'] { animation: panel-in var(--duration-base) var(--ease-out); }
@keyframes fade-in { from { opacity: 0 } }
@keyframes panel-in { from { opacity: 0; transform: translate(-50%, calc(-50% + 8px)) scale(.98) } }
@media (prefers-reduced-motion: reduce) {
  .ui-overlay[data-state='open'], .ui-panel[data-state='open'] { animation: none }
}
</style>
