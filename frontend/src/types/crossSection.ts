export type CrossSectionLensType = 'indicator' | 'strategy'

export interface CrossSectionCatalogItem {
  id: string
  name: string
  kind: CrossSectionLensType
  description: string
  version: string | null
  feature_version?: string | null
  implementation_sha256?: string
  unit: string | null
  source_path: string | null
  thresholds: Record<string, number>
  content_sha256: string
}

export interface CrossSectionTransition {
  id: string
  group_id: string
  group_name: string
  symbol: string
  state: string
  state_label: string
  value: number | null
  change: number | null
  transition: { type: string; from: string; to: string } | null
  snapshot_id: string
  dataset_id: string
  previous_trading_date?: string | null
}

export interface CrossSectionComparison {
  mode: 'previous_trading_session' | string
  status: 'ok' | 'partial' | 'unavailable' | string
  current_trading_date: string
  previous_trading_date: string | null
  previous_trading_dates: string[]
  available_group_count: number
  group_count: number
  previous_snapshot_ids: string[]
  previous_dataset_ids: string[]
}

export interface CrossSectionRow {
  id: string
  group_id: string
  group_name: string
  group_version_id: string
  snapshot_id: string
  dataset_id: string
  symbol: string
  valid: boolean
  status: string
  quality_status: string
  value: number | null
  previous_value: number | null
  change: number | null
  display_value: string
  previous_display_value?: string | null
  state: string
  state_label: string
  previous_state?: string | null
  previous_state_label?: string | null
  previous_trading_date?: string | null
  previous_snapshot_id?: string | null
  previous_dataset_id?: string | null
  threshold_distance?: number | null
  unit?: string | null
  thresholds?: Record<string, number>
  benchmark_symbols?: string[]
  transition: { type: string; from: string; to: string } | null
  stance?: string
  action?: string
  previous_stance?: string | null
  previous_action?: string | null
  score?: number | null
  strategy_version?: string | null
  implementation_sha256?: string | null
  decision_id?: string | null
  setup_progress?: Record<string, any>
  horizon?: Record<string, any> | null
  confirmation_conditions?: string[]
  invalidation_conditions?: string[]
  decision_quality?: Record<string, any>
  reasons?: Array<Record<string, any>>
  evidence_refs: Array<Record<string, any>>
  warnings: string[]
  attention_features?: Record<string, number | boolean | string | null>
}

export interface CrossSectionGroupSummary {
  group_id: string
  display_name?: string
  group_name: string
  group_version_id: string
  group_version: number
  snapshot_id: string
  dataset_id: string
  previous_trading_date?: string | null
  previous_snapshot_id?: string | null
  previous_dataset_id?: string | null
  trading_date: string
  benchmark_symbols: string[]
  symbol_count: number
  valid_symbol_count: number
  missing_symbol_count: number
  quality_status: string
  state_counts: Record<string, number>
  stance_counts?: Record<string, number>
  distribution: {
    count: number
    median: number | null
    q1: number | null
    q3: number | null
    min: number | null
    max: number | null
  }
  previous_distribution?: {
    count: number
    median: number | null
    q1: number | null
    q3: number | null
    min: number | null
    max: number | null
  }
  distribution_median_change?: number | null
  previous_valid_symbol_count?: number
  previous_symbol_count?: number
  warnings: string[]
}

export interface CrossSectionProjection {
  schema_version: string
  scope_type: 'observation_run'
  scope_id: string
  observation_run_id: string
  trading_date: string
  cutoff_time: string
  comparison?: CrossSectionComparison
  lens: {
    type: CrossSectionLensType
    id: string
    version: string | null
    feature_version?: string | null
    implementation_sha256?: string | null
  }
  indicator?: CrossSectionCatalogItem
  strategy?: CrossSectionCatalogItem
  group_version_ids: string[]
  failed_groups: Array<Record<string, any>>
  groups: CrossSectionGroupSummary[]
  rows: CrossSectionRow[]
  transitions: CrossSectionTransition[]
  quality: {
    status: string
    run_status: string
    requested_group_count: number
    projected_group_count: number
    failed_group_count: number
    projected_row_count: number
    valid_row_count: number
    missing_row_count: number
    snapshot_ids: string[]
    dataset_ids: string[]
    warnings: string[]
  }
  ai: {
    available: boolean
    status: string
    reason: string
  }
  content_sha256: string
}
