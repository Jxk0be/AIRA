<script setup lang="ts">
/**
 * Where toasts appear. Mounted once, in App.vue.
 *
 * Reka's Toast primitives handle the parts that are easy to get wrong by hand:
 * the region is announced politely, a toast is swipe-dismissable, hovering or
 * focusing pauses the timer, and F6 jumps to the region from anywhere.
 *
 * Tone is never the only carrier: every toast has an icon and a word, because
 * a red bar and a green bar are the same bar to a lot of people, and to anyone
 * listening rather than looking.
 *
 * On a phone it sits at the bottom, above the tab bar and clear of the home
 * indicator; on a desktop it sits bottom-right.
 */
import {
  ToastAction,
  ToastDescription,
  ToastProvider,
  ToastRoot,
  ToastTitle,
  ToastViewport,
} from 'reka-ui'

import { dismissToast, type ToastTone, toasts } from './toast'

const TONE: Record<ToastTone, { ring: string; icon: string; word: string }> = {
  success: { ring: 'border-l-success', icon: 'M4 10.5 8 14.5 16 5.5', word: 'Done' },
  info: { ring: 'border-l-info', icon: 'M10 9v5M10 6.2h.01', word: 'Note' },
  warning: { ring: 'border-l-warning', icon: 'M10 7v4.4M10 14.6h.01', word: 'Careful' },
  danger: { ring: 'border-l-danger', icon: 'M6.5 6.5l7 7M13.5 6.5l-7 7', word: 'Problem' },
}

const TONE_TEXT: Record<ToastTone, string> = {
  success: 'text-success',
  info: 'text-info',
  warning: 'text-warning',
  danger: 'text-danger',
}
</script>

<template>
  <ToastProvider swipe-direction="right">
    <ToastRoot
      v-for="item in toasts"
      :key="item.id"
      :duration="item.duration > 0 ? item.duration : 1000000"
      class="toast pointer-events-auto flex w-full items-start gap-3 rounded-md border border-border border-l-4 bg-surface p-3 shadow-float"
      :class="TONE[item.tone].ring"
      @update:open="(open: boolean) => !open && dismissToast(item.id)"
    >
      <svg
        class="mt-0.5 h-5 w-5 flex-none"
        :class="TONE_TEXT[item.tone]"
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <circle v-if="item.tone !== 'success'" cx="10" cy="10" r="8" />
        <path :d="TONE[item.tone].icon" />
      </svg>

      <div class="min-w-0 flex-1">
        <ToastTitle class="text-base leading-snug font-semibold text-ink">
          <!-- The word, for anyone who cannot use the color. -->
          <span class="sr-only">{{ TONE[item.tone].word }}: </span>{{ item.title }}
        </ToastTitle>
        <ToastDescription v-if="item.detail" class="mt-0.5 text-sm text-ink-muted">
          {{ item.detail }}
        </ToastDescription>
        <ToastAction
          v-if="item.action"
          :alt-text="item.action.label"
          class="mt-2 min-h-11 rounded-sm px-1 text-sm font-semibold text-primary underline underline-offset-4"
          @click="item.action.run()"
        >
          {{ item.action.label }}
        </ToastAction>
      </div>

      <button
        type="button"
        class="-m-1 grid h-11 w-11 flex-none place-items-center rounded-sm text-ink-muted hover:text-ink"
        @click="dismissToast(item.id)"
      >
        <span class="sr-only">Dismiss</span>
        <svg viewBox="0 0 20 20" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <path d="M5 5l10 10M15 5L5 15" />
        </svg>
      </button>
    </ToastRoot>

    <ToastViewport
      class="pointer-events-none fixed z-50 flex w-[min(26rem,calc(100vw-2rem))] flex-col gap-2 outline-none"
      :style="{
        right: '1rem',
        bottom: 'calc(env(safe-area-inset-bottom, 0px) + var(--toast-offset, 1rem))',
      }"
    />
  </ToastProvider>
</template>

<style scoped>
/* 200ms, and nothing at all for anyone who asked for less movement. */
.toast[data-state='open'] {
  animation: toast-in var(--duration-base) var(--ease-out);
}
.toast[data-state='closed'] {
  animation: toast-out var(--duration-fast) ease-in;
}
.toast[data-swipe='move'] {
  transform: translateX(var(--reka-toast-swipe-move-x));
}
.toast[data-swipe='end'] {
  animation: toast-out var(--duration-fast) ease-in;
}

@keyframes toast-in {
  from {
    opacity: 0;
    transform: translateY(8px) scale(0.98);
  }
}
@keyframes toast-out {
  to {
    opacity: 0;
    transform: translateY(4px) scale(0.98);
  }
}

@media (prefers-reduced-motion: reduce) {
  .toast[data-state='open'],
  .toast[data-state='closed'],
  .toast[data-swipe='end'] {
    animation: none;
  }
}
</style>
