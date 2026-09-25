/**
 * The browser's connection to Supabase Auth, and nothing else.
 *
 * This client is used for exactly one job: signing in, signing out, and keeping
 * a fresh access token. It never reads or writes a table. Shop data comes from
 * our own API, which is the only thing that knows what a tenant is
 * (CLAUDE.md rule 6) — and the tables are closed to this key anyway, which the
 * `close_the_data_api` migration made true and `test_data_api_is_closed` keeps
 * true.
 *
 * The publishable key belongs in the bundle. That is what it is for: it
 * identifies the project, it is not a credential, and every Supabase app ships
 * one. What must never be here is the secret key.
 *
 * In development the local stack's values are the default, so a fresh clone
 * signs in with nothing to configure. In a production build there is no default
 * and nothing configured is a supported state rather than a crash: the sign-in
 * screen says which two variables are missing, instead of a white page and a
 * console error.
 */

import { createClient, type Session, type SupabaseClient } from '@supabase/supabase-js'

/**
 * The Supabase CLI's own local-stack values, used when nothing is configured.
 *
 * Neither is a secret: the URL is loopback, and a publishable key identifies a
 * project rather than authorising anything — our tables are closed to every Data
 * API role, so this key can do exactly one thing, which is sign in. Committing
 * them means `python tasks.py web` and the Playwright suite work on a fresh clone
 * with nothing to fill in, which is why they are here rather than in a `.env.*`
 * file that `.gitignore` deliberately refuses to track.
 *
 * Guarded by `import.meta.env.DEV`, which Vite replaces at build time, so a
 * production build drops them entirely and fails loudly when it is misconfigured
 * instead of quietly pointing a customer's browser at their own localhost.
 */
const LOCAL_STACK = {
  url: 'http://127.0.0.1:54321',
  publishableKey: 'sb_publishable_ACJWlzQHlZjBrEguHvfOxg_3BJgxAaH',
} as const

const url = import.meta.env.VITE_SUPABASE_URL || (import.meta.env.DEV ? LOCAL_STACK.url : '')
const publishableKey =
  import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY ||
  (import.meta.env.DEV ? LOCAL_STACK.publishableKey : '')

/** Where the session is kept, and what the UI suite seeds. */
export const AUTH_STORAGE_KEY = 'aira-auth'

/** False only in a production build with nothing configured. */
export const authConfigured = Boolean(url && publishableKey)

export const supabase: SupabaseClient | null = authConfigured
  ? createClient(url, publishableKey, {
      auth: {
        /**
         * Named rather than derived from the project URL.
         *
         * The default key is built from the project's subdomain, which for a
         * loopback URL is not a stable thing to depend on — and the Playwright
         * suite seeds a session into this key so it can render signed-in screens
         * without an auth server. A name we chose makes that a contract instead
         * of a guess. See `web/tests/support/app.ts`.
         */
        storageKey: AUTH_STORAGE_KEY,
        // Survive a reload and a new tab.
        persistSession: true,
        // The library refreshes in the background before the hour is up, which
        // is what keeps a long afternoon on the Home screen from ending in a
        // 401.
        autoRefreshToken: true,
        // Password-reset and email-confirmation links come back with the
        // session in the URL fragment.
        detectSessionInUrl: true,
      },
    })
  : null

/**
 * A usable access token, or null.
 *
 * `getSession` refreshes an expired token before returning it, so this is the
 * right thing to call per request rather than caching the token anywhere. The
 * cost is a read of local storage, not a network round trip, unless a refresh is
 * actually due.
 */
export async function accessToken(): Promise<string | null> {
  if (!supabase) return null
  const { data } = await supabase.auth.getSession()
  return data.session?.access_token ?? null
}

export type { Session }
