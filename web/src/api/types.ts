/**
 * The API's shapes, mirrored.
 *
 * Money arrives as a string, not a number: it is a `Decimal` on the way out and
 * `0.1 + 0.2` is where a shop stops trusting a dashboard. Nothing here parses
 * one into a float — formatting reads the string, and arithmetic happens in the
 * semantic layer where it belongs.
 */

export type Money = string
export type Quantity = string
/** A 0–1 proportion, or null where there was nothing to divide by. */
export type Ratio = string | null

export interface Caveat {
  code: string
  message: string
}

/** A widget, or the sentence explaining why this shop cannot have one. */
export interface Widget<T> {
  available: boolean
  reason: string | null
  data: T | null
}

export interface Capabilities {
  has_costs: boolean
  has_customers: boolean
  has_inventory_history: boolean
  multi_location: boolean
  has_online_channel: boolean
  supports_incremental: boolean
}

/**
 * What somebody may do at a shop. Ordered: an owner can do everything a manager
 * can, a manager everything staff can. The API enforces it; the UI uses it to
 * hide a button rather than offer one that will be refused.
 */
export type MemberRole = 'owner' | 'manager' | 'staff'

export interface TenantSummary {
  tenant: string
  name: string
  timezone: string
  currency: string
  capabilities: Capabilities
  role: MemberRole
}

export interface ShopMembership {
  tenant: string
  name: string
  role: MemberRole
}

/** The signed-in person, and the shops they may open. */
export interface Me {
  email: string
  display_name: string | null
  shops: ShopMembership[]
}

export interface ShopMember {
  id: string
  email: string
  display_name: string | null
  role: MemberRole
  /** True for the caller's own row. */
  is_you: boolean
}

export interface ShopLocation {
  id: string
  name: string
}

/**
 * One register a shop runs.
 *
 * `capabilities` is this register's own, not the shop's union. That distinction
 * is the whole reason this is here: "the marketplace export has no costs" is the
 * sentence that makes a partial margin figure make sense, and the union cannot
 * say it.
 */
export interface ShopSource {
  source: string
  label: string
  capabilities: Capabilities
}

export interface ShopProfile {
  tenant: string
  name: string
  timezone: string
  currency: string
  today: string
  capabilities: Capabilities
  locations: ShopLocation[]
  channels: string[]
  categories: string[]
  /** Every register this shop runs. One entry for most shops. */
  sources: ShopSource[]
  data_from: string | null
  data_to: string | null
  /** The shop's own color, or null for the palette we ship. See lib/brand.ts. */
  brand_color: string | null
}

export interface Appearance {
  tenant: string
  brand_color: string | null
}

export interface Period {
  start: string
  end: string
  days: number
  label: string
}

export interface Kpi {
  key: string
  label: string
  definition: string
  formula: string
  unit: 'money' | 'count' | 'units' | 'percent' | 'days'
  value: Money
  previous: Money | null
  change: Ratio
}

export interface SeriesPoint {
  bucket: string
  label: string
  gross_sales: Money
  discounts: Money
  refunds: Money
  net_sales: Money
  units_sold: Quantity
  order_count: number
}

export interface SalesSeries {
  tenant: string
  currency: string
  timezone: string
  period_start: string
  period_end: string
  caveats: Caveat[]
  grain: 'day' | 'week' | 'month'
  points: SeriesPoint[]
}

export interface BreakdownRow {
  key: string | null
  label: string
  gross_sales: Money
  discounts: Money
  net_sales: Money
  units_sold: Quantity
  order_count: number
  share_of_net_sales: Ratio
}

export interface Breakdown {
  tenant: string
  currency: string
  timezone: string
  period_start: string
  period_end: string
  caveats: Caveat[]
  dimension: string
  rows: BreakdownRow[]
  total_net_sales: Money
  truncated: boolean
}

export interface StockRow {
  variant_id: string
  product_name: string
  variant_name: string | null
  sku: string | null
  category: string | null
  units_on_hand: Quantity
  price: Money | null
  cost: Money | null
  retail_value: Money
  daily_units: Quantity
  days_of_cover: string | null
  last_sold_at: string | null
  days_since_last_sale: number | null
  reason: string | null
}

