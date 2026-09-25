/**
 * One way in and out of the API.
 *
 * Every call goes through `request`, so a failure has one shape and every
 * screen can show the same honest thing: what we asked for, and what came
 * back. FastAPI puts a sentence in `detail`, and that sentence is usually
 * written for the shop owner ("Panel & Pawn's system doesn't record costs"),
 * so it is kept rather than replaced with "Something went wrong".
 *
 * Every request carries the signed-in person's access token. It is fetched per
 * request rather than held in a variable: `accessToken()` hands back a refreshed
 * token when the old one has expired, so a tab left open over lunch keeps
 * working. A 401 that survives that is a session that is genuinely gone, and
 * `onUnauthorized` is how the auth store hears about it — this module does not
 * know what a router is.
 */

import type {
  Appearance,
  AssistantInfo,
  CatalogPage,
  CatalogSort,
  ChartSpec,
  ConversationSummary,
  Dashboard,
  DataScreen,
  DeadStockScreen,
  DetectorRun,
  DigestPreview,
  Inbox,
  Insight,
  InsightStatus,
  JobsScreen,
  Me,
  MemberRole,
  MonthEndPacket,
  NotificationRecipient,
  OutboundMessage,
  PinnedChart,
  PurchaseOrder,
  PurchaseOrderStatus,
  ReorderScreen,
  RescueAction,
  RescuePlay,
  ShopMember,
  ShopProfile,
  StaffingScreen,
  StoredMessage,
  SyncStarted,
  TenantSummary,
  ValueLedger,
} from './types'

import { accessToken } from '../lib/supabase'

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

type UnauthorizedHandler = () => void | Promise<void>

let unauthorized: UnauthorizedHandler | null = null

/**
 * Register what to do when the API says the session is no longer good.
 *
 * Called once, by the auth store. Kept as a callback rather than an import so
 * that this module stays the bottom of the dependency graph: the store imports
 * the client, never the other way round.
 */
