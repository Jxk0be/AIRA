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

export interface TenantSummary {
  tenant: string
  name: string
  timezone: string
  currency: string
  capabilities: Capabilities
}

export interface ShopLocation {
  id: string
  name: string
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
  data_from: string | null
  data_to: string | null
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
