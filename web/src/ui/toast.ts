/**
 * One place that knows something happened.
 *
 * Before this, six views each kept a private `notice` ref that rendered a
 * paragraph above the content and shoved the whole page down — and pinning a
 * chart, rating a finding, changing an order quantity or removing a line
 * confirmed nothing at all. Every write goes through here now.
 *
 * The queue lives outside any component on purpose: a toast raised by a screen
 * that then navigates away still has to be readable on the next screen.
 *
 * Errors do not auto-dismiss. A message that disappears before it is read is
 * the same as no message, and an error is the one case where the owner may
 * need to copy the text or act on it.
 */

import { readonly, ref } from 'vue'

export type ToastTone = 'success' | 'info' | 'warning' | 'danger'

export interface Toast {
  id: number
  tone: ToastTone
  /** One line, in the owner's language. "Draft ready", not "201 Created". */
  title: string
  /** Optional second line for the detail that does not fit the first. */
  detail?: string
  /** An undo, a retry, or a way to look at what just happened. */
  action?: { label: string; run: () => void }
  duration: number
}

const DEFAULT_MS = 5000
const items = ref<Toast[]>([])
const timers = new Map<number, ReturnType<typeof setTimeout>>()
let nextId = 1

export const toasts = readonly(items)

export function dismissToast(id: number): void {
  const timer = timers.get(id)
  if (timer) {
    clearTimeout(timer)
    timers.delete(id)
  }
  items.value = items.value.filter((one) => one.id !== id)
}

function push(toast: Omit<Toast, 'id'>): number {
  const id = nextId++
  items.value = [...items.value, { ...toast, id }]
  if (toast.duration > 0) {
    timers.set(
      id,
      setTimeout(() => dismissToast(id), toast.duration),
    )
  }
  return id
}

type Options = Pick<Toast, 'detail' | 'action'> & { duration?: number }

export const toast = {
  success: (title: string, options: Options = {}) =>
    push({ tone: 'success', title, duration: DEFAULT_MS, ...options }),
  info: (title: string, options: Options = {}) =>
    push({ tone: 'info', title, duration: DEFAULT_MS, ...options }),
  warning: (title: string, options: Options = {}) =>
    push({ tone: 'warning', title, duration: 8000, ...options }),
  /** Stays until dismissed. See the note at the top of the file. */
  danger: (title: string, options: Options = {}) =>
    push({ tone: 'danger', title, duration: 0, ...options }),
}

/**
 * The shape almost every call site wants: run the write, say so, and turn a
 * failure into a readable message with a way to try again.
 *
 * `ApiError` already carries a sentence written for the shop owner — FastAPI's
 * `detail` — so it is shown as-is. Anything else is a network or programming
 * fault whose message is not fit to read, and gets a plain one instead.
 */
export async function withToast<T>(
  run: () => Promise<T>,
  messages: { success: string | ((result: T) => string); failure?: string },
): Promise<T | undefined> {
  try {
    const result = await run()
    const line =
      typeof messages.success === 'function' ? messages.success(result) : messages.success
    toast.success(line)
    return result
  } catch (cause) {
    const named = cause instanceof Error && cause.name === 'ApiError'
    toast.danger(messages.failure ?? 'That did not work', {
      detail: named ? (cause as Error).message : 'Check the connection and try again.',
      action: { label: 'Try again', run: () => void withToast(run, messages) },
    })
    return undefined
  }
}
