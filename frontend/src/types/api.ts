export type RunType = 'pre_market' | 'pre_close'
export type RunStatus = 'pending' | 'running' | 'succeeded' | 'mixed' | 'partial' | 'failed'
export type StepStatus = 'pending' | 'running' | 'succeeded' | 'placeholder' | 'unavailable' | 'skipped' | 'failed'
export type DataState = 'live' | 'mock' | 'mixed' | 'placeholder' | 'unavailable' | 'skipped'
export type StepCode = '1a' | '1b' | '2' | '3a' | '3b' | '4' | '5'

export interface HealthResponse {
  status: string
  environment: string
  database: string
}

export interface VersionResponse {
  app_name: string
  app_version: string
  api_schema_version: string
}

export interface WatchlistResponse {
  symbols: string[]
  option_symbols: string[]
  option_excluded_symbols: string[]
  is_development_allowlist: boolean
  is_mock: boolean
}

export interface RunListItem {
  id: string
  run_type: RunType
  status: RunStatus
  started_at: string | null
  completed_at: string | null
  cutoff_time: string
  snapshot_id: string | null
  error_message: string | null
}

export interface StepRun {
  id: string
  run_id: string
  position: number
  step_code: StepCode
  status: StepStatus
  started_at: string | null
  completed_at: string | null
  data_state?: DataState
  summary: string | null
  error_message: string | null
  payload: Record<string, unknown> | null
}

export interface RunDetail extends RunListItem {
  steps: StepRun[]
}

export interface RunCreateResponse {
  run_id: string
  status: RunStatus
  snapshot_id: string | null
}

export interface SnapshotResponse {
  id: string
  run_id: string
  schema_version: string
  cutoff_time: string
  created_at: string
  quality_status: string
  payload: Record<string, unknown>
}

export interface MarketCard {
  is_mock: boolean
  symbol: string
  label: string
  data_mode?: string
  source?: string
  quote_code?: string | null
  last_price: number | null
  change_percent: number | null
  regular_change_percent?: number | null
  previous_close?: number | null
  volume?: number | null
  quote_time?: string | null
  session?: string | null
  session_label?: string | null
  session_price_source?: string | null
  premarket_price?: number | null
  premarket_volume?: number | null
  premarket_change_percent?: number | null
  afterhours_price?: number | null
  afterhours_volume?: number | null
  afterhours_change_percent?: number | null
  trend: string | null
  session_note: string | null
  history?: HistorySummary
  market_snapshot?: MarketSnapshot
  macro_context?: MacroContext
  quality_status?: string
  quality_warnings?: string[]
  note: string
}

export interface TechnicalMetric {
  value: number | null
  unit: string
  as_of: string | null
  sample_count: number
  source: string
  window: number
  annualization_factor?: number
}

export interface BollingerMetric {
  upper: number
  middle: number
  lower: number
  current_price: number
  position_ratio: number | null
  position_percent: number | null
  unit: string
  as_of: string | null
  sample_count: number
  source: string
  window: number
  standard_deviations: number
}

export interface TechnicalIndicators {
  is_mock: boolean
  available: boolean
  quality_status: string
  source: string
  as_of: string | null
  sample_count: number
  warnings: string[]
  realized_volatility_20d?: TechnicalMetric
  atr14?: TechnicalMetric
  atr14_percent?: TechnicalMetric
  bollinger_20_2?: BollingerMetric
}

export interface HistorySummary {
  is_mock: boolean
  available: boolean
  requested_days: number
  returned_days: number
  latest_completed_bar: Record<string, unknown> | null
  returns_percent: Record<string, number | null>
  moving_average: Record<string, number | null>
  technical_indicators?: TechnicalIndicators
  reference_previous_close?: number | null
  warnings: string[]
  error?: string
}

export interface MarketSnapshot {
  is_mock: boolean
  data_mode: string
  source: string
  requested_symbols: string[]
  request_count?: number
  returned_symbols: string[]
  unavailable_symbols: string[]
  quotes: MarketSnapshotQuote[]
  vix: MarketSnapshotVix
  quality_status: string
  quality_warnings: string[]
  quality_errors: string[]
}

export interface MarketSnapshotQuote {
  symbol: string
  label: string
  quote_code: string | null
  last_price: number | null
  previous_close: number | null
  change_percent: number | null
  open_price?: number | null
  high_price?: number | null
  low_price?: number | null
  volume?: number | null
  turnover?: number | null
  bid_price?: number | null
  ask_price?: number | null
  price_spread?: number | null
  premarket_price?: number | null
  premarket_volume?: number | null
  premarket_change_percent?: number | null
  afterhours_price?: number | null
  afterhours_volume?: number | null
  afterhours_change_percent?: number | null
  quote_time: string | null
}

export interface MarketSnapshotVix {
  is_mock: boolean
  available: boolean
  status?: string
  symbol: string
  quote_code: string | null
  source: string
  reason?: string
  last_price?: number | null
  previous_close?: number | null
  change_percent?: number | null
  quote_time?: string | null
}

export interface MacroContext {
  is_mock: boolean
  data_mode: string
  source: string
  market_date?: string
  collected_at?: string
  observations: Record<string, MacroObservation>
  derived: Record<string, MacroObservation>
  cross_checks?: Record<string, MacroObservation>
  yahoo?: {
    required: boolean
    vix_available: boolean
    preferred_keys?: string[]
    selected_keys?: string[]
    source: string
    quality_status: string
    observations: string[]
  }
  quality_status: string
  quality_warnings: string[]
  quality_errors: string[]
}

export interface MacroObservation {
  series_id?: string
  label: string
  unit: string
  value: number
  as_of: string
  source: string
}

export interface InstrumentCard {
  is_mock: boolean
  symbol: string
  label: string
  last_price: number | null
  change_percent: number | null
  trend: string | null
  technical_note: string | null
  data_state?: DataState
  note: string
}

export interface EventSummary {
  is_mock: boolean
  category: string
  status: StepStatus
  title: string | null
  summary: string | null
  reason: string | null
  data_state: DataState
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
  exposure: {
    totals: ExposureTotals
    walls: Record<string, ExposureWall | null>
    by_strike: ExposureStrikeRow[]
    gamma_zones: GammaZone[]
    gamma_flip_levels: GammaFlipLevel[]
    gamma_noise_threshold: number
    usable_delta_contracts: number
    usable_gamma_contracts: number
  }
}

export interface OptionSymbolAnalysis {
  symbol: string
  spot: number
  spot_time: string | null
  overview: Record<string, number | null>
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

export interface DecisionPlaceholder {
  is_mock: boolean
  status: string
  stance: string | null
  confidence: number | null
  summary: string
  data_state: DataState
  note: string
}

export interface ReadModelStep {
  code: StepCode
  label: string
  status: StepStatus
  data_state: DataState
  summary: string | null
  error_message: string | null
}

export interface DataQuality {
  is_mock: boolean
  data_state: DataState
  status: string
  message: string
  warnings: string[]
  errors: string[]
}

export interface FrontendReadModel {
  schema_version: string
  data_mode: string
  run_id: string
  snapshot_id: string
  run_type: RunType
  run_status: RunStatus
  cutoff_time: string
  generated_at: string
  data_state: DataState
  is_mock: boolean
  market: MarketCard | null
  instrument: InstrumentCard | null
  macro_event: EventSummary
  options: OptionsData
  instrument_event: EventSummary
  decision: DecisionPlaceholder
  steps: ReadModelStep[]
  data_quality: DataQuality
}
