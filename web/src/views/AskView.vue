<script setup lang="ts">
/**
 * The shop's analyst, in conversation.
 *
 * Five things this screen has to get right, and four of them were wrong before.
 *
 *   **The composer stays put.** The conversation scrolls; the box you type in
 *   does not. The shell gives this route the whole frame (`meta.fills`) and the
 *   height is `100dvh`, which shrinks when the mobile keyboard opens — so the
 *   composer rides up with it instead of hiding behind it.
 *
 *   **History is a drawer, not a layout change.** It used to expand *inside*
 *   the page and shove the conversation down and off-screen, so tapping it lost
 *   your place in the thread (audit U14). Now it covers the page and gives it
 *   back untouched.
 *
 *   **Streaming is announced, politely and once.** Tokens append to a live
 *   region silently; a separate status line says "Working", names each tool as
 *   it runs, and says "Answer ready" at the end. Announcing every token would
 *   make a screen reader unusable, and announcing nothing — which is what
 *   happened before — leaves a blind user with no idea an answer is arriving
 *   (audit A8).
 *
 *   **Renaming and deleting are real dialogs.** They were `window.prompt` and
 *   `window.confirm`: unstyled, unthemed, and on a phone they read as the
 *   browser breaking rather than the app asking (audit U17).
 *
 *   **A chart has to be pinnable exactly as it was drawn.** Pinning stores the
 *   spec that was validated during the answer, not a fresh query, so a pinned
 *   chart is a record of what was said. That part was already right.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../api/client'
import { ask, type DonePayload } from '../api/stream'
import type { AssistantInfo, ChartSpec, ConversationSummary, ToolCallRecord } from '../api/types'
import ChartRenderer from '../components/ChartRenderer.vue'
import ToolChip from '../components/ToolChip.vue'
import { duration, money, sinceNow, toolLabel } from '../lib/format'
import { renderMarkdown } from '../lib/markdown'
import { useTenantStore } from '../stores/tenant'
import UiButton from '../ui/UiButton.vue'
import UiDialog from '../ui/UiDialog.vue'
import UiSheet from '../ui/UiSheet.vue'
import UiSkeleton from '../ui/UiSkeleton.vue'
import { toast, withToast } from '../ui/toast'

interface Turn {
  id: string
  role: 'user' | 'assistant'
  text: string
  tools: ToolCallRecord[]
  charts: ChartSpec[]
  done?: DonePayload | null
  failed?: string | null
  streaming?: boolean
}

const route = useRoute()
const shop = useTenantStore()

const slug = computed(() => String(route.params.tenant ?? ''))
const info = ref<AssistantInfo | null>(null)
const conversations = ref<ConversationSummary[]>([])
const conversationId = ref<string | null>(null)
const turns = ref<Turn[]>([])
const draft = ref('')
const sending = ref(false)
const loadingShell = ref(true)
const error = ref<string | null>(null)
const pinning = ref<string | null>(null)
const pinned = ref<Set<string>>(new Set())
const historyOpen = ref(false)
const thread = ref<HTMLElement | null>(null)
const composer = ref<HTMLTextAreaElement | null>(null)

/** Short, polite, and never the tokens themselves (audit A8). */
const status = ref('')

const renaming = ref<ConversationSummary | null>(null)
const renameTo = ref('')
const deleting = ref<ConversationSummary | null>(null)

let controller: AbortController | null = null

/** Unique within the conversation: two turns can draw the same chart. */
const chartKey = (turn: Turn, index: number) => `${turn.id}#${index}`

async function scrollDown() {
  await nextTick()
  const box = thread.value
  if (box) box.scrollTop = box.scrollHeight
}

async function loadShell() {
  loadingShell.value = true
  error.value = null
  try {
    const [assistant, list] = await Promise.all([
      api.assistant(slug.value),
      api.conversations(slug.value),
    ])
    info.value = assistant
    conversations.value = list
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loadingShell.value = false
  }
}

async function openConversation(id: string) {
  conversationId.value = id
  historyOpen.value = false
  pinned.value = new Set()
  try {
    const messages = await api.messages(slug.value, id)
    turns.value = messages
      .filter((message) => message.role === 'user' || message.role === 'assistant')
      .map((message) => ({
        id: message.id,
        role: message.role as 'user' | 'assistant',
        text: message.content,
        tools: message.tool_calls ?? [],
        charts: message.charts ?? [],
      }))
    await scrollDown()
  } catch {
    toast.danger('Could not open that conversation')
  }
}

