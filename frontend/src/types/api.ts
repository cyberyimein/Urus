export type RunType = 'pre_market' | 'pre_close'
export type RunStatus = 'pending' | 'running' | 'succeeded' | 'partial' | 'mixed' | 'failed'
export type StepStatus = 'pending' | 'running' | 'succeeded' | 'skipped' | 'failed'
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
  last_price: number | null
  change_percent: number | null
  trend: string | null
  session_note: string | null
  note: string
}

export interface InstrumentCard {
  is_mock: boolean
  symbol: string
  label: string
  last_price: number | null
  change_percent: number | null
  trend: string | null
  technical_note: string | null
  note: string
}

export interface EventSummary {
  is_mock: boolean
  category: string
  status: StepStatus
  title: string | null
  summary: string | null
  reason: string | null
}

export interface OptionsPlaceholder {
  is_mock: true
  status: string
  available: boolean
  provider?: null
  symbols?: []
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
  provider: string
  source_mode: string
  captured_at: string
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
  note: string
}

export interface ReadModelStep {
  code: StepCode
  label: string
  status: StepStatus
  summary: string | null
  error_message: string | null
}

export interface DataQuality {
  is_mock: boolean
  status: string
  message: string
  warnings: string[]
  errors: string[]
}

export interface FrontendReadModel {
  schema_version: string
  run_id: string
  snapshot_id: string
  run_type: RunType
  run_status: RunStatus
  cutoff_time: string
  generated_at: string
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