export interface StockList {
  tenant: string
  currency: string
  timezone: string
  period_start: string
  period_end: string
  caveats: Caveat[]
  rows: StockRow[]
  row_count: number
  truncated: boolean
  retail_value: Money
}

export interface Dashboard {
  tenant: string
  name: string
  currency: string
  timezone: string
  period: Period
  previous: Period
  kpis: Kpi[]
  kpi_caveats: Caveat[]
  sales_over_time: Widget<SalesSeries>
  top_products: Widget<Breakdown>
  category_mix: Widget<Breakdown>
  by_location: Widget<Breakdown>
  by_channel: Widget<Breakdown>
  /**
   * Net sales per register. Unavailable, with a sentence, for a shop whose tills
   * are all the same system — which is most shops, and where a one-bar chart
   * would be noise.
   */
  by_source: Widget<Breakdown>
  low_stock: Widget<StockList>
  dead_stock: Widget<StockList>
}

export interface StockAtLocation {
  location_id: string
  location: string
  on_hand: Quantity
}

export interface CatalogRow {
  variant_id: string
  product_id: string
  product_name: string
  variant_name: string | null
  label: string
  sku: string | null
  barcode: string | null
  category: string | null
  price: Money | null
  cost: Money | null
  unit_margin: Ratio
  units_on_hand: Quantity
  retail_value: Money
  stock: StockAtLocation[]
  last_sold_at: string | null
  is_active: boolean
}

export interface CatalogPage {
  tenant: string
  currency: string
  timezone: string
  rows: CatalogRow[]
  total: number
  limit: number
  offset: number
  caveats: Caveat[]
}

export type CatalogSort =
  | 'name'
  | 'sku'
  | 'category'
  | 'price'
  | 'cost'
  | 'margin'
  | 'on_hand'
  | 'retail_value'
  | 'last_sold'

export type ChartType = 'line' | 'bar' | 'pie' | 'table'

/** Our chart format. The same one the assistant draws and the dashboard pins. */
export interface ChartSpec {
  type: ChartType
  title: string
  x: string
  y: string[]
  data: Record<string, unknown>[]
  note?: string | null
}

/**
 * A button an answer offered, already checked against the backend's catalogue.
 *
 * `route` and `task` are the API's, never the model's: an `open` action carries
 * a path this app already has, and a `run` action carries a name this client
 * maps onto one of its own calls rather than a URL it is told to fetch.
 */
export interface AssistantAction {
  key: string
  kind: 'open' | 'email' | 'run'
  label: string
  detail: string | null
  /** For `open`: a path under `/:tenant`, e.g. `stock?tab=reorder`. */
  route: string | null
  /** For `run`: which of the client's own calls to make. */
  task: string | null
  email: EmailDraft | null
}

/** A message the owner reviews and sends themselves. We never send it. */
export interface EmailDraft {
  to: string | null
  subject: string
  body: string
}

export interface PinnedChart {
  id: string
  title: string
  spec: ChartSpec
  position: number
  created_at: string
}

export interface AssistantInfo {
  tenant: string
  shop: string
  timezone: string
  currency: string
  today: string
  data_from: string | null
  data_to: string | null
  capabilities: Capabilities
  tools: string[]
  caveats: string[]
  starters: string[]
}

export interface ConversationSummary {
  id: string
  title: string | null
  updated_at: string
}

export interface ToolCallRecord {
  tool: string
  args?: Record<string, unknown>
  ms?: number
  error?: string | null
  result?: string | null
}

export interface StoredMessage {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  tool_calls: ToolCallRecord[]
  charts: ChartSpec[]
  actions: AssistantAction[]
  created_at: string
}

export interface Integration {
  id: string
  adapter: string
  source: string
  is_active: boolean
  capabilities: Record<string, boolean>
  config: Record<string, unknown>
  secret_ref: string | null
}

