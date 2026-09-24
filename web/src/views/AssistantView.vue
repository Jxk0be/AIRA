<script setup lang="ts">
/**
 * The shop's analyst, in conversation.
 *
 * Three things this screen has to get right.
 *
 * It has to show the working. Tool chips appear as the tools run, so six
 * seconds of thinking reads as six seconds of work rather than as a hang.
 *
 * It has to be honest about what this shop can be asked. The starter questions
 * come from the API, built from the tenant's capabilities, so a shop with no
 * customer records is never invited to ask about its regulars.
 *
 * And a chart has to be pinnable exactly as it was drawn. Pinning stores the
 * spec that was validated during the answer, not a fresh query — a pinned
 * chart is a record of what was said.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '../api/client'
import { ask, type DonePayload } from '../api/stream'
import type { AssistantInfo, ChartSpec, ConversationSummary, ToolCallRecord } from '../api/types'
import ChartRenderer from '../components/ChartRenderer.vue'
import ToolChip from '../components/ToolChip.vue'
import { duration, money, sinceNow } from '../lib/format'
import { renderMarkdown } from '../lib/markdown'
import { useTenantStore } from '../stores/tenant'

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
const error = ref<string | null>(null)
const pinning = ref<string | null>(null)
const pinned = ref<Set<string>>(new Set())
const showHistory = ref(false)
const thread = ref<HTMLElement | null>(null)

let controller: AbortController | null = null

/** Unique within the conversation: two turns can draw the same chart. */
function chartKey(turn: Turn, index: number): string {
  return `${turn.id}#${index}`
}

async function scrollDown() {
  await nextTick()
  const box = thread.value
  if (box) box.scrollTop = box.scrollHeight
}

async function loadShell() {
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
  }
}

async function openConversation(id: string) {
  conversationId.value = id
  showHistory.value = false
  pinned.value = new Set()
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
}

function startNew() {
  controller?.abort()
  conversationId.value = null
  turns.value = []
  pinned.value = new Set()
  showHistory.value = false
  draft.value = ''
}

async function send(question?: string) {
  const text = (question ?? draft.value).trim()
  if (!text || sending.value) return

  draft.value = ''
  sending.value = true
  error.value = null

  turns.value.push({
    id: `you-${Date.now()}`,
    role: 'user',
    text,
    tools: [],
    charts: [],
  })
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
          break
        case 'tool_end': {
          // The chip that is already on screen, filled in.
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
          if (event.payload.conversation_id) {
            conversationId.value = event.payload.conversation_id
          }
          break
        case 'error':
          answer.failed = event.message
          break
      }
      await scrollDown()
    }
  } catch (cause) {
    answer.failed = cause instanceof Error ? cause.message : String(cause)
  } finally {
    answer.streaming = false
    sending.value = false
    controller = null
    // A new conversation has just been titled by then.
    conversations.value = await api.conversations(slug.value).catch(() => conversations.value)
  }
}

async function pin(spec: ChartSpec, key: string) {
  pinning.value = key
  try {
    await api.pinChart(slug.value, spec)
    pinned.value = new Set([...pinned.value, key])
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    pinning.value = null
  }
}

async function rename(conversation: ConversationSummary) {
  const next = window.prompt('Rename this conversation', conversation.title ?? '')
  if (!next?.trim()) return
  const updated = await api.renameConversation(slug.value, conversation.id, next.trim())
  conversations.value = conversations.value.map((row) =>
    row.id === updated.id ? updated : row,
  )
}

