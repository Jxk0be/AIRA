<script setup lang="ts">
/**
 * A short status, in a word as well as a color.
 *
 * The old severity badge encoded urgent/warn/info purely as border and text
 * color while its text said the *kind* ("Reordering"), so the ranking vanished
 * for anyone who could not see the difference. The label here is the meaning.
 */
withDefaults(
  defineProps<{ tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'primary' }>(),
  { tone: 'neutral' },
)

const TONE = {
  neutral: 'bg-raised text-ink-muted border-border',
  success: 'bg-success-subtle text-ink border-success',
  warning: 'bg-warning-subtle text-ink border-warning',
  danger: 'bg-danger-subtle text-ink border-danger',
  info: 'bg-info-subtle text-ink border-info',
  primary: 'bg-primary-subtle text-primary border-primary',
} as const
</script>

<template>
  <span
    class="inline-flex items-center gap-1.5 rounded-sm border px-2 py-0.5 text-xs font-semibold tracking-wide"
    :class="TONE[tone]"
  >
    <slot />
  </span>
</template>