export interface SyncRun {
  id: string
  mode: 'backfill' | 'incremental'
  status: 'running' | 'succeeded' | 'failed'
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  counts: Record<string, { fetched?: number; upserted?: number; soft_deleted?: number }>
  errors: unknown[]
}

export interface QualityFinding {
  code: string
  severity: 'info' | 'warning' | 'error'
  message: string
  count: number
  share: number | null
}

export interface QualityReport {
  generated_at: string
  metrics: Record<string, unknown>
  findings: QualityFinding[]
}

export interface UploadedDocument {
  id: string
  title: string
  filename: string | null
  characters: number
  chunks: number
  created_at: string
}

export interface DataScreen {
  tenant: string
  name: string
  timezone: string
  integrations: Integration[]
  last_sync: SyncRun | null
  history: SyncRun[]
  quality: QualityReport | null
  documents: UploadedDocument[]
  syncing: boolean
}

export interface SyncStarted {
  tenant: string
  mode: 'backfill' | 'incremental'
  started: boolean
  detail: string
}

// ---------------------------------------------------------------------------
// The proactive half: findings, and the screens that act on them
// ---------------------------------------------------------------------------

export type InsightSeverity = 'info' | 'warn' | 'urgent'
export type InsightStatus = 'new' | 'seen' | 'acted' | 'dismissed' | 'expired' | 'snoozed'

/** What an insight offers to do about itself. `route` is a path inside the app. */
export interface SuggestedAction {
  type?: string
  label?: string
  route?: string
  [key: string]: unknown
}

export interface Insight {
  id: string
  kind: string
  severity: InsightSeverity
  status: InsightStatus
  title: string
  summary: string
  dollar_impact: Money | null
  /** The exact numbers behind the claim, shown when a row is expanded. */
  evidence: Record<string, unknown>
  suggested_action: SuggestedAction
  as_of: string
  created_at: string
  expires_at: string | null
  snoozed_until: string | null
  was_useful: boolean | null
}

export interface Inbox {
  tenant: string
  insights: Insight[]
  counts: Record<string, number>
  kinds: string[]
}

export interface ValueLedger {
  tenant: string
  start: string
  end: string
  insights_created: number
  insights_acted: number
  attributed_revenue: Money
  cash_recovered: Money
  /** What the open findings say is still available. Never added to the two above. */
  flagged_impact: Money
  outcomes: number
  has_anything_to_show: boolean
}

export interface DetectorRun {
  kind: string
  drafts: number
  created: number
  updated: number
  skipped_reason: string | null
  error: string | null
}

export interface ReorderLine {
  variant_id: string
  label: string
  sku: string | null
  category: string | null
  location: string | null
  on_hand: Quantity
  velocity_per_day: Quantity
  seasonal_factor: string
  days_of_cover: Quantity | null
  lead_time_days: number
  suggested_qty: Quantity
  unit_cost: Money | null
  line_cost: Money | null
  /** The sentence that makes the number arguable rather than magic. */
  why: string
  caveats: string[]
  urgent: boolean
}

export interface VendorGroup {
  vendor_id: string | null
  vendor_name: string
  lines: ReorderLine[]
  total_at_cost: Money | null
  unpriced_lines: number
}

export interface ReorderScreen {
  tenant: string
  as_of: string
  groups: VendorGroup[]
  total_at_cost: Money
  cost_coverage: Ratio
  skipped: Record<string, number>
  caveats: string[]
}

export interface PurchaseOrderLine {
  id: string
  variant_id: string
  name: string
  sku: string | null
  quantity: Quantity
  suggested_qty: Quantity
  unit_cost: Money | null
  line_cost: Money | null
  on_hand_at_draft: Quantity
  why: string
  caveats: string[]
  received_qty: Quantity | null
}

export type PurchaseOrderStatus = 'draft' | 'sent' | 'received' | 'canceled'

