/**
 * Turning the API's strings into something a shop owner reads.
 *
 * Money arrives as a decimal string and stays one until the last moment.
 * `Intl.NumberFormat` does take a string, so nothing here has to round-trip
 * through a float to put a thousands separator in.
 *
 * Dates are formatted in the *shop's* timezone, never the browser's. Someone
 * looking at a Knoxville shop from a laptop set to UTC must still see the day
 * the till saw.
 */

import type { Caveat, Ratio } from '../api/types'

export function money(value: string | number | null | undefined, currency = 'USD'): string {
  if (value === null || value === undefined) return '—'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: 2,
  }).format(Number(value))
}

/**
 * Money for a headline.
 *
 * Cents are dropped once a figure is into the thousands, because $18,535 is the
 * number someone repeats out loud — but an average order value of $22.95 is not
 * $23, and rounding it there would be the one place this lies.
 */
export function moneyShort(value: string | number | null | undefined, currency = 'USD'): string {
  if (value === null || value === undefined) return '—'
  const number = Number(value)
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: Math.abs(number) >= 1000 ? 0 : 2,
  }).format(number)
}

export function count(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return new Intl.NumberFormat('en-US').format(Number(value))
}

/** Quantities can be fractional; they just should not print as 12.000. */
export function quantity(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(Number(value))
}

export function percent(value: Ratio | number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return '—'
  return new Intl.NumberFormat('en-US', {
    style: 'percent',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Number(value))
}

/** A change, with its sign kept: "+12.4%" reads differently from "12.4%". */
export function signedPercent(value: Ratio | number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${percent(number)}`
}

export function days(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const number = Number(value)
  return `${new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 }).format(number)}d`
}

export function kpiValue(
  value: string,
  unit: 'money' | 'count' | 'units' | 'percent' | 'days',
  currency: string,
): string {
  switch (unit) {
    case 'money':
      return moneyShort(value, currency)
    case 'percent':
      return percent(value)
    case 'days':
      return days(value)
    case 'units':
      return quantity(value)
    default:
      return count(value)
  }
}

export function shopDate(iso: string | null | undefined, timezone: string): string {
  if (!iso) return '—'
  const date = iso.length === 10 ? new Date(`${iso}T12:00:00Z`) : new Date(iso)
  return new Intl.DateTimeFormat('en-US', {
    timeZone: iso.length === 10 ? 'UTC' : timezone,
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(date)
}

export function shopDateTime(iso: string | null | undefined, timezone: string): string {
  if (!iso) return '—'
  return new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(iso))
}

/** "3 minutes ago" — for a last-sync line, where the gap is the whole point. */
export function sinceNow(iso: string | null | undefined): string {
  if (!iso) return 'never'
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000
  const relative = new Intl.RelativeTimeFormat('en-US', { numeric: 'auto' })
  const steps: [Intl.RelativeTimeFormatUnit, number][] = [
    ['second', 60],
    ['minute', 60],
    ['hour', 24],
    ['day', 30],
    ['month', 12],
  ]

  let amount = seconds
  for (const [unit, size] of steps) {
    if (Math.abs(amount) < size) return relative.format(-Math.round(amount), unit)
    amount /= size
  }
  return relative.format(-Math.round(amount), 'year')
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/** The same caveat can come back from two metrics in one response. */
export function uniqueCaveats(...lists: (Caveat[] | undefined | null)[]): Caveat[] {
  const seen = new Set<string>()
  const out: Caveat[] = []
  for (const list of lists) {
    for (const caveat of list ?? []) {
      if (!seen.has(caveat.code)) {
        seen.add(caveat.code)
        out.push(caveat)
      }
    }
  }
  return out
}

const TOOL_WORDS: Record<string, string> = {
  search_catalog: 'Searching the catalog',
  sales_summary: 'Totting up sales',
  sales_over_time: 'Reading the trend',
  top_products: 'Ranking products',
  category_breakdown: 'Splitting by category',
  location_channel_breakdown: 'Splitting by where it sold',
  low_stock: 'Checking what is running out',
  dead_stock: 'Looking for stock sitting still',
  sell_through: 'Working out sell-through',
  inventory_value: 'Valuing the shelves',
  margin_report: 'Working out margin',
  customer_stats: 'Counting regulars',
  make_chart: 'Drawing a chart',
}

/** What a tool call is doing, in words rather than a function name. */
export function toolLabel(name: string): string {
  return TOOL_WORDS[name] ?? name.replace(/_/g, ' ')
}