function startNew() {
  controller?.abort()
  conversationId.value = null
  turns.value = []
  pinned.value = new Set()
  historyOpen.value = false
  draft.value = ''
  status.value = ''
  void nextTick(() => composer.value?.focus())
}

async function send(question?: string) {
  const text = (question ?? draft.value).trim()
  if (!text || sending.value) return

  draft.value = ''
  sending.value = true
  error.value = null
  status.value = 'Working on it'

  turns.value.push({ id: `you-${Date.now()}`, role: 'user', text, tools: [], charts: [] })
  turns.value.push({
    id: `aira-${Date.now()}`,
    role: 'assistant',
    text: '',
    tools: [],
    charts: [],
    streaming: true,
  })
  // The reactive proxy, not the object that was pushed: mutating the raw one
  // would update the data and never repaint, which is the whole screen.
  const answer = turns.value[turns.value.length - 1] as Turn
  await scrollDown()

  controller = new AbortController()
  try {
    for await (const event of ask({
      tenant: slug.value,
      message: text,
      conversationId: conversationId.value,
      signal: controller.signal,
    })) {
      switch (event.type) {
        case 'token':
          answer.text += event.text
          break
        case 'tool_start':
          answer.tools.push({ tool: event.tool, args: event.args })
          // Naming the tool is the honest version of a spinner.
          status.value = toolLabel(event.tool)
          break
        case 'tool_end': {
          const open = [...answer.tools].reverse().find((call) => call.tool === event.tool)
          if (open) {
            open.ms = event.ms
            open.error = event.error
            open.result = event.result
          }
          break
        }
        case 'chart':
          answer.charts.push(event.spec)
          break
        case 'done':
          answer.done = event.payload
          if (event.payload.conversation_id) conversationId.value = event.payload.conversation_id
          break
        case 'error':
          answer.failed = event.message
          break
      }
      await scrollDown()
    }
    status.value = answer.failed ? 'That did not work' : 'Answer ready'
  } catch (cause) {
    answer.failed = cause instanceof Error ? cause.message : String(cause)
    status.value = 'That did not work'
  } finally {
    answer.streaming = false
    sending.value = false
    controller = null
    conversations.value = await api.conversations(slug.value).catch(() => conversations.value)
  }
}

function stop() {
  controller?.abort()
  status.value = 'Stopped'
}

async function pin(spec: ChartSpec, key: string) {
  pinning.value = key
  const done = await withToast(() => api.pinChart(slug.value, spec), {
    success: 'Pinned to Home',
    failure: 'Could not pin that chart',
  })
  if (done !== undefined) pinned.value = new Set([...pinned.value, key])
  pinning.value = null
}

function askRename(conversation: ConversationSummary) {
  renaming.value = conversation
  renameTo.value = conversation.title ?? ''
}

async function confirmRename() {
  const target = renaming.value
  const next = renameTo.value.trim()
  if (!target || !next) return
  renaming.value = null
  const updated = await withToast(() => api.renameConversation(slug.value, target.id, next), {
    success: 'Renamed',
    failure: 'Could not rename that',
  })
  if (updated) {
    conversations.value = conversations.value.map((row) => (row.id === updated.id ? updated : row))
  }
}

async function confirmDelete() {
  const target = deleting.value
  if (!target) return
  deleting.value = null
  const done = await withToast(() => api.deleteConversation(slug.value, target.id), {
    success: 'Conversation deleted',
    failure: 'Could not delete that',
  })
  if (done !== undefined) {
    conversations.value = conversations.value.filter((row) => row.id !== target.id)
    if (conversationId.value === target.id) startNew()
  }
}

function submit(event: Event) {
  event.preventDefault()
  void send()
}

/** Enter sends; shift+enter is a newline, as everywhere else. */
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void send()
  }
}

