/**
 * Which one is working, rather than whether something is.
 *
 * The bug this exists to stop: a screen holds `const working = ref(false)`,
 * binds `:loading="working"` to a button, and then that button turns out to be
 * inside a `v-for`. Now pressing "Make a draft" for one supplier spins the
 * button for every supplier, and the owner cannot tell which order is actually
 * being built — or whether they clicked the right one at all. It is the same
 * mistake every time: a boolean cannot say *which*.
 *
 * So hold the key of the thing in flight. `busy(key)` is true for exactly one
 * of them, and `anyBusy` is there for the rest, which should go quiet rather
 * than pretend to be working — greyed out is honest, spinning is a lie.
 *
 * The key is whatever identifies the row: a vendor name, a variant id, or a
 * plain string like `'save'` when two buttons on one screen do different jobs
 * and merely shared a flag.
 *
 *     const drafting = useBusy()
 *     <UiButton :loading="drafting.busy(group.vendor_name)"
 *               :disabled="drafting.anyBusy.value"
 *               @click="drafting.run(group.vendor_name, () => draft(group))" />
 *
 * `run` always clears in a `finally`, because the one thing worse than every
 * button spinning is one button spinning forever.
 */

import { computed, ref, type ComputedRef, type Ref } from 'vue'

export interface Busy<Key> {
  /** True for the one that is working. */
  busy: (key: Key) => boolean
  /** True while any of them is, for disabling the others. */
  anyBusy: ComputedRef<boolean>
  /** The key in flight, or null. Rarely needed; `busy` reads better. */
  current: Readonly<Ref<Key | null>>
  /** Run the work with this key marked, clearing it however it ends. */
  run: <T>(key: Key, work: () => Promise<T>) => Promise<T>
}

export function useBusy<Key = string>(): Busy<Key> {
  const current = ref<Key | null>(null) as Ref<Key | null>

  return {
    current,
    busy: (key: Key) => current.value === key,
    anyBusy: computed(() => current.value !== null),
    run: async <T>(key: Key, work: () => Promise<T>): Promise<T> => {
      current.value = key
      try {
        return await work()
      } finally {
        current.value = null
      }
    },
  }
}
