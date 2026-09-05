import type { PostCloseOptionAlignment } from '@/types/options'

export type DataState = 'live' | 'mock' | 'mixed' | 'placeholder' | 'unavailable' | 'skipped'

export interface HealthResponse {
  status: string
  environment: string
  database: string
}

export interface DatabaseHealthResponse {
  schema_version: string
  status: 'ok' | 'error' | 'unsupported' | string
  checked_at: string
  next_check_at: string
  integrity_check: string | null
  foreign_key_violations: number | null
  duration_ms: number | null
  database_size_bytes: number | null
  message: string | null
}

export interface VersionResponse {
  app_name: string
  app_version: string
  api_schema_version: string
}

export interface ObservationGroup {
  version_id: string
  group_id: string
  version: number
  status: string
  source: 'manual' | 'universe' | string
  universe_revision_id: string | null
  display_name: string
  description: string
  symbols: string[]
  benchmark_symbols: string[]
  tags: string[]
  display_order: number
  content_sha256: string
  created_at: string
  activated_at: string | null
}

export interface ObservationGroupDetail {
  group: ObservationGroup
  latest_snapshot: GroupDailySnapshot | null
}

export interface ObservationGroupSync {
  source: string
  source_url: string | null
  universe_revision_id: string
  universe_version_id: string
  universe_revision: number
  universe_freshness: 'fresh' | 'stale' | 'local' | string
  warnings: string[]
  sync_error?: string | null
  upstream_universe_version_id?: string | null
  upstream_universe_revision?: number | null
  symbol_count: number
  group_count: number
  groups: ObservationGroup[]
}

export interface GroupDailySnapshot {
  schema_version: string
  feature_version: string
  dataset_id: string
  indicator_snapshot_ids: string[]
  group: {
    group_id: string
    version_id: string
    version: number
    display_name: string
    symbols: string[]
    benchmark_symbols: string[]
  }
  trading_date: string
  quality: {
    requested_symbol_count: number
    valid_symbol_count: number
    missing_symbol_count: number
    status: string
    warnings: string[]
  }
  features: {
    valid_symbol_count: number
    requested_symbol_count: number
    missing_symbol_count: number
    returns_percent: Record<string, { count: number; median: number | null; q1: number | null; q3: number | null; min: number | null; max: number | null }>
    breadth: Record<string, number | null>
    rsi_distribution: Record<string, number | null>
    rsi_extremes: Record<string, number | null>
    macd_positive_percent: number | null
    volume_expansion_percent: number | null
    relative_strength: Record<string, string | number | null>
    cross_sectional_dispersion_1d: number | null
    leaders: Array<{ symbol: string; return_percent: number | null }>
    laggards: Array<{ symbol: string; return_percent: number | null }>
    leader_concentration: number | null
  }
  symbols: Array<Record<string, any>>
  charts: {
    relative_strength: { benchmark: string | null; series: Array<{ time: string; value: number | null; benchmark_value?: number | null }>; dispersion: Array<{ time: string; value: number | null }> }
    breadth: { series: Record<string, Array<{ time: string; value: number | null }>> }
    rotation: Array<{ symbol: string; x_relative_20d: number | null; y_relative_change: number | null; size: number | null; stance: string; trend?: string }>
    heatmap: Array<Record<string, any>>
    small_multiples: Array<{ symbol: string; points: Array<{ time: string; value: number | null; ma20: number | null; ma50: number | null }>; return_20d: number | null; trend: string | null }>
  }
  group_decision: { state: string; level?: string; impulse?: string; phase?: string; setup?: string; category?: string; stance: string; action: string; reasons: string[]; participation?: number | null; setup_participation?: number | null; leader_concentration?: number | null; evidence_strength?: string; rank_eligible?: boolean; sample_support?: Record<string, any> }
  symbol_setups?: Record<string, { symbol: string; candidate_count: number; primary_setup: Record<string, any> | null; alternatives: Array<Record<string, any>> }>
  group_strategy_decisions: Array<Record<string, any>>
  changes: {
    previous_trading_date: string | null
    group_state: { from: string | null; to: string | null; changed: boolean }
    median_20d_delta_percent: number | null
    breadth_ma20_delta: number | null
    relative_20d_delta_percent: number | null
    leaders_added: string[]
    leaders_removed: string[]
  }
  strategy_decisions: Array<Record<string, any>>
  deterministic_synthesis: Record<string, any>
  content_sha256: string
}

