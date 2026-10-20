export type IntradayStatus =
  | 'disabled'
  | 'no_data'
  | 'invalid_source'
  | 'fresh'
  | 'partial'
  | 'stale'
  | 'clock_skew'
  | 'ok'
  | 'unavailable'
  | 'quote_only'
  | 'not_found'

export interface IntradayMacdPoint {
  time: string
  value: number | null
}

export interface IntradayMacdMarker {
  time: string
  type: string
  label: string
  from_sign: string
  to_sign: string
  value: number | null
}

export interface IntradayMacdSeries {
  series_id: string
  kind: string
  unit: string
  points: IntradayMacdPoint[]
  markers?: IntradayMacdMarker[]
}

export interface IntradayMacdSummary {
  available: boolean
  quality_status: string
  as_of: string | null
  sample_count: number
  histogram: number | null
  histogram_delta: number | null
  histogram_delta2: number | null
  histogram_signals: Record<string, { sign: string; transition: string; label: string }>
  series: IntradayMacdSeries[]
  warnings: string[]
  [key: string]: unknown
}

export interface IntradayFeatures {
  ma5: number | null
  ma5_distance_pct: number | null
  ma5_slope: number | null
  atr14: number | null
  session_vwap: number | null
  rs15: number | null
  rs30: number | null
  rvol: number | null
  macd: IntradayMacdSummary
  macd_warmup_bars: number
  macd_signal_eligible: boolean
  [key: string]: unknown
}

export interface IntradaySymbolProjection {
  symbol: string
  observation_group?: string | null
  role: 'A' | 'B' | 'C' | 'D' | string | null
  status: IntradayStatus | string
  quote: IntradayQuote | null
  as_of: string | null
  coverage: Record<string, number | boolean>
  bars: IntradayBar[]
  features: IntradayFeatures
  reasons: Record<string, string>
}

export interface IntradayQuote {
  last_price: number | null
  provider: string
  quote_time: string | null
  quote_time_raw?: string | null
  quote_time_status: 'known' | 'unknown' | string
  received_at: string | null
  quality_status: string
  price_basis?: 'raw' | 'unknown' | string
}

export interface IntradayBar {
  symbol: string
  bar_start_at: string
  bar_end_at: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  turnover: number
  vwap: number | null
  minute_count: number
  quality_state: string
}

export interface IntradaySource {
  capture_id: string
  content_sha256: string
  kind: string
  schema_version: string
  provider: string
  task_name: string
  trading_date: string
  scheduled_at: string
  cutoff: string
  captured_at: string
  stored_at?: string | null
}

export interface IntradayProjection {
  schema_version: string
  formula_version: string
  status: IntradayStatus | string
  source: Partial<IntradaySource> | null
  freshness: {
    status: IntradayStatus | string
    age_seconds: number | null
    max_age_seconds?: number
    as_of: string | null
    expected_latest_bar_end?: string | null
    stale_minute_symbols?: string[]
  }
  quality?: Record<string, unknown>
  coverage: Record<string, unknown>
  symbols: Record<string, IntradaySymbolProjection>
  breadth: Record<string, any>
  warnings: string[]
}

export interface IntradaySymbolDetail {
  schema_version: string
  formula_version: string
  status: IntradayStatus | string
  symbol: string
  source: IntradayProjection['source']
  freshness: IntradayProjection['freshness']
  coverage?: Record<string, unknown>
  as_of?: string | null
  role?: string | null
  quote?: IntradayQuote | null
  bars: IntradayBar[]
  features: Partial<IntradayFeatures>
  reasons: Record<string, string>
  warnings: string[]
}
