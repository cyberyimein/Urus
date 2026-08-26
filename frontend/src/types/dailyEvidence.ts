export type DailyQualityStatus = 'ok' | 'partial' | 'stale' | 'missing' | 'conflicted' | string

export interface DailyBar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  turnover?: number | null
  turnover_rate?: number | null
  adjustment?: string
}

export interface ChartPoint {
  time: string
  value: number | null
}

export interface ChartSeries {
  series_id: string
  pane: 'price' | 'volume' | 'momentum' | 'volatility' | 'relative_strength' | string
  kind: 'line' | 'bar' | 'histogram' | string
  unit: string
  points: ChartPoint[]
  bounds?: {
    min?: number
    max?: number
    reference_lines?: number[]
  }
  benchmark?: string
  reference_value?: number
}

export interface DailyInstrumentQuality {
  status: DailyQualityStatus
  bar_count: number
  latest_bar_date: string | null
  input_bar_hash: string | null
  warnings: string[]
  is_benchmark?: boolean
}

export interface DailyDatasetQuality {
  status: DailyQualityStatus
  symbols: Record<string, DailyInstrumentQuality>
  requested_symbol_count: number
  available_symbol_count: number
  errors: string[]
  warnings: string[]
  collection?: {
    status: string
    requested_symbols: string[]
    fetched_symbols: string[]
    warnings: string[]
  }
}

export interface DailyBarManifest {
  symbol: string
  bar_count: number
  start_date: string | null
  end_date: string | null
  input_bar_hash: string | null
  source: string | null
  adjustment: string | null
  exchange: string | null
  source_revisions: string[]
  quality_status: DailyQualityStatus
}

export interface DailyDecisionDataset {
  schema_version: string
  feature_version: string
  dataset_id: string
  trading_date: string
  cutoff_time: string
  market_timezone: string
  bar_completion_policy: string
  scope: {
    scope_type: 'instrument' | 'group' | 'observation_run' | string
    scope_id: string
    scope_version?: number | null
    symbols: string[]
    benchmark_symbols: string[]
    trading_date: string
  }
  bar_manifest: DailyBarManifest[]
  indicator_snapshot_ids: string[]
  group_snapshot_ids: string[]
  quality: DailyDatasetQuality
  status: DailyQualityStatus
  content_sha256: string
}

export interface ChartInstrument {
  symbol: string
  price: {
    symbol: string
    price_format?: { precision?: number; currency?: string }
    bars: DailyBar[]
  }
  series: ChartSeries[]
  indicator_snapshot_id: string | null
  quality: DailyInstrumentQuality
}

export interface DecisionChartProjection {
  schema_version: string
  dataset_id: string
  scope: DailyDecisionDataset['scope']
  timezone: string
  instruments: Record<string, ChartInstrument>
  price?: ChartInstrument['price']
  series?: ChartSeries[]
  indicator_snapshot_id?: string | null
  overlays: Array<Record<string, unknown>>
  state_segments: Array<Record<string, unknown>>
  events: Array<Record<string, unknown>>
  quality: DailyDatasetQuality
  content_sha256?: string
}

export interface DailyEvidenceResponse {
  dataset: DailyDecisionDataset
  chart: DecisionChartProjection
  strategy_decisions: StrategyDecision[]
  deterministic_synthesis: DeterministicSynthesis
}

export type StrategyStance = 'bullish' | 'bearish' | 'neutral' | 'insufficient_data' | string
export type StrategyAction = 'prioritize' | 'watch' | 'wait' | 'avoid' | 'no_action' | string

export interface StrategyDecision {
  schema_version: string
  decision_id: string
  dataset_id: string
  scope: {
    scope_type: string
    scope_id: string
    scope_version?: number | null
    symbol: string
  }
  strategy: {
    name: string
    version: string
    implementation_sha256: string
  }
  status: 'ok' | 'not_applicable' | 'error' | string
  stance: StrategyStance
  action: StrategyAction
  horizon: { unit: string; value: number }
  score: number | null
  score_scale: [number, number]
  confidence: number | null
  confidence_type: string
  setup_progress: {
    stage: string
    stage_since: string | null
    confirmation_distance_atr: number | null
    invalidation_distance_atr: number | null
    bars_in_stage: number
    changed_from_previous_stage: boolean | null
  }
  reasons: Array<{ code: string; detail: string }>
  risks: string[]
  confirmation_conditions: string[]
  invalidation_conditions: string[]
  visual_anchors: Array<Record<string, unknown>>
  evidence_refs: Array<Record<string, unknown>>
  quality: DailyInstrumentQuality
  generated_at: string
  content_sha256: string
  strategy_set_sha256?: string
}

export interface DeterministicSynthesis {
  schema_version?: string
  dataset_id?: string
  scope?: DailyDecisionDataset['scope']
  strategy_set_sha256?: string
  consensus_state?: string
  bullish_count?: number
  bearish_count?: number
  neutral_count?: number
  not_applicable_count?: number
  error_count?: number
  strongest_supporting_strategy_ids?: string[]
  strongest_conflicting_strategy_ids?: string[]
  suggested_action?: StrategyAction
  conflict_summary?: string
  strategy_set?: Array<Record<string, string>>
  generated_at?: string
  content_sha256?: string
}

export interface StrategyBundleResponse {
  dataset_id: string
  strategy_decisions: StrategyDecision[]
  deterministic_synthesis: DeterministicSynthesis
}
