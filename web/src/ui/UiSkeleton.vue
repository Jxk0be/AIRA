<script setup lang="ts">
/**
 * A shape where content is about to be, rather than a spinner.
 *
 * A spinner says "wait"; a skeleton says "a table with five rows is coming",
 * which is the difference between a page that feels slow and one that feels
 * like it is already arriving.
 *
 * It is `aria-hidden` and the container carries `aria-busy` instead: pulsing
 * rectangles are meaningless read aloud, and announcing them each time they
 * repaint is worse than saying nothing.
 */
withDefaults(defineProps<{ lines?: number; class?: string }>(), { lines: 1 })
</script>

<template>
  <div class="space-y-2" aria-hidden="true">
    <div
      v-for="line in lines"
      :key="line"
      class="working h-3 rounded-sm bg-raised"
      :class="[$props.class, line === lines && lines > 1 ? 'w-2/3' : 'w-full']"
    />
  </div>
</template>
