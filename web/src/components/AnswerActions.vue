<script setup lang="ts">
/**
 * The buttons an answer ends with.
 *
 * An answer that says "those four should go on an order" and leaves the owner
 * to find the reorder screen has done about half the job. So the assistant may
 * end a turn by offering one or two things to press: open the screen that does
 * the thing, open an email it has written, run the checks again.
 *
 * Three things this has to get right.
 *
 *   **Nothing here is the model's idea of a URL.** An `open` action carries a
 *   route the API picked out of its own catalogue, and a `run` action carries a
 *   *name* — `run_checks` — that this component maps onto a call it already
 *   makes. There is no path in an action that this client will fetch because it
 *   was told to, which is the whole reason the catalogue lives on the server.
 *
 *   **A button that does something says so before it does it.** Navigating is
 *   free and happens on the click. Running the checks writes findings against
 *   the shop's data, so it asks first, the same way the Home screen's own
 *   "Check again" would if it were not already labelled as the thing it does.
 *
 *   **An email is a draft, never a send.** The dialog shows the whole message,
 *   the owner copies it or opens it in their own mail app, and we are not in
 *   the loop. `mailto:` is not a fallback here — it is the feature.
 */
import { ref, useId } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '../api/client'
import type { AssistantAction } from '../api/types'
import UiButton from '../ui/UiButton.vue'
import UiDialog from '../ui/UiDialog.vue'
import { toast } from '../ui/toast'
import EmailDraftDialog from './EmailDraftDialog.vue'

const props = defineProps<{ tenant: string; actions: AssistantAction[] }>()

const router = useRouter()

// The reason under a button has to be wired to it, not merely next to it:
// tabbing onto "Order the ones that are low" should say why. Two turns can
// offer the same action, so the id is per-instance rather than per-key.
const uid = useId()
const detailId = (action: AssistantAction) => `${uid}-${action.key}`

const drafting = ref<AssistantAction | null>(null)
const confirming = ref<AssistantAction | null>(null)
const running = ref<string | null>(null)

/** `route` is a path under the shop, so `''` is the shop's own home. */
function go(action: AssistantAction) {
  const [path = '', search] = (action.route ?? '').split('?')
  void router.push({
    path: path ? `/${props.tenant}/${path}` : `/${props.tenant}`,
    query: Object.fromEntries(new URLSearchParams(search ?? '')),
  })
}

/**
 * The only tasks an action may name, and what each one actually calls.
 *
 * A lookup rather than anything computed from the action: whatever arrives in
 * `task`, the worst it can do is miss.
 */
const TASKS: Record<string, (tenant: string) => Promise<string>> = {
  run_checks: async (tenant) => {
    const runs = await api.runDetectors(tenant)
    const found = runs.reduce((total, run) => total + run.created, 0)
    const failed = runs.filter((run) => run.error).length
    if (failed) return `${failed} of ${runs.length} checks could not run`
    return found
      ? `${found} new thing${found === 1 ? '' : 's'} to look at`
      : 'Nothing new since the last check'
  },
}

async function run(action: AssistantAction) {
  const task = action.task ? TASKS[action.task] : undefined
  confirming.value = null
  if (!task) {
    toast.danger('This app cannot do that one')
    return
  }
  running.value = action.key
  try {
    toast.success(await task(props.tenant), {
      action: { label: 'See what needs doing', run: () => void router.push(`/${props.tenant}`) },
    })
  } catch (cause) {
    toast.danger('That did not run', {
      detail: cause instanceof Error ? cause.message : undefined,
    })
  } finally {
    running.value = null
  }
}

function press(action: AssistantAction) {
  if (action.kind === 'open') go(action)
  else if (action.kind === 'email') drafting.value = action
  else confirming.value = action
}
</script>

<template>
  <div v-if="actions.length" class="mt-3 flex flex-wrap gap-2">
    <div v-for="action in actions" :key="action.key" class="min-w-0">
      <UiButton
        size="sm"
        variant="secondary"
        :loading="running === action.key"
        :aria-describedby="action.detail ? detailId(action) : undefined"
        @click="press(action)"
      >
        {{ action.label }}
      </UiButton>
      <!-- Why it is being offered, under the button rather than inside it: a
           label long enough to explain itself is a label that wraps. -->
      <p v-if="action.detail" :id="detailId(action)" class="mt-1 max-w-xs text-sm text-ink-muted">
        {{ action.detail }}
      </p>
    </div>
  </div>

  <EmailDraftDialog :action="drafting" @close="drafting = null" />

  <UiDialog
    :open="confirming !== null"
    title="Run the checks now?"
    description="This looks at the latest synced data and writes anything new into the list of what needs doing."
    @update:open="(value: boolean) => !value && (confirming = null)"
  >
    <p class="text-base text-ink-muted">
      It takes a few seconds and changes nothing in your own system.
    </p>
    <template #footer="{ close }">
      <UiButton variant="ghost" @click="close()">Not now</UiButton>
      <UiButton @click="confirming && run(confirming)">Run them</UiButton>
    </template>
  </UiDialog>
</template>