export interface ObservationReport {
  schema_version: string
  mode: 'deterministic-only' | string
  run_id: string
  trading_date: string
  provenance?: {
    universe_revision_id?: string | null
    universe_freshness?: string
    universe_source_url?: string | null
    dataset_ids?: string[]
    group_version_ids?: string[]
  }
  summary: Record<string, number>
  group_rankings: Array<Record<string, any>>
  ranking_status?: string
  leading_themes?: Array<Record<string, any>>
  deteriorating_themes?: Array<Record<string, any>>
  reversal_themes?: Array<Record<string, any>>
  improving_groups: Array<Record<string, any>>
  deteriorating_groups: Array<Record<string, any>>
  anomalies: { leaders: Array<Record<string, any>>; laggards: Array<Record<string, any>> }
  strategy_conflicts: Array<Record<string, any>>
  quality_issues: Array<Record<string, any>>
  opportunity_lanes: Record<string, Array<Record<string, any>>>
  risk_lanes: Record<string, Array<Record<string, any>>>
  visuals: {
    group_momentum_map: Array<Record<string, any>>
    breadth_delta: Array<Record<string, any>>
    state_transitions: Array<Record<string, any>>
  }
  content_sha256: string
}

export interface ObservationRun {
  run_id: string
  status: string
  trigger_mode: string
  trading_date: string
  universe_revision_id: string | null
  universe_freshness: string
  universe_source_url: string | null
  idempotency_key: string
  group_ids: string[]
  group_version_ids: string[]
  group_snapshots: Array<Record<string, any>>
  options?: OptionsData
  options_alignment?: PostCloseOptionAlignment | null
  options_collection?: Record<string, any>
  report?: ObservationReport | null
  group_count: number
  successful_group_count?: number
  failed_group_count?: number
  content_sha256: string
  created_at: string
  completed_at: string | null
  error_message: string | null
}

export interface ObservationOptionsResponse {
  run_id: string | null
  status: string
  trading_date: string
  cutoff_time: string | null
  available: boolean
  options: OptionsData
  alignment: PostCloseOptionAlignment | null
  message: string | null
}

export interface OptionsPlaceholder {
  is_mock: true
  status: string
  available: boolean
  provider?: null
  symbols?: []
  data_state: DataState
  note: string
}

export interface ExposureWall {
  strike: number
  exposure: number
}

export interface ExposureTotals {
  call_dex: number
  put_dex: number
  net_dex: number
  absolute_dex: number
  call_gex: number
  put_gex: number
  modeled_net_gex: number
  absolute_gex: number
}

export interface ExposureStrikeRow extends ExposureTotals {
  strike: number
  gamma_regime: 'positive' | 'negative' | 'neutral'
}

export interface GammaZone {
  sign: 'positive' | 'negative'
  start_strike: number
  end_strike: number
  strike_count: number
  total_modeled_net_gex: number
  peak_strike: number
  peak_exposure: number
}

export interface GammaFlipLevel {
  level: number
  from_sign: 'positive' | 'negative'
  to_sign: 'positive' | 'negative'
  between_strikes: [number, number]
}

export interface SpotGammaPoint {
  spot: number
  call_gex: number
  put_gex: number
  net_gex: number
}

export interface SpotGammaProfile {
  available: boolean
  points: SpotGammaPoint[]
  gamma_flip_levels: number[]
  primary_gamma_flip?: number | null
  current_spot?: number
  current_spot_net_gex?: number
  usable_iv_contracts?: number
  range_percent?: number
  point_count?: number
  time_years?: number
  risk_free_rate_percent?: number
  dividend_yield_percent?: number
  model?: string
}

export interface OptionExpirationAnalysis {
  expiration: string
  days_to_expiry: number
  contract_count: number
  max_pain: number | null
  expected_move: {
    amount: number | null
    percent: number | null
    atm_strike: number | null
  }
  spot_gamma_profile?: SpotGammaProfile
  exposure: {
    totals: ExposureTotals
    walls: Record<string, ExposureWall | null>
    by_strike: ExposureStrikeRow[]
    gamma_zones: GammaZone[]
    strike_gex_sign_changes?: GammaFlipLevel[]
    gamma_flip_levels?: GammaFlipLevel[]
    gamma_noise_threshold: number
    calculation_strike_count?: number
    display_strike_count?: number
    usable_delta_contracts: number
    usable_gamma_contracts: number
  }
}

export interface OptionSymbolAnalysis {
  symbol: string
  spot: number
  spot_time: string | null
  overview: Record<string, unknown>
  expirations: OptionExpirationAnalysis[]
}

export interface OptionsAnalysis {
  is_mock: false
  status: string
  available: boolean
  data_state: DataState
  provider: string
  source_mode: string
  captured_at: string
  requested_symbols: string[]
  unavailable_symbols: string[]
  symbols: OptionSymbolAnalysis[]
  subscription_quota: Record<string, number | null>
  model_assumptions: string[]
  warnings: string[]
  note: string
}

export type OptionsData = OptionsPlaceholder | OptionsAnalysis
