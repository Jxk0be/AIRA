<script setup lang="ts">
/**
 * The shell, or nothing.
 *
 * Two screens render without it: the shop picker at `/`, which only appears
 * when there is no shop to send you to, and the dev styleguide. Everything else
 * lives inside `AppShell`.
 */
import { computed } from 'vue'
import { RouterView, useRoute } from 'vue-router'

import AppShell from './ui/AppShell.vue'
import Toaster from './ui/Toaster.vue'

const route = useRoute()

const bare = computed(() => route.name === 'root' || route.name === 'styleguide')
</script>

<template>
  <Toaster />

  <RouterView v-if="bare" />
  <AppShell v-else />
</template>
