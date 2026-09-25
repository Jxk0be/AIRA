/**
 * The assistant's answer, as it is written.
 *
 * `EventSource` cannot be used here: asking a question is a POST with a body,
 * and EventSource only does GET. So this is the same protocol read by hand off
 * `fetch` — which is no loss, because it also means a failed request is an
 * ordinary rejected promise instead of an opaque `onerror`.
 *
 * The parser is deliberately strict about one thing: an event is only
 * dispatched on a blank line. A chunk boundary can land anywhere, including the
 * middle of a UTF-8 character or halfway through a JSON payload, and a parser
 * that guesses will one day drop a paragraph of someone's answer.
 */

import type { AssistantAction, ChartSpec } from './types'

const BASE = import.meta.env.VITE_API_BASE ?? '/api'

export interface DonePayload {
  conversation_id: string | null
  title: string | null
  model: string
  latency_ms: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cost_usd: string
  tools: string[]
}

export type AgentEvent =
  | { type: 'token'; text: string }
  | { type: 'tool_start'; tool: string; args: Record<string, unknown> }
  | { type: 'tool_end'; tool: string; ms: number; error: string | null; result: string | null }
  | { type: 'chart'; spec: ChartSpec }
  | { type: 'action'; action: AssistantAction }
  | { type: 'done'; payload: DonePayload }
  | { type: 'error'; message: string }

function toEvent(name: string, raw: string): AgentEvent | null {
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    return null
  }
  const body = data as Record<string, unknown>

  switch (name) {
    case 'token':
      return { type: 'token', text: String(body.text ?? '') }
    case 'tool_start':
      return {
        type: 'tool_start',
        tool: String(body.tool ?? ''),
        args: (body.args as Record<string, unknown>) ?? {},
      }
    case 'tool_end':
      return {
        type: 'tool_end',
        tool: String(body.tool ?? ''),
        ms: Number(body.ms ?? 0),
        error: (body.error as string) ?? null,
        result: (body.result as string) ?? null,
      }
    case 'chart':
      return { type: 'chart', spec: data as ChartSpec }
    case 'action':
      return { type: 'action', action: data as AssistantAction }
    case 'done':
      return { type: 'done', payload: data as DonePayload }
    case 'error':
      return { type: 'error', message: String(body.message ?? 'Something went wrong') }
    default:
      // An event type this client does not know about yet. Ignored rather than
      // thrown: a newer API must not break an older tab.
      return null
  }
}

export interface AskOptions {
  tenant: string
  message: string
  conversationId?: string | null
  signal?: AbortSignal
}

/**
 * Ask, and yield each event as it arrives.
 *
 * The caller gets exactly one `done` or one `error` last — the API guarantees
 * it, and a dropped connection surfaces as a thrown error here rather than as
 * a stream that simply stops. Charts and actions arrive after the last token,
 * so a button never appears under a half-written answer.
 */
export async function* ask(options: AskOptions): AsyncGenerator<AgentEvent> {
  const response = await fetch(`${BASE}/tenants/${options.tenant}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({
      message: options.message,
      conversation_id: options.conversationId ?? null,
    }),
    signal: options.signal,
  })

  if (!response.ok || !response.body) {
    throw new Error(`The assistant could not be reached (${response.status})`)
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += value

      let split = buffer.indexOf('\n\n')
      while (split !== -1) {
        const block = buffer.slice(0, split)
        buffer = buffer.slice(split + 2)

        let name = 'message'
        const payload: string[] = []
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) name = line.slice(6).trim()
          else if (line.startsWith('data:')) payload.push(line.slice(5).trimStart())
        }

        const event = payload.length ? toEvent(name, payload.join('\n')) : null
        if (event) yield event

        split = buffer.indexOf('\n\n')
      }
    }
  } finally {
    // Leaving the page mid-answer must not leave the connection open.
    await reader.cancel().catch(() => undefined)
  }
}
