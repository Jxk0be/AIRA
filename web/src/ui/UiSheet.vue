<script setup lang="ts">
/**
 * A drawer over the content, for the phone.
 *
 * This is what the assistant's conversation history becomes. Today that list
 * expands *inside* the layout and shoves the conversation off the screen —
 * tapping History loses your place in the thread. A sheet covers the page
 * instead of moving it, and gives the same focus handling as the dialog.
 */
import { DialogClose, DialogContent, DialogOverlay, DialogPortal, DialogRoot, DialogTitle } from 'reka-ui'

withDefaults(defineProps<{ title: string; side?: 'left' | 'right' | 'bottom' }>(), { side: 'right' })
const open = defineModel<boolean>('open', { required: true })

const SIDE = {
  left: 'inset-y-0 left-0 w-[min(22rem,88vw)] border-r',
  right: 'inset-y-0 right-0 w-[min(22rem,88vw)] border-l',
  bottom: 'inset-x-0 bottom-0 max-h-[85vh] rounded-t-lg border-t',
} as const
</script>

<template>
  <DialogRoot v-model:open="open">
    <DialogPortal>
      <DialogOverlay class="ui-overlay fixed inset-0 z-40 bg-ink/40" />
      <DialogContent
        class="ui-sheet fixed z-50 flex flex-col border-border bg-surface shadow-float"
        :class="SIDE[side]"
        :style="{ paddingBottom: side === 'bottom' ? 'env(safe-area-inset-bottom, 0px)' : undefined }"
        :data-side="side"
      >
        <div class="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <DialogTitle class="text-lg font-semibold text-ink">{{ title }}</DialogTitle>
          <DialogClose class="-mr-2 grid h-11 w-11 place-items-center rounded-sm text-ink-muted hover:text-ink">
            <span class="sr-only">Close</span>
            <svg viewBox="0 0 20 20" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 5l10 10M15 5L5 15" /></svg>
          </DialogClose>
        </div>
        <div class="min-h-0 flex-1 overflow-y-auto"><slot /></div>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>

<style scoped>
.ui-overlay[data-state='open'] { animation: fade-in var(--duration-fast) var(--ease-out); }
.ui-sheet[data-state='open'] { animation: sheet-in var(--duration-base) var(--ease-out); }
.ui-sheet[data-side='left'][data-state='open'] { animation-name: sheet-in-left }
.ui-sheet[data-side='bottom'][data-state='open'] { animation-name: sheet-in-bottom }
@keyframes fade-in { from { opacity: 0 } }
@keyframes sheet-in { from { transform: translateX(100%) } }
@keyframes sheet-in-left { from { transform: translateX(-100%) } }
@keyframes sheet-in-bottom { from { transform: translateY(100%) } }
@media (prefers-reduced-motion: reduce) { .ui-overlay[data-state='open'], .ui-sheet[data-state='open'] { animation: none } }
</style>