export function onUnauthorized(handler: UnauthorizedHandler): void {
  unauthorized = handler
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await accessToken()
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.headers ?? {}),
      },
    })
  } catch (cause) {
    // No response at all: the API is not running, or the browser refused it.
    throw new ApiError(0, `Could not reach the API (${String(cause)})`, path)
  }

  if (response.status === 401) {
    // The token was refreshed before this request if it needed to be, so a 401
    // here means the session itself is over.
    await unauthorized?.()
    throw new ApiError(401, await errorMessage(response), path)
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
  /** Who is signed in, and which shops they may open. */
  me: () => request<Me>('/me'),

  setDisplayName: (name: string | null) =>
    request<Me>('/me', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_name: name }),
    }),

  tenants: () => request<TenantSummary[]>('/tenants'),

  members: (tenant: string) => request<ShopMember[]>(`/tenants/${tenant}/members`),

  addMember: (tenant: string, email: string, role: MemberRole) =>
    request<ShopMember>(`/tenants/${tenant}/members`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, role }),
    }),

  setMemberRole: (tenant: string, membershipId: string, role: MemberRole) =>
    request<ShopMember>(`/tenants/${tenant}/members/${membershipId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role }),
    }),

  removeMember: (tenant: string, membershipId: string) =>
    request<void>(`/tenants/${tenant}/members/${membershipId}`, { method: 'DELETE' }),

  profile: (tenant: string) => request<ShopProfile>(`/tenants/${tenant}/profile`),

  /** `null` puts the shop back on the palette we ship. */
  setAppearance: (tenant: string, brandColor: string | null) =>
    request<Appearance>(`/tenants/${tenant}/appearance`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ brand_color: brandColor }),
    }),

  /**
   * `grain` is optional and the API defaults to weeks, so leaving it off is the
   * same request this made before it existed.
   */
  dashboard: (tenant: string, days = 30, weeks = 52, grain?: 'day' | 'week' | 'month') =>
    request<Dashboard>(`/tenants/${tenant}/dashboard${query({ days, weeks, grain })}`),

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

  // -- findings ------------------------------------------------------------

  insights: (
    tenant: string,
    options: { kind?: string; status?: string; limit?: number; offset?: number } = {},
  ) => request<Inbox>(`/tenants/${tenant}/insights${query({ ...options })}`),

  setInsightStatus: (
    tenant: string,
    id: string,
    status: Extract<InsightStatus, 'seen' | 'acted' | 'dismissed' | 'snoozed'>,
    snoozeDays?: number,
  ) =>
    request<Insight>(`/tenants/${tenant}/insights/${id}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, snooze_days: snoozeDays ?? null }),
    }),

  rateInsight: (tenant: string, id: string, useful: boolean, note?: string) =>
    request<void>(`/tenants/${tenant}/insights/${id}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ useful, note: note ?? null }),
    }),

  runDetectors: (tenant: string) =>
    request<DetectorRun[]>(`/tenants/${tenant}/insights/run`, { method: 'POST' }),

  value: (tenant: string) => request<ValueLedger>(`/tenants/${tenant}/value`),

  // -- reordering ----------------------------------------------------------

  reorder: (tenant: string) => request<ReorderScreen>(`/tenants/${tenant}/reorder`),

  createDrafts: (tenant: string, vendorId?: string) =>
    request<string[]>(`/tenants/${tenant}/reorder/drafts${query({ vendor_id: vendorId })}`, {
      method: 'POST',
    }),

  purchaseOrders: (tenant: string, status?: string) =>
    request<PurchaseOrder[]>(`/tenants/${tenant}/purchase-orders${query({ status })}`),

  purchaseOrder: (tenant: string, id: string) =>
    request<PurchaseOrder>(`/tenants/${tenant}/purchase-orders/${id}`),

  updatePurchaseOrderLine: (
    tenant: string,
    lineId: string,
    body: { quantity?: string; remove?: boolean },
  ) =>
    request<void>(`/tenants/${tenant}/purchase-orders/lines/${lineId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  setPurchaseOrderStatus: (tenant: string, id: string, status: PurchaseOrderStatus) =>
    request<PurchaseOrder>(`/tenants/${tenant}/purchase-orders/${id}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    }),

  purchaseOrderFile: (tenant: string, id: string, kind: 'pdf' | 'csv') =>
    `${BASE}/tenants/${tenant}/purchase-orders/${id}.${kind}`,

  // -- dead stock ----------------------------------------------------------

  deadStock: (tenant: string) => request<DeadStockScreen>(`/tenants/${tenant}/dead-stock`),

  logRescue: (
    tenant: string,
    body: {
      variant_id: string
      kind: RescuePlay
      detail?: Record<string, unknown>
      price_after?: string | null
      insight_id?: string | null
      note?: string | null
    },
  ) =>
    request<RescueAction>(`/tenants/${tenant}/dead-stock/actions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  rescueActions: (tenant: string) =>
    request<RescueAction[]>(`/tenants/${tenant}/dead-stock/actions`),

  // -- staffing ------------------------------------------------------------

  staffing: (tenant: string, includeEvents = false) =>
    request<StaffingScreen>(`/tenants/${tenant}/staffing${query({ include_events: includeEvents })}`),

  saveShift: (
    tenant: string,
    body: {
      weekday: number
      start_time: string
      end_time: string
      staff_count: number
      location_id?: string | null
      note?: string | null
    },
  ) =>
    request<string>(`/tenants/${tenant}/staffing/shifts`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  deleteShift: (tenant: string, id: string) =>
    request<void>(`/tenants/${tenant}/staffing/shifts/${id}`, { method: 'DELETE' }),

  // -- month end -----------------------------------------------------------

  packets: (tenant: string) => request<MonthEndPacket[]>(`/tenants/${tenant}/month-end`),

  generatePacket: (tenant: string, year?: number, month?: number) =>
    request<MonthEndPacket>(`/tenants/${tenant}/month-end`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ year: year ?? null, month: month ?? null }),
    }),

  packetFile: (tenant: string, id: string, kind: 'pdf' | 'xlsx') =>
    `${BASE}/tenants/${tenant}/month-end/${id}.${kind}`,

  emailPacket: (tenant: string, id: string, bookkeeper?: string) =>
    request<{ sent: number }>(`/tenants/${tenant}/month-end/${id}/email`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bookkeeper: bookkeeper ?? null }),
    }),

  // -- digest and notifications --------------------------------------------

  digestPreview: (tenant: string) => request<DigestPreview>(`/tenants/${tenant}/digest/preview`),

  digestPreviewUrl: (tenant: string) => `${BASE}/tenants/${tenant}/digest/preview.html`,

  sendTestDigest: (tenant: string, email?: string) =>
    request<{ to: string; status: string; detail: string }>(`/tenants/${tenant}/digest/test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email ?? null }),
    }),

  recipients: (tenant: string) =>
    request<NotificationRecipient[]>(`/tenants/${tenant}/notifications`),

  saveRecipient: (
    tenant: string,
    body: {
      email: string
      name?: string | null
      phone?: string | null
      channels?: string[]
      quiet_hours_start?: string | null
      quiet_hours_end?: string | null
      max_per_day?: number
      wants_digest?: boolean
    },
  ) =>
    request<string>(`/tenants/${tenant}/notifications`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  // Named for what it is, because `messages` already means a conversation's.
  outboundMessages: (tenant: string) =>
    request<OutboundMessage[]>(`/tenants/${tenant}/notifications/messages`),

  jobs: (tenant: string) => request<JobsScreen>(`/tenants/${tenant}/jobs`),

  uploadDocument: async (tenant: string, file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<{ title: string; chunks_embedded: number; tokens: number }>(
      `/tenants/${tenant}/documents`,
      { method: 'POST', body },
    )
  },
}