async function remove(conversation: ConversationSummary) {
  if (!window.confirm(`Delete "${conversation.title ?? 'this conversation'}"?`)) return
  await api.deleteConversation(slug.value, conversation.id)
  conversations.value = conversations.value.filter((row) => row.id !== conversation.id)
  if (conversationId.value === conversation.id) startNew()
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
  <div class="mx-auto flex h-full max-w-4xl flex-col px-4 sm:px-6">
    <header class="flex items-center justify-between gap-3 py-4">
      <div class="min-w-0">
        <h1 class="display truncate text-xl font-semibold tracking-tight text-ink sm:text-2xl">
          Ask {{ shop.name }}'s analyst
        </h1>
        <p v-if="info" class="text-xs text-ink-faint">
          {{ info.tools.length }} tools · data to {{ info.data_to ?? 'nowhere yet' }}
        </p>
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <button
          type="button"
          class="border border-rule bg-panel px-2.5 py-1 text-xs text-ink-muted hover:bg-sunk"
          :aria-expanded="showHistory"
          @click="showHistory = !showHistory"
        >
          History
        </button>
        <button
          type="button"
          class="border border-rule bg-panel px-2.5 py-1 text-xs text-ink-muted hover:bg-sunk"
          @click="startNew"
        >
          New
        </button>
      </div>
    </header>

    <!-- The sidebar is a drawer at every width: on this screen the conversation
         is the thing, and a permanent rail would take a third of a phone. -->
    <section v-if="showHistory" class="mb-3 border border-rule bg-panel">
      <p v-if="!conversations.length" class="px-4 py-3 text-xs text-ink-faint">
        Nothing asked yet.
      </p>
      <ul v-else class="max-h-64 overflow-auto">
        <li
          v-for="conversation in conversations"
          :key="conversation.id"
          class="flex items-center gap-2 border-b border-rule px-3 py-2 text-sm last:border-0"
          :class="{ 'bg-sunk': conversation.id === conversationId }"
        >
          <button
            type="button"
            class="min-w-0 flex-1 truncate text-left text-ink hover:text-brand"
            @click="openConversation(conversation.id)"
          >
            {{ conversation.title ?? 'Untitled' }}
            <span class="ml-2 text-xs text-ink-faint">{{ sinceNow(conversation.updated_at) }}</span>
          </button>
          <button
            type="button"
            class="shrink-0 text-xs text-ink-faint hover:text-ink"
            @click="rename(conversation)"
          >
            Rename
          </button>
          <button
            type="button"
            class="shrink-0 text-xs text-ink-faint hover:text-down"
            @click="remove(conversation)"
          >
            Delete
          </button>
        </li>
      </ul>
    </section>

    <div ref="thread" class="min-h-0 flex-1 space-y-5 overflow-y-auto pb-4">
      <div v-if="!turns.length && info" class="rise border border-rule bg-panel p-5">
        <p class="text-sm text-ink-muted">
          Every number comes from {{ shop.name }}'s own data, through the same definitions the
          dashboard uses. Ask anything it can answer — and it will say so when it cannot.
        </p>
        <ul class="mt-4 space-y-1.5">
          <li v-for="starter in info.starters" :key="starter">
            <button
              type="button"
              class="w-full border-l-2 border-rule py-1 pl-3 text-left text-sm text-ink hover:border-brand hover:text-brand"
              @click="send(starter)"
            >
              {{ starter }}
            </button>
          </li>
        </ul>
        <p v-if="info.caveats.length" class="mt-4 border-l-2 border-note-rule bg-note px-3 py-2">
          <span
            v-for="caveat in info.caveats"
            :key="caveat"
            class="block text-xs leading-snug text-note-ink"
          >
            {{ caveat }}
          </span>
        </p>
      </div>

      <article v-for="turn in turns" :key="turn.id" class="rise">
        <p
          v-if="turn.role === 'user'"
          class="ml-auto w-fit max-w-[85%] border border-rule bg-panel px-3 py-2 text-sm text-ink"
        >
          {{ turn.text }}
        </p>

        <div v-else class="max-w-[95%]">
          <div v-if="turn.tools.length" class="mb-3 space-y-0.5">
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
               the model's markdown is parsed and then put through DOMPurify
               with a tag allowlist, so what lands here is the sanitised output
               rather than anything the model wrote. -->
          <!-- eslint-disable vue/no-v-html -->
          <div
            v-if="turn.text"
            class="answer text-[0.95rem] leading-relaxed text-ink"
            v-html="renderMarkdown(turn.text)"
          />
          <!-- eslint-enable vue/no-v-html -->
          <p v-else-if="turn.streaming" class="text-sm text-ink-faint working">Reading the data…</p>

          <div v-for="(spec, index) in turn.charts" :key="chartKey(turn, index)" class="mt-4">
            <div class="border border-rule bg-panel p-3">
              <ChartRenderer :spec="spec" :currency="shop.currency" :height="230" />
              <div class="mt-2 flex justify-end">
                <button
                  type="button"
                  class="text-xs text-ink-faint hover:text-brand"
                  :disabled="pinned.has(chartKey(turn, index)) || pinning === chartKey(turn, index)"
                  @click="pin(spec, chartKey(turn, index))"
                >
                  {{ pinned.has(chartKey(turn, index)) ? 'Pinned to dashboard' : 'Pin to dashboard' }}
                </button>
              </div>
            </div>
          </div>

          <p v-if="turn.failed" class="mt-2 border-l-2 border-down pl-3 text-sm text-down">
            {{ turn.failed }}
          </p>

          <p v-if="turn.done" class="tabular mt-2 text-[0.68rem] text-ink-faint">
            {{ duration(turn.done.latency_ms) }} · {{ money(turn.done.cost_usd) }} ·
            {{ turn.done.input_tokens.toLocaleString() }} in /
            {{ turn.done.output_tokens.toLocaleString() }} out
          </p>
        </div>
      </article>
    </div>

    <p v-if="error" class="mb-2 border-l-2 border-down pl-3 text-sm text-down" role="alert">
      {{ error }}
    </p>

    <form class="mb-4 flex items-end gap-2 border border-rule bg-panel p-2" @submit="submit">
      <label class="sr-only" for="question">Your question</label>
      <textarea
        id="question"
        v-model="draft"
        rows="2"
        class="min-h-[2.75rem] flex-1 resize-none bg-transparent px-2 py-1.5 text-sm text-ink outline-none placeholder:text-ink-faint"
        :placeholder="`Ask about ${shop.name}…`"
        :disabled="sending"
        @keydown="onKeydown"
      />
      <button
        type="submit"
        class="shrink-0 bg-brand px-3 py-2 text-sm font-medium text-paper disabled:opacity-40"
        :disabled="sending || !draft.trim()"
      >
        {{ sending ? 'Asking…' : 'Ask' }}
      </button>
    </form>
  </div>
</template>
