<script setup lang="ts">
/**
 * The only screen you can reach without an account.
 *
 * Three states share one form, because they are the same two fields: signing in,
 * signing up, and asking for a reset link. Splitting them into three routes
 * would mean three near-identical files and a person clicking twice to get
 * between them.
 *
 * Deliberately plain about failure. Supabase's own message is shown as written
 * ("Invalid login credentials"), because it is accurate and because rewording it
 * into something friendlier is how you end up telling somebody their password is
 * wrong when the real problem is an unconfirmed address.
 *
 * Note what this screen does *not* tell you: whether an email has an account
 * here. Both sign-up and reset say the same "check your email" either way.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'
import UiButton from '../ui/UiButton.vue'

type Mode = 'sign-in' | 'sign-up' | 'reset'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const mode = ref<Mode>('sign-in')
const email = ref('')
const password = ref('')
const sent = ref(false)

const HEADINGS: Record<Mode, string> = {
  'sign-in': 'Sign in',
  'sign-up': 'Create an account',
  reset: 'Reset your password',
}

const ACTIONS: Record<Mode, string> = {
  'sign-in': 'Sign in',
  'sign-up': 'Create account',
  reset: 'Email me a link',
}

const heading = computed(() => HEADINGS[mode.value])
const action = computed(() => ACTIONS[mode.value])

/** Where to land afterwards, if a guard bounced them here from somewhere. */
const wanted = computed(() => {
  const next = route.query.next
  return typeof next === 'string' && next.startsWith('/') ? next : '/'
})

function setMode(next: Mode): void {
  mode.value = next
  sent.value = false
  auth.error = null
}

watch([email, password], () => {
  // Clearing the error as they type is the difference between a form that
  // corrects you once and a form that nags.
  if (auth.error) auth.error = null
})

async function submit(): Promise<void> {
  if (mode.value === 'reset') {
    sent.value = await auth.sendPasswordReset(email.value)
    return
  }

  const ok =
    mode.value === 'sign-in'
      ? await auth.signIn(email.value, password.value)
      : await auth.signUp(email.value, password.value)
  if (!ok) return

  // Sign-up on a project that confirms addresses returns no session: they have
  // to click the link in the email before there is anything to redirect to.
  if (!auth.signedIn) {
    sent.value = true
    return
  }
  await router.replace(wanted.value)
}

onMounted(() => {
  // A reset link lands back here with a session already in the URL fragment,
  // which Supabase picks up. If that has happened, there is nothing to ask for.
  if (auth.signedIn) void router.replace(wanted.value)
})
</script>

<template>
  <main class="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6 py-16">
    <h1 class="display text-3xl font-semibold tracking-tight text-ink">
      AIRA<span class="text-primary">.</span>
    </h1>
    <p class="mt-1 text-sm text-ink-muted">The shop's analyst, whatever the shop runs on.</p>

    <div v-if="!auth.configured" class="mt-8 border border-border bg-surface p-5">
      <p class="text-sm text-ink">Sign-in is not configured in this build.</p>
      <p class="mt-1 text-sm text-ink-muted">
        Copy <code>web/.env.example</code> to <code>web/.env</code> and fill in the two
        values. <code>npx supabase status</code> prints both.
      </p>
      <pre
        class="tabular mt-3 overflow-x-auto border-l-2 border-border bg-raised px-3 py-2 text-sm text-ink-muted"
>VITE_SUPABASE_URL=
VITE_SUPABASE_PUBLISHABLE_KEY=</pre
      >
    </div>

    <template v-else>
      <h2 class="mt-10 text-lg font-semibold text-ink">{{ heading }}</h2>

      <div v-if="sent" class="mt-4 border border-border bg-surface p-5">
        <p class="text-sm text-ink">Check your email.</p>
        <p class="mt-1 text-sm text-ink-muted">
          If there is an account for {{ email }}, a link is on its way.
        </p>
        <UiButton class="mt-4" variant="secondary" size="sm" @click="setMode('sign-in')">
          Back to sign in
        </UiButton>
      </div>

      <form v-else class="mt-4 flex flex-col gap-4" @submit.prevent="submit">
        <label class="flex flex-col gap-1.5">
          <span class="text-sm font-medium text-ink">Email</span>
          <input
            v-model="email"
            type="email"
            name="email"
            autocomplete="email"
            required
            class="min-h-11 rounded-md border border-border-strong bg-surface px-3 text-base text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
          />
        </label>

        <label v-if="mode !== 'reset'" class="flex flex-col gap-1.5">
          <span class="text-sm font-medium text-ink">Password</span>
          <input
            v-model="password"
            type="password"
            name="password"
            :autocomplete="mode === 'sign-up' ? 'new-password' : 'current-password'"
            required
            :minlength="mode === 'sign-up' ? 8 : undefined"
            class="min-h-11 rounded-md border border-border-strong bg-surface px-3 text-base text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
          />
          <span v-if="mode === 'sign-up'" class="text-sm text-ink-muted">
            Eight characters or more.
          </span>
        </label>

        <p
          v-if="auth.error"
          role="alert"
          class="border-l-2 border-danger bg-danger-subtle px-3 py-2 text-sm text-ink"
        >
          {{ auth.error }}
        </p>

        <UiButton type="submit" :loading="auth.busy">{{ action }}</UiButton>
      </form>

      <div class="mt-6 flex flex-wrap gap-x-5 gap-y-2 text-sm">
        <button
          v-if="mode !== 'sign-in'"
          type="button"
          class="text-primary underline underline-offset-2"
          @click="setMode('sign-in')"
        >
          Sign in instead
        </button>
        <button
          v-if="mode !== 'sign-up'"
          type="button"
          class="text-primary underline underline-offset-2"
          @click="setMode('sign-up')"
        >
          Create an account
        </button>
        <button
          v-if="mode === 'sign-in'"
          type="button"
          class="text-ink-muted underline underline-offset-2"
          @click="setMode('reset')"
        >
          Forgotten your password?
        </button>
      </div>
    </template>
  </main>
</template>
