/**
 * Who is signed in.
 *
 * The session itself is Supabase's to own — it persists it, refreshes it and
 * tells us when it changes. This store is the Vue-shaped view of that, plus the
 * one thing the app adds: `shops`, which comes from our API and is the answer to
 * "which shops may this person open". A signed-in user with no shops is a normal
 * state, not an error, and the screen for it says "ask your owner" rather than
 * looking broken.
 *
 * `ready` exists because the first paint has to wait for something. Supabase
 * reads the stored session asynchronously, so for a tick on every load we do not
 * yet know whether there is a session — and a router guard that reads `signedIn`
 * during that tick would bounce a signed-in owner to the sign-in screen on every
 * refresh.
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api, onUnauthorized } from '../api/client'
import type { Me, ShopMembership } from '../api/types'
import { authConfigured, supabase, type Session } from '../lib/supabase'

export const useAuthStore = defineStore('auth', () => {
  const session = ref<Session | null>(null)
  const me = ref<Me | null>(null)
  /** False until Supabase has told us whether a stored session exists. */
  const ready = ref(false)
  const busy = ref(false)
  const error = ref<string | null>(null)

  const signedIn = computed(() => session.value !== null)
  const email = computed(() => me.value?.email ?? session.value?.user?.email ?? null)
  const shops = computed<ShopMembership[]>(() => me.value?.shops ?? [])
  const hasShops = computed(() => shops.value.length > 0)

  /** The caller's role at one shop, or null if they are not a member. */
  function roleAt(tenant: string): ShopMembership['role'] | null {
    return shops.value.find((shop) => shop.tenant === tenant)?.role ?? null
  }

  /**
   * Fetch the current person and their shops.
   *
   * Deduplicated while in flight, because signing in reaches this twice: once
   * from `signIn`, which has to wait for the answer before the router can decide
   * where to send them, and once from `onAuthStateChange`, which fires for the
   * same sign-in a moment later. Two identical requests is not a bug that hurts,
   * but it is a bug that shows up in the network tab and makes somebody wonder.
   */
  let loading: Promise<void> | null = null

  function loadMe(): Promise<void> {
    loading ??= fetchMe().finally(() => {
      loading = null
    })
    return loading
  }

  async function fetchMe(): Promise<void> {
    if (!session.value) {
      me.value = null
      return
    }
    try {
      me.value = await api.me()
    } catch (cause) {
      // Losing /me is not losing the session: the API may simply be down, and
      // signing the owner out over that would be rude and wrong.
      me.value = null
      error.value = cause instanceof Error ? cause.message : String(cause)
    }
  }

  /**
   * Subscribe to the session, once, at app startup.
   *
   * `onAuthStateChange` covers sign-in, sign-out, token refresh and the other
   * tab, so nothing else in the app needs to poll or re-check.
   *
   * Memoised: the router guard calls this on the first navigation, and two
   * navigations racing must not end up with two subscriptions.
   */
  let starting: Promise<void> | null = null

  function start(): Promise<void> {
    starting ??= begin()
    return starting
  }

  async function begin(): Promise<void> {
    if (!supabase) {
      ready.value = true
      return
    }

    // A 401 from our API after the session looked fine means the token is no
    // longer good — revoked, or the project's keys rotated. Drop it rather than
    // leaving the app in a state where every screen shows an error.
    onUnauthorized(async () => {
      if (session.value) await signOut()
    })

    const { data } = await supabase.auth.getSession()
    session.value = data.session
    ready.value = true
    if (data.session) await loadMe()

    supabase.auth.onAuthStateChange((_event, next) => {
      const changedUser = next?.user?.id !== session.value?.user?.id
      session.value = next
      // A plain token refresh keeps the same user, and re-fetching /me on every
      // refresh would be a pointless hourly request.
      if (changedUser) void loadMe()
    })
  }

  async function signIn(address: string, password: string): Promise<boolean> {
    if (!supabase) {
      error.value = 'Sign-in is not configured in this build.'
      return false
    }
    busy.value = true
    error.value = null
    try {
      const { error: failed } = await supabase.auth.signInWithPassword({
        email: address.trim(),
        password,
      })
      if (failed) {
        error.value = failed.message
        return false
      }
      await loadMe()
      return true
    } finally {
      busy.value = false
    }
  }

  async function signUp(address: string, password: string): Promise<boolean> {
    if (!supabase) {
      error.value = 'Sign-in is not configured in this build.'
      return false
    }
    busy.value = true
    error.value = null
    try {
      const { error: failed } = await supabase.auth.signUp({
        email: address.trim(),
        password,
      })
      if (failed) {
        error.value = failed.message
        return false
      }
      await loadMe()
      return true
    } finally {
      busy.value = false
    }
  }

  async function sendPasswordReset(address: string): Promise<boolean> {
    if (!supabase) return false
    busy.value = true
    error.value = null
    try {
      const { error: failed } = await supabase.auth.resetPasswordForEmail(address.trim(), {
        redirectTo: `${window.location.origin}/sign-in`,
      })
      if (failed) {
        error.value = failed.message
        return false
      }
      return true
    } finally {
      busy.value = false
    }
  }

  async function signOut(): Promise<void> {
    if (!supabase) return
    await supabase.auth.signOut()
    session.value = null
    me.value = null
  }

  return {
    session,
    me,
    ready,
    busy,
    error,
    signedIn,
    email,
    shops,
    hasShops,
    roleAt,
    start,
    loadMe,
    signIn,
    signUp,
    sendPasswordReset,
    signOut,
    configured: authConfigured,
  }
})
