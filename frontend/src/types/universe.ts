export type AssetType = 'market' | 'etf' | 'equity'

export interface UniverseRoles {
  market_benchmark: boolean
  equity_watchlist: boolean
  cta_proxy: boolean
  options_collection: boolean
  event_tracking: boolean
  ai_candidate: boolean
}

export interface InstrumentConfig {
  symbol: string
  display_name: string
  asset_type: AssetType
  theme: string
  /** Optional while reading a pre-0014 API response; writes always include it. */
  themes?: string[]
  enabled: boolean
  roles: UniverseRoles
  benchmarks: { relative_strength: string | null; cta_proxy_for: string | null }
  collection: { quote: boolean; daily_history: boolean; options: boolean }
  notes: string
}

export interface UniverseDerivedScopes {
  market_symbols: string[]
  instrument_symbols: string[]
  history_symbols?: string[]
  cta_proxy_symbols: string[]
  option_symbols: string[]
  event_symbols: string[]
  ai_candidate_symbols: string[]
}

export interface UniverseResponse {
  version_id: string
  revision: number
  content_sha256: string
  source: 'environment' | 'runtime'
  created_at: string
  items: InstrumentConfig[]
  derived: UniverseDerivedScopes
  capacity?: HistoryCapacitySnapshot
  collection_states?: Record<string, HistoryCollectionState>
}

export interface HistoryCapacitySnapshot {
  id?: string
  provider?: string
  enabled?: boolean
  quota_kind?: string
  available?: boolean
  used?: number | null
  remain?: number | null
  total?: number | null
  reserve?: number | null
  quality_status?: string
  warning?: string | null
  captured_at?: string | null
  expires_at?: string | null
  [key: string]: unknown
}

export interface HistoryCollectionState {
  symbol: string
  provider?: string
  access_state: string
  quality_state?: string
  reason_code?: string | null
  message?: string | null
  bar_count?: number
  latest_bar_date?: string | null
  required_through_date?: string | null
  minimum_bar_count?: number
  quota_cost?: number
  updated_at?: string
  [key: string]: unknown
}

export interface UniverseCapacityPlan {
  schema_version: string
  plan_id: string
  provider: string
  universe_content_sha256: string
  captured_at: string
  expires_at?: string | null
  quota: HistoryCapacitySnapshot
  summary: Record<string, number>
  symbols: Array<{
    symbol: string
    cache_state?: string
    bar_count?: number
    latest_bar_date?: string | null
    quota_cost?: number
    decision: string
    reason_code?: string | null
    required_through_date?: string | null
  }>
  warnings: string[]
}

export interface HistoryCollectionProjection {
  provider: string
  captured_at?: string | null
  capacity: HistoryCapacitySnapshot
  states: Record<string, HistoryCollectionState>
  warnings: string[]
}

export interface UniverseUpdate {
  base_version_id: string | null
  items: InstrumentConfig[]
}
