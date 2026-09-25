/// <reference types="vite/client" />

/**
 * The settings a build needs, typed so a missing one is a compile error rather
 * than `undefined` at runtime. Optional because a contributor who has not
 * written `web/.env` yet should get the sign-in screen's "not configured"
 * message, not a white page.
 */
interface ImportMetaEnv {
  /** The Supabase project's API URL. */
  readonly VITE_SUPABASE_URL?: string
  /** The project's publishable key. Safe in the bundle; the secret key is not. */
  readonly VITE_SUPABASE_PUBLISHABLE_KEY?: string
  /** Where the API lives. Unset in dev: calls go through Vite's /api proxy. */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, never>, Record<string, never>, unknown>
  export default component
}