export interface PurchaseOrder {
  id: string
  reference: string
  vendor_id: string | null
  vendor_name: string
  vendor_email: string | null
  status: PurchaseOrderStatus
  note: string | null
  expected_at: string | null
  units: Quantity
  total_at_cost: Money | null
  unpriced_lines: number
  lines: PurchaseOrderLine[]
  /** A pre-filled mailto. The owner presses send, not us. */
  mailto: string | null
}

export type RescuePlay = 'markdown' | 'bundle' | 'move' | 'return_to_vendor'

export interface MarkdownRung {
  discount: string
  price: Money
  margin_per_unit: Money | null
  clears_cost: boolean
}

export interface StaleItem {
  variant_id: string
  label: string
  sku: string | null
  category: string | null
  kind: 'slowing' | 'stale' | 'dead'
  units_on_hand: Quantity
  days_since_last_sale: number | null
  never_sold: boolean
  cash_tied_up: Money
  cash_at_cost: Money | null
  price: Money | null
  cost: Money | null
  play: RescuePlay
  headline: string
  why: string
  detail: Record<string, unknown>
  ladder: MarkdownRung[]
}

export interface DeadStockScreen {
  tenant: string
  as_of: string
  total_cash: Money
  planned_cash: Money
  item_count: number
  cost_coverage: Ratio
  counts: Record<string, number>
  items: StaleItem[]
}

export interface RescueAction {
  id: string
  variant_id: string
  kind: string
  detail: Record<string, unknown>
  price_before: Money | null
  price_after: Money | null
  on_hand_before: Quantity
  taken_at: string
  measured_at: string | null
  note: string | null
}

export interface HeatmapCell {
  weekday: number
  hour: number
  orders_per_week: Quantity
  net_sales_per_week: Money
  weeks_observed: number
}

export interface Heatmap {
  location_id: string | null
  location_name: string
  weeks: number
  period_start: string
  period_end: string
  weekday_names: string[]
  cells: HeatmapCell[]
  event_cells: HeatmapCell[]
  event_days_excluded: number
}

export interface Shift {
  id: string | null
  location_id: string | null
  weekday: number
  start_time: string
  end_time: string
  staff_count: number
  note: string | null
}

/** An observation about the week. Never an instruction. */
export interface StaffingObservation {
  kind: string
  weekday: number
  weekday_name: string
  hours: number[]
  orders_per_hour: Quantity
  staff_count: number | null
  sentence: string
  labour_cost: Money | null
}

export interface StaffingScreen {
  tenant: string
  heatmaps: Heatmap[]
  shifts: Shift[]
  observations: StaffingObservation[]
  hourly_labour_cost: Money | null
}

export interface MonthEndPacket {
  id: string
  label: string
  period_start: string
  period_end: string
  generated_at: string
  notes: string[]
  emailed_to: string | null
  figures: Record<string, unknown> | null
}

export interface DigestPreview {
  tenant: string
  subject: string
  text: string
  html: string
  /** "model" when the cheap model's wording passed the number check. */
  copy_source: 'model' | 'template'
  week_start: string
  week_end: string
  net_sales: Money
  net_sales_previous: Money | null
  actions: number
  payload: Record<string, unknown>
}

export interface NotificationRecipient {
  id: string
  name: string | null
  email: string
  phone: string | null
  channels: string[]
  quiet_hours_start: string | null
  quiet_hours_end: string | null
  max_per_day: number
  wants_digest: boolean
}

export interface OutboundMessage {
  id: string
  channel: string
  kind: string
  to_address: string
  subject: string | null
  status: 'queued' | 'sent' | 'failed' | 'suppressed'
  /** Why it was held back, or why sending failed. Written for a person. */
  detail: string | null
  created_at: string
  sent_at: string | null
}

export interface JobSchedule {
  job: string
  when: string
  last_due: string
  last_succeeded_at: string | null
  overdue: boolean
}

export interface JobRun {
  id: string
  job: string
  status: 'running' | 'succeeded' | 'failed' | 'skipped'
  due_at: string
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  detail: Record<string, unknown>
  error: string | null
}

export interface JobsScreen {
  tenant: string
  timezone: string
  schedules: JobSchedule[]
  runs: JobRun[]
}
