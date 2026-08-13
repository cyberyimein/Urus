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
  enabled: boolean
  roles: UniverseRoles
  benchmarks: { relative_strength: string | null; cta_proxy_for: string | null }
  collection: { quote: boolean; daily_history: boolean; options: boolean }
  notes: string
}

export interface UniverseDerivedScopes {
  market_symbols: string[]
  instrument_symbols: string[]
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
}

export interface UniverseUpdate {
  base_version_id: string | null
  items: InstrumentConfig[]
}
