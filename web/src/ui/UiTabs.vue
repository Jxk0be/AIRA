<script setup lang="ts">
/**
 * The sub-navigation for Stock, Reports and Settings.
 *
 * Reka gives this arrow-key roving focus and the right roles, which a row of
 * `<button>`s does not. The active tab is marked by weight and a rule as well
 * as color, so the ordering does not vanish for anyone who cannot see the
 * difference (audit A4).
 *
 * ## Why there are two layouts
 *
 * This used to be one horizontally scrolling strip at every width. On a 375px
 * phone that is 343px of room, and Settings' four labels need 477px — so the
 * screen opened on "ta & sync", half a word clipped at the left edge, with
 * "Appearance" off the right. That is audit U1 in miniature: a destination you
 * cannot see is a destination you do not have, and a scroll strip with no
 * affordance does not tell you to look.
 *
 * So below `sm` the strip becomes a segmented block that wraps: every tab gets
 * an equal share of the width, three fit on one row and four fold into a 2x2.
 * Nothing is clipped, nothing scrolls, and every cell is a full-width target
 * at least 44px tall. From `sm` up — where all of them fit on one line with
 * room to spare — it is the underline strip it always was.
 *
 * ## Why the rule is on the tab rather than under the strip
 *
 * This used Reka's `TabsIndicator`, a single absolutely-positioned bar that
 * slides between tabs. Two problems. It cannot follow a tab onto a second row,
 * which the phone layout now has — and it was never rendering in the first
 * place: the component is a `v-if` on a measurement that stayed null here, so
 * every tabbed screen on this branch shipped with no rule at all and the
 * active tab distinguished by weight and color alone.
 *
 * A 2px bottom border on the trigger itself needs no measuring, works the same
 * in a grid and in a row, and cannot silently not happen. The cost is the
 * slide, which nobody has been getting anyway.
 */
import { computed } from 'vue'
import { TabsList, TabsRoot, TabsTrigger } from 'reka-ui'

// `label` rather than `ariaLabel`: an aria-shaped prop name makes eslint ask
// for hyphens and vue-tsc ask for camelCase, and they cannot both be obeyed.
const props = defineProps<{ tabs: { value: string; label: string }[]; label: string }>()
const model = defineModel<string>({ required: true })

/**
 * How many across on a phone.
 *
 * Three still fit side by side at 375px — about 108px each, which holds "Not
 * selling" on one line — and wrap to two lines rather than clip at 320px. Four
 * do not fit, and a 2x2 is better than three and an orphan. Anything longer
 * keeps folding in pairs.
 */
const columns = computed(() => (props.tabs.length <= 3 ? props.tabs.length : 2))
</script>

<template>
  <TabsRoot v-model="model" :unmount-on-hide="false">
    <TabsList
      :aria-label="label"
      class="gap-1 border-border max-sm:grid max-sm:rounded-lg max-sm:border max-sm:p-1 sm:flex sm:border-b"
      :style="{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }"
    >
      <TabsTrigger
        v-for="tab in tabs"
        :key="tab.value"
        :value="tab.value"
        class="flex min-h-11 items-center justify-center border-b-2 border-transparent text-base font-medium text-ink-muted transition-[color,background-color,border-color] hover:text-ink data-[state=active]:border-primary data-[state=active]:font-semibold data-[state=active]:text-ink max-sm:rounded-md max-sm:px-2 max-sm:py-1.5 max-sm:text-center max-sm:leading-tight max-sm:data-[state=active]:bg-raised sm:-mb-px sm:shrink-0 sm:rounded-t-sm sm:px-3 sm:whitespace-nowrap"
        :style="{ transitionDuration: 'var(--duration-fast)' }"
      >
        {{ tab.label }}
      </TabsTrigger>
    </TabsList>
    <slot />
  </TabsRoot>
</template>
