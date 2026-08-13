export type RunType = 'pre_market' | 'pre_close' | 'post_close_review' | 'manual_analysis'
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

export interface ManualAnalysisCreateResponse {
  run_id: string
  status: RunStatus
  session_context: string
  trigger_type: 'manual'
  analysis_mode: 'current_state'
  official_cycle: false
  eligible_for_scoring: false
  updates_official_cta_state: false
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
  regular_price?: number | null
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

export interface MacdIndicator {
  available: boolean
  quality_status: string
  source: string
  as_of: string | null
  sample_count: number
  fast_window: number
  slow_window: number
  signal_window: number
  dif: number | null
  dea: number | null
  histogram: number | null
  previous_dif: number | null
  previous_dea: number | null
  previous_histogram: number | null
  crossover: string
  zero_axis: string
  momentum: string
  warnings: string[]
}

export interface VolumeEffortResult {
  available: boolean
  quality_status: string
  source: string
  as_of: string | null
  sample_count: number
  latest_volume: number | null
  volume_sma_20: number | null
  volume_ratio_20d: number | null
  return_1d_percent: number | null
  true_range: number | null
  range_atr_ratio: number | null
  close_location_ratio: number | null
  effort: string
  result_direction: string
  combination: string
  signal: string
  signal_strength: string
  thresholds: Record<string, number>
  warnings: string[]
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
  bollinger_20_1?: BollingerMetric
  bollinger_20_2?: BollingerMetric
  bollinger_20_3?: BollingerMetric
  bollinger_bandwidth_20?: TechnicalMetric
  macd_12_26_9?: MacdIndicator
  volume_effort_result?: VolumeEffortResult
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
  regular_price?: number | null
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
  status?: string
  available?: boolean
  provider?: string
  source_mode?: string
  captured_at?: string | null
  asset_type?: string
  theme?: string
  themes?: string[]
  requested_symbols?: string[]
  unavailable_symbols?: string[]
  quota_audit?: Record<string, unknown>
  data_mode?: string
  source?: string
  quote_code?: string | null
  last_price: number | null
  regular_price?: number | null
  change_percent: number | null
  regular_change_percent?: number | null
  previous_close?: number | null
  volume?: number | null
  turnover?: number | null
  turnover_rate?: number | null
  bid_price?: number | null
  ask_price?: number | null
  price_spread?: number | null
  quote_time?: string | null
  session?: string | null
  session_label?: string | null
  premarket_price?: number | null
  premarket_volume?: number | null
  afterhours_price?: number | null
  afterhours_volume?: number | null
  history?: HistorySummary
  relative_strength?: Record<string, unknown>
  trend: string | null
  technical_note: string | null
  data_state?: DataState
  quality_status?: string
  quality_warnings?: string[]
  note: string
}

export interface EventSummary {
  is_mock: boolean
  schema_version?: string | null
  category: string
  status: StepStatus
  mode?: string
  variant?: 'events' | 'cta' | string
  scope?: string | null
  agent?: string | null
  schedule_step?: EventWorkflowPhase
  result_step?: EventWorkflowPhase
  schedule_api_called?: boolean
  result_api_call_count?: number
  missing_future_definitions?: string[]
  missing_future_targets?: EventScheduleTarget[]
  title: string | null
  summary: string | null
  reason: string | null
  events?: EventRecord[]
  counts?: Record<string, number>
  next_check_at?: string | null
  warnings?: string[]
  signals?: CTAProxySignal[]
  aggregate?: CTAProxyAggregate
  expected_symbols?: string[]
  missing_symbols?: string[]
  quality_status?: string | null
  market_reaction_count?: number
  data_state: DataState
}

export interface CTAProxySignal {
  schema_version: string
  symbol: string
  proxy_for: string
  source: string
  source_mode: 'etf_proxy' | string
  as_of: string | null
  sample_count: number
  available: boolean
  quality_status: string
  forecast_volatility?: number
  raw_signal?: number
  target_exposure?: number
  previous_target_exposure?: number
  exposure_change?: number
  pressure_index?: number
  direction?: 'long' | 'short' | 'neutral' | string
  pressure_direction?: 'buying' | 'selling' | 'stable' | string
  components?: Record<string, unknown>
  warnings: string[]
}

export interface CTAProxyAggregate {
  available?: boolean
  signal_count?: number
  average_target_exposure?: number | null
  average_pressure_index?: number | null
  classification?: string
  pressure_classification?: string
}

export interface EventWorkflowPhase {
  operation: 'discover_schedule' | 'collect_result'
  status: StepStatus
  summary: string
  data_state?: DataState
  error_message?: string | null
  api_called?: boolean
  api_call_count?: number
  discovered_count?: number
  due_count?: number
  completed_count?: number
  missing_future_definitions?: string[]
  missing_future_targets?: EventScheduleTarget[]
  errors?: string[]
  warnings?: string[]
}

export interface EventScheduleTarget {
  definition_key: string
  subject_type: string
  subject: string
}

export interface EventRecord {
  id: string
  event_key: string
  definition_key: string
  category: string
  subject_type: string
  subject: string
  event_type: string
  title: string
  period: string | null
  status: string
  discovery_mode: string
  scheduled_at: string | null
  result_expected_at: string | null
  result_available_at: string | null
  next_check_at: string | null
  confidence: number | null
  result: {
    version: number
    status: string
    released_at: string | null
    captured_at: string | null
    facts: Array<Record<string, unknown>>
    summary: string | null
    guidance: string | null
    confidence: number | null
    needs_follow_up: boolean
    next_check_at: string | null
    source_count: number
  } | null
  sources: Array<{ publisher: string; url: string; source_type: string; published_at: string | null; is_primary: boolean }>
  market_reactions?: Array<Record<string, unknown>>
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

export interface DecisionPlaceholder {
  is_mock: true
  status: string
  stance: string | null
  confidence: number | null
  summary: string
  data_state: DataState
  availability_status?: string | null
  dataset_key?: string | null
  source_run_ids?: string[]
  source_snapshot_ids?: string[]
  note: string
}

export interface DecisionAnalysis {
  is_mock: false
  status: string
  data_state: DataState
  provider: string
  model?: string | null
  skill_name?: string | null
  skill_hash?: string | null
  tool_call_count: number
  decision_session_id?: string | null
  availability_status?: string | null
  dataset_key?: string | null
  source_run_ids?: string[]
  source_snapshot_ids?: string[]
  pair_status?: string | null
  reason?: string | null
  technical_report: Record<string, unknown>
  decision_report: Record<string, unknown>
  decision: Record<string, unknown>
  note: string
}

export type DecisionData = DecisionPlaceholder | DecisionAnalysis

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
  trigger_type?: string
  analysis_mode?: string
  session_context?: string
  official_cycle?: boolean
  eligible_for_scoring?: boolean
  updates_official_cta_state?: boolean
  run_status: RunStatus
  cutoff_time: string
  generated_at: string
  data_state: DataState
  is_mock: boolean
  market: MarketCard | null
  instrument: InstrumentCard | null
  instrument_cards?: InstrumentCard[]
  systematic_flows?: Record<string, unknown>
  macro_event: EventSummary
  options: OptionsData
  instrument_event: EventSummary
  decision: DecisionData
  technical_report?: Record<string, unknown>
  steps: ReadModelStep[]
  data_quality: DataQuality
}
