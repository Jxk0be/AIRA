<script setup lang="ts">
/**
 * One button, four intentions.
 *
 * Before this the app had four different primary buttons. Two of them used
 * `bg-brand text-white`, which measured 9.6:1 in light and **2.05:1 in dark** —
 * a button you could not read after sunset. The foreground is a token now
 * (`--primary-fg`, `--danger-fg`), so the pairing is checked by the contrast
 * gate rather than chosen per call site.
 *
 * Every size is at least 44px tall, which is the tap target the complaints
 * asked for and which the old 28px text buttons were nowhere near.
 */
import { Primitive } from 'reka-ui'

withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
    size?: 'md' | 'sm'
    /** Renders whatever child you pass instead of a <button> — e.g. RouterLink. */
    asChild?: boolean
    as?: string
    loading?: boolean
    disabled?: boolean
  }>(),
  { variant: 'primary', size: 'md', as: 'button', asChild: false },
)

const VARIANT = {
  primary: 'bg-primary text-primary-fg border-primary hover:opacity-90',
  secondary: 'bg-surface text-ink border-border-strong hover:bg-raised',
  ghost: 'bg-transparent text-ink-muted border-transparent hover:bg-raised hover:text-ink',
  danger: 'bg-danger text-danger-fg border-danger hover:opacity-90',
} as const

const SIZE = {
  md: 'min-h-11 px-4 text-base',
  sm: 'min-h-11 px-3 text-sm',
} as const
</script>

<template>
  <Primitive
    :as="as"
    :as-child="asChild"
    :type="as === 'button' && !asChild ? 'button' : undefined"
    :disabled="disabled || loading || undefined"
    :aria-busy="loading ? 'true' : undefined"
    class="inline-flex items-center justify-center gap-2 rounded-md border font-semibold transition-[background-color,opacity,border-color] disabled:pointer-events-none disabled:opacity-45"
    :class="[VARIANT[variant], SIZE[size]]"
    :style="{ transitionDuration: 'var(--duration-fast)' }"
  >
    <svg
      v-if="loading"
      class="h-4 w-4 flex-none animate-spin"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="2" opacity=".25" />
      <path d="M14.5 8A6.5 6.5 0 0 0 8 1.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
    </svg>
    <slot />
  </Primitive>
</template>
