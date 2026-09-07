/**
 * Read-only daily opportunity view returned for an already frozen dataset.
 *
 * The view is intentionally separate from the daily decision dataset and
 * research outcome contracts.  Its prices describe visible evidence at the
 * requested view time; they do not describe an order or a realised return.
 */

export type OpportunityViewStatus =
  | 'unavailable'
  | 'stale'
  | 'invalidated'
  | 'expired'
  | 'preparing'
  | 'triggered_unverified'
  | 'in_entry_zone'
  | 'extended'
  | 'waiting'
  | string

export type OpportunityViewReferenceKind = 'daily_close' | 'intraday_quote' | 'none'
export type OpportunityViewQualityStatus = 'ok' | 'partial' | 'unavailable' | 'unknown' | string
export type OpportunityViewFreshness = 'fresh' | 'stale' | 'unknown' | string
export type OpportunityViewResearchSupport = 'unknown' | 'research_only' | 'supported' | string

export interface OpportunityViewEventRisk {
  status: 'clear' | 'near_term' | 'holding_window' | 'unknown' | 'not_applicable' | string
  codes: string[]
  known_events: string[]
  horizon_sessions: number | null
}

export interface OpportunityViewQuote {
  quote_id: string
  symbol: string
  source_mode: 'observed_pit' | 'historical_reconstruction' | 'daily_execution_proxy' | string
  data_source: 'real' | 'synthetic' | 'unknown' | string
  price_basis: 'raw' | 'adjusted' | string
  price_mapping_id: string | null
  timestamp: string
  received_at: string
  bid: number
  ask: number
  quality: 'ok' | 'partial' | 'unknown' | string
  source: string | null
  source_revision: string | null
}

export interface OpportunityViewEvidenceRef {
  [key: string]: unknown
}

export interface OpportunityView {
  schema_version: string
  view_id: string
  dataset_id: string | null
  scan_id: string
  setup_id: string
  trigger_id: string | null
  symbol: string
  family_version: string
  rules_hash: string
  viewed_at: string
  knowledge_at: string
  market_as_of: string | null
  source_mode: 'observed_pit' | 'historical_reconstruction' | 'daily_execution_proxy' | string
  data_source: 'real' | 'synthetic' | 'unknown' | string
  price_basis: 'raw' | 'adjusted' | string
  price_mapping_id: string | null
  quality: OpportunityViewQualityStatus
  quality_warnings: string[]
  freshness: OpportunityViewFreshness
  status: OpportunityViewStatus
  /** Calendar dates for the setup lifecycle, when supplied by the view API. */
  setup_observed_at?: string | null
  setup_valid_from?: string | null
  setup_invalidated_at?: string | null
  upper_trigger: number
  invalidation_stop: number
  atr: number
  upper_limit: number
  reference_price: number | null
  reference_kind: OpportunityViewReferenceKind
  reference_quote: OpportunityViewQuote | null
  distance_to_trigger_atr: number | null
  condition: string
  invalidation: string
  event_risk: OpportunityViewEventRisk
  research_support: OpportunityViewResearchSupport
  reason_codes: string[]
  evidence_refs: OpportunityViewEvidenceRef[]
  content_sha256: string
}

export interface OpportunityViewScan {
  scan_id: string
  status: string
  rejection_codes: string[]
}

export interface OpportunityViewReference {
  price: number | null
  kind: 'daily_close' | 'none'
  market_as_of: string | null
}

export interface OpportunityViewQuality {
  status: OpportunityViewQualityStatus
  warnings: string[]
}

export interface OpportunityViewRow {
  symbol: string
  status: OpportunityViewStatus
  /** Setup lifecycle dates are duplicated at row level for unavailable views. */
  setup_observed_at?: string | null
  setup_valid_from?: string | null
  setup_invalidated_at?: string | null
  plan_state?: 'current' | 'historical' | 'unavailable' | string
  scan: OpportunityViewScan
  opportunity: OpportunityView | null
  reference: OpportunityViewReference
  quality: OpportunityViewQuality
  reason_codes: string[]
}

export interface DailyOpportunityViewResponse {
  schema_version: string
  dataset_id: string
  viewed_at: string
  knowledge_at: string | null
  views: OpportunityViewRow[]
  warnings: string[]
}