onMounted(loadShell)
watch(slug, async () => {
  startNew()
  await loadShell()
})
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <!--
    `100%` of the frame the shell handed over, and nothing taller. The thread is
    the only thing that scrolls.
  -->
  <div class="mx-auto flex h-full max-w-3xl flex-col px-4 sm:px-6">
    <div class="flex shrink-0 items-center justify-between gap-2 py-2">
      <!--
        The heading is for the screen reader and for the focus move on
        navigation, not for the eye: the shell's top bar already says "Ask" and
        names the shop directly above this. Printing both again cost sixty
        pixels of a phone screen to say the same thing twice, and truncated the
        shop's name doing it.
      -->
      <h1 class="sr-only">Ask about {{ shop.name }}</h1>
      <p v-if="info" class="min-w-0 truncate text-sm text-ink-muted">
        {{ info.tools.length }} tools · data to {{ info.data_to ?? 'nowhere yet' }}
      </p>
      <div class="flex shrink-0 items-center gap-2">
        <UiButton size="sm" variant="secondary" @click="historyOpen = true">History</UiButton>
        <UiButton size="sm" variant="secondary" @click="startNew">New</UiButton>
      </div>
    </div>

    <!-- Short states only. The answer itself is in the thread to be read. -->
    <p class="sr-only" aria-live="polite">{{ status }}</p>

    <div ref="thread" class="min-h-0 flex-1 space-y-5 overflow-y-auto pb-4">
      <div v-if="loadingShell" class="rounded-lg border border-border bg-surface p-5">
        <UiSkeleton :lines="4" />
      </div>

      <div
        v-else-if="error"
        class="rounded-lg border border-danger bg-danger-subtle p-4"
        role="alert"
      >
        <p class="font-medium text-ink">We could not reach the analyst.</p>
        <p class="mt-1 text-sm text-ink-muted">{{ error }}</p>
        <UiButton class="mt-3" size="sm" variant="secondary" @click="loadShell">Try again</UiButton>
      </div>

      <div v-else-if="!turns.length && info" class="rise rounded-lg border border-border bg-surface p-5">
        <p class="text-base text-ink-muted">
          Every number comes from {{ shop.name }}'s own data, through the same definitions Home
          uses. Ask anything it can answer — it will say so when it cannot.
        </p>
        <ul class="mt-4 space-y-2">
          <li v-for="starter in info.starters" :key="starter">
            <button
              type="button"
              class="flex min-h-11 w-full items-center rounded-md border border-border px-3 py-2 text-left text-base text-ink hover:border-border-strong hover:bg-raised"
              @click="send(starter)"
            >
              {{ starter }}
            </button>
          </li>
        </ul>
        <div v-if="info.caveats.length" class="mt-4 flex gap-2">
          <svg
            class="mt-0.5 h-4 w-4 flex-none text-warning"
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            aria-hidden="true"
          >
            <circle cx="10" cy="10" r="8" />
            <path d="M10 6.2v4.4M10 13.4h.01" stroke-linecap="round" />
          </svg>
          <div class="min-w-0">
            <p v-for="caveat in info.caveats" :key="caveat" class="text-sm leading-snug text-ink-muted">
              {{ caveat }}
            </p>
          </div>
        </div>
      </div>

      <article v-for="turn in turns" :key="turn.id" class="rise">
        <p
          v-if="turn.role === 'user'"
          class="ml-auto w-fit max-w-[85%] rounded-lg rounded-br-sm bg-primary px-3 py-2 text-base text-primary-fg"
        >
          {{ turn.text }}
        </p>

        <div v-else>
          <div v-if="turn.tools.length" class="mb-2">
            <ToolChip
              v-for="(call, index) in turn.tools"
              :key="`${call.tool}-${index}`"
              :tool="call.tool"
              :args="call.args"
              :ms="call.ms"
              :error="call.error"
              :result="call.result"
              :running="turn.streaming && call.ms === undefined"
            />
          </div>

          <!-- The only v-html in the app, and the reason lib/markdown.ts exists:
               the model's markdown is parsed and then put through DOMPurify with
               a tag allowlist, so what lands here is the sanitised output rather
               than anything the model wrote. -->
          <!-- eslint-disable vue/no-v-html -->
          <div
            v-if="turn.text"
            class="answer text-base leading-relaxed text-ink"
            :aria-busy="turn.streaming ? 'true' : 'false'"
            v-html="renderMarkdown(turn.text)"
          />
          <!-- eslint-enable vue/no-v-html -->
          <p v-else-if="turn.streaming" class="working text-base text-ink-muted">
            Reading the data…
          </p>

          <!-- Full width on a phone: a chart squeezed into a bubble is a smear. -->
          <div
            v-for="(spec, index) in turn.charts"
            :key="chartKey(turn, index)"
            class="mt-4 rounded-lg border border-border bg-surface p-3"
          >
            <ChartRenderer :spec="spec" :currency="shop.currency" :height="220" />
            <div class="mt-1 flex justify-end">
              <UiButton
                size="sm"
                variant="ghost"
                :disabled="pinned.has(chartKey(turn, index))"
                :loading="pinning === chartKey(turn, index)"
                @click="pin(spec, chartKey(turn, index))"
              >
                {{ pinned.has(chartKey(turn, index)) ? 'Pinned to Home' : 'Pin to Home' }}
              </UiButton>
            </div>
          </div>

          <p v-if="turn.failed" class="mt-2 rounded-md bg-danger-subtle px-3 py-2 text-base text-ink">
            {{ turn.failed }}
          </p>

          <p v-if="turn.done" class="tabular mt-2 text-sm text-ink-muted">
            {{ duration(turn.done.latency_ms) }} · {{ money(turn.done.cost_usd) }} ·
            {{ turn.done.input_tokens.toLocaleString() }} in /
            {{ turn.done.output_tokens.toLocaleString() }} out
          </p>
        </div>
      </article>
    </div>

    <form
      class="shrink-0 pb-[calc(4.5rem+env(safe-area-inset-bottom,0px))] lg:pb-4"
      @submit="submit"
    >
      <div class="flex items-end gap-2 rounded-lg border border-border-strong bg-surface p-2">
        <label class="sr-only" for="question">Your question</label>
        <textarea
          id="question"
          ref="composer"
          v-model="draft"
          rows="1"
          class="max-h-32 min-h-11 flex-1 resize-none bg-transparent px-2 py-2.5 text-base text-ink outline-none placeholder:text-ink-muted"
          placeholder="Ask a question…"
          @keydown="onKeydown"
        />
        <UiButton v-if="sending" variant="secondary" @click.prevent="stop">Stop</UiButton>
        <UiButton v-else type="submit" :disabled="!draft.trim()">Ask</UiButton>
      </div>
    </form>
  </div>

  <UiSheet v-model:open="historyOpen" title="History" side="right">
    <p v-if="!conversations.length" class="px-4 py-4 text-base text-ink-muted">
      Nothing asked yet.
    </p>
    <ul v-else class="divide-y divide-border">
      <li
        v-for="conversation in conversations"
        :key="conversation.id"
        class="px-2 py-1"
        :class="{ 'bg-raised': conversation.id === conversationId }"
      >
        <button
          type="button"
          class="min-h-11 w-full rounded-md px-2 text-left hover:bg-raised"
          @click="openConversation(conversation.id)"
        >
          <span class="block truncate text-base font-medium text-ink">
            {{ conversation.title ?? 'Untitled' }}
          </span>
          <span class="block text-sm text-ink-muted">{{ sinceNow(conversation.updated_at) }}</span>
        </button>
        <div class="flex gap-1 px-1 pb-1">
          <UiButton size="sm" variant="ghost" @click="askRename(conversation)">Rename</UiButton>
          <UiButton size="sm" variant="ghost" @click="deleting = conversation">Delete</UiButton>
        </div>
      </li>
    </ul>
  </UiSheet>

  <UiDialog
    :open="renaming !== null"
    title="Rename this conversation"
    @update:open="(value: boolean) => !value && (renaming = null)"
  >
    <label class="block">
      <span class="mb-1 block text-sm font-medium text-ink">Name</span>
      <input
        v-model="renameTo"
        class="min-h-11 w-full rounded-md border border-border-strong bg-surface px-3 text-base text-ink"
        @keydown.enter.prevent="confirmRename"
      />
    </label>
    <template #footer="{ close }">
      <UiButton variant="ghost" @click="close()">Cancel</UiButton>
      <UiButton :disabled="!renameTo.trim()" @click="confirmRename">Save</UiButton>
    </template>
  </UiDialog>

  <UiDialog
    :open="deleting !== null"
    title="Delete this conversation?"
    description="This cannot be undone."
    @update:open="(value: boolean) => !value && (deleting = null)"
  >
    <p class="text-base text-ink-muted">
      “{{ deleting?.title ?? 'Untitled' }}” and everything in it.
    </p>
    <template #footer="{ close }">
      <UiButton variant="ghost" @click="close()">Keep it</UiButton>
      <UiButton variant="danger" @click="confirmDelete">Delete</UiButton>
    </template>
  </UiDialog>
</template>
