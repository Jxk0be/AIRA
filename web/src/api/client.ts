/**
 * One way in and out of the API.
 *
 * Every call goes through `request`, so a failure has one shape and every
 * screen can show the same honest thing: what we asked for, and what came
 * back. FastAPI puts a sentence in `detail`, and that sentence is usually
 * written for the shop owner ("Panel & Pawn's system doesn't record costs"),
 * so it is kept rather than replaced with "Something went wrong".
 */

import type {
  CatalogPage,
  CatalogSort,
  ChartSpec,
  ConversationSummary,
  Dashboard,
  DataScreen,
  AssistantInfo,
  PinnedChart,
  ShopProfile,
  StoredMessage,
  SyncStarted,
  TenantSummary,
} from './types'

/** Dev goes through Vite's proxy; a deployed build talks to the API directly. */
const BASE = import.meta.env.VITE_API_BASE ?? '/api'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly path: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
    })
  } catch (cause) {
    // No response at all: the API is not running, or the browser refused it.
    throw new ApiError(0, `Could not reach the API (${String(cause)})`, path)
  }

  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response), path)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail)) {
      // A validation error: say which field, not "422".
      return body.detail
        .map((item) => {
          const entry = item as { loc?: unknown[]; msg?: string }
          return `${(entry.loc ?? []).slice(1).join('.')}: ${entry.msg ?? 'invalid'}`
        })
        .join('; ')
    }
  } catch {
    // Not JSON — fall through to the status line.
  }
  return `${response.status} ${response.statusText}`
}

function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value))
    }
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export const api = {
  tenants: () => request<TenantSummary[]>('/tenants'),

  profile: (tenant: string) => request<ShopProfile>(`/tenants/${tenant}/profile`),

  dashboard: (tenant: string, days = 30, weeks = 52) =>
    request<Dashboard>(`/tenants/${tenant}/dashboard${query({ days, weeks })}`),

  inventory: (
    tenant: string,
    options: {
      q?: string
      sort?: CatalogSort
      desc?: boolean
      limit?: number
      offset?: number
      location?: string
    } = {},
  ) => request<CatalogPage>(`/tenants/${tenant}/inventory${query({ ...options })}`),

  charts: (tenant: string) => request<PinnedChart[]>(`/tenants/${tenant}/charts`),

  pinChart: (tenant: string, spec: ChartSpec, sourceMessageId?: string) =>
    request<PinnedChart>(`/tenants/${tenant}/charts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec, source_message_id: sourceMessageId ?? null }),
    }),

  unpinChart: (tenant: string, id: string) =>
    request<void>(`/tenants/${tenant}/charts/${id}`, { method: 'DELETE' }),

  assistant: (tenant: string) => request<AssistantInfo>(`/tenants/${tenant}/assistant`),

  conversations: (tenant: string) =>
    request<ConversationSummary[]>(`/tenants/${tenant}/conversations`),

  messages: (tenant: string, id: string) =>
    request<StoredMessage[]>(`/tenants/${tenant}/conversations/${id}`),

  renameConversation: (tenant: string, id: string, title: string) =>
    request<ConversationSummary>(`/tenants/${tenant}/conversations/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    }),

  deleteConversation: (tenant: string, id: string) =>
    request<void>(`/tenants/${tenant}/conversations/${id}`, { method: 'DELETE' }),

  data: (tenant: string) => request<DataScreen>(`/tenants/${tenant}/data`),

  startSync: (tenant: string, mode: 'incremental' | 'backfill' = 'incremental') =>
    request<SyncStarted>(`/tenants/${tenant}/sync${query({ mode })}`, { method: 'POST' }),

  uploadDocument: async (tenant: string, file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<{ title: string; chunks_embedded: number; tokens: number }>(
      `/tenants/${tenant}/documents`,
      { method: 'POST', body },
    )
  },
}
