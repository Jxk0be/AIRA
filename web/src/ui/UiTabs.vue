<script setup lang="ts">
/**
 * The sub-navigation for Stock, Reports and Settings.
 *
 * Reka gives this arrow-key roving focus and the right roles, which a row of
 * <button>s does not. The active tab is marked by weight and a rule as well as
 * colour, and the strip scrolls horizontally with a visible edge rather than
 * silently hiding destinations the way the old nine-item nav did.
 */
import { TabsIndicator, TabsList, TabsRoot, TabsTrigger } from 'reka-ui'

// `label` rather than `ariaLabel`: an aria-shaped prop name makes eslint ask
// for hyphens and vue-tsc ask for camelCase, and they cannot both be obeyed.
defineProps<{ tabs: { value: string; label: string }[]; label: string }>()
const model = defineModel<string>({ required: true })
</script>

<template>
  <TabsRoot v-model="model" :unmount-on-hide="false">
    <TabsList
      :aria-label="label"
      class="relative flex gap-1 overflow-x-auto border-b border-border"
    >
      <TabsTrigger
        v-for="tab in tabs"
        :key="tab.value"
        :value="tab.value"
        class="min-h-11 shrink-0 rounded-t-sm px-3 text-base font-medium whitespace-nowrap text-ink-muted transition-colors hover:text-ink data-[state=active]:font-semibold data-[state=active]:text-ink"
        :style="{ transitionDuration: 'var(--duration-fast)' }"
      >
        {{ tab.label }}
      </TabsTrigger>
      <TabsIndicator
        class="absolute bottom-0 left-0 h-0.5 w-(--reka-tabs-indicator-size) translate-x-(--reka-tabs-indicator-position) rounded-full bg-primary transition-[translate,width]"
        :style="{ transitionDuration: 'var(--duration-base)' }"
      />
    </TabsList>
    <slot />
  </TabsRoot>
</template>
