<script setup lang="ts">
/**
 * The shell, or nothing.
 *
 * Two screens render without it: the shop picker at `/`, which only appears
 * when there is no shop to send you to, and the dev styleguide. Everything else
 * lives inside `AppShell`.
 */
import { computed, watchEffect } from 'vue'
import { RouterView, useRoute } from 'vue-router'

import { applyBrand } from './lib/brand'
import { useTheme } from './lib/theme'
import { useTenantStore } from './stores/tenant'
import AppShell from './ui/AppShell.vue'
import Toaster from './ui/Toaster.vue'

const route = useRoute()
const shop = useTenantStore()
const { resolved } = useTheme()

const bare = computed(() => route.name === 'root' || route.name === 'styleguide')

/**
 * The shop's color, on the page.
 *
 * Here rather than in the store, because a store that writes to `<html>` is a
 * store you cannot test without a document. It depends on the theme as well as
 * the color: light and dark need different shades of the same hue, so this has
 * to run again every time the theme changes, not only when the shop does.
 *
 * `flush: 'sync'` because the router resolves the profile before it resolves
 * the navigation — so by writing the variables in the same tick, the first
 * paint of a screen is already the shop's color rather than ours for a frame.
 */
watchEffect(() => applyBrand(shop.brandColor, resolved.value), { flush: 'sync' })
</script>

<template>
  <Toaster />

  <RouterView v-if="bare" />
  <AppShell v-else />
</template>
