<script setup lang="ts">
/**
 * Light / Dark / System, with Light as the default.
 *
 * Not "System by default", which is the usual advice: this app used to obey
 * `prefers-color-scheme` with no override, which is exactly why every
 * screenshot taken on a dark laptop came out dark and the owner had no say.
 *
 * It is a radio group rather than three buttons so that arrow keys move between
 * the options and a screen reader announces "Light, selected, 1 of 3".
 */
import { RadioGroupItem, RadioGroupRoot } from 'reka-ui'

import { type ThemeChoice, useTheme } from '../lib/theme'

const { choice, setTheme } = useTheme()

const OPTIONS: { value: ThemeChoice; label: string }[] = [
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
  { value: 'system', label: 'System' },
]
</script>

<template>
  <RadioGroupRoot
    :model-value="choice"
    class="inline-flex rounded-md bg-raised p-0.5"
    aria-label="Theme"
    @update:model-value="(value) => setTheme(value as ThemeChoice)"
  >
    <RadioGroupItem
      v-for="option in OPTIONS"
      :key="option.value"
      :value="option.value"
      class="min-h-11 flex-1 rounded-sm px-3 text-sm font-semibold text-ink-muted transition-colors data-[state=checked]:bg-surface data-[state=checked]:text-ink data-[state=checked]:shadow-raised"
      :style="{ transitionDuration: 'var(--duration-fast)' }"
    >
      {{ option.label }}
    </RadioGroupItem>
  </RadioGroupRoot>
</template>
