export interface IntradayPoolVersion {
  revision: number
  source: 'legacy_capture' | 'runtime'
  effective_date: string | null
  selected_symbols: string[]
  member_symbols: string[]
  display_group_by_symbol: Record<string, string>
  source_capture_id: string | null
}

export interface IntradayPoolMember {
  symbol: string
  display_name: string
  asset_type: string
  themes: string[]
  display_group: string
  fixed: boolean
}

export interface IntradayPoolSettings {
  status: 'configured' | 'pending_only' | 'unconfigured'
  revision: number
  benchmark_symbols: string[]
  active: IntradayPoolVersion | null
  pending: IntradayPoolVersion | null
  available_symbols: IntradayPoolMember[]
  warning: string | null
}
