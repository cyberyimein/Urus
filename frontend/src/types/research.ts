export type ResearchReportStatus = 'waiting_for_pair' | 'technical_ready' | 'running' | 'succeeded' | 'partial' | 'failed' | 'timed_out' | 'disabled'

export type TechnicalSection = 'overview' | 'instruments' | 'options' | 'events'

export interface ResearchReportIndex {
  report_id: string
  session_id: string | null
  workflow_run_id: string
  dataset_key: string
  cutoff_time: string
  decision_phase?: 'pre_market' | 'pre_close' | 'post_close_review' | 'current_state'
  trigger_type?: 'scheduled' | 'manual' | string
  analysis_mode?: 'official_cycle' | 'current_state' | string
  session_context?: string
  report_scope?: string[]
  official_cycle?: boolean
  eligible_for_scoring?: boolean
  updates_official_cta_state?: boolean
  trading_date?: string
  parent_report_id?: string | null
  status: ResearchReportStatus
  policy: Record<string, unknown>
  technical_report_schema_version: string | null
  decision_report_schema_version: string | null
  equity_decision_run_id: string | null
  error_code: string | null
  error_message: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string | null
  quality?: Record<string, unknown>
  run_summary?: ReportRunSummary
  resources?: {
    technical: string
    decision: string
    trace: string
  }
}

export interface ReportRunSummary {
  run_count: number
  tool_call_count: number
  prompt_tokens: number
  completion_tokens: number
  estimated_cost?: number | null
  duration_ms: number
  providers: string[]
  models: string[]
  skill_hashes: string[]
  statuses: string[]
}

export interface TechnicalReport extends Record<string, unknown> {
  schema_version?: string
  report_type?: string
  generated_at?: string
  cutoff_time?: string
  source?: Record<string, unknown>
  quality?: Record<string, unknown>
  market?: Record<string, unknown>
  instruments?: Record<string, unknown>
  options?: Record<string, unknown>
  systematic_flows?: Record<string, unknown>
  events?: Record<string, unknown>
  omissions?: Array<Record<string, unknown>>
  execution_ready?: boolean
}

export interface DecisionReport extends Record<string, unknown> {
  schema_version?: string
  report_type?: string
  status?: string
  session_id?: string
  workflow_run_id?: string
  cutoff_time?: string
  decision_phase?: 'pre_market' | 'pre_close' | 'post_close_review' | 'current_state'
  agent_profile?: string
  trading_date?: string
  parent_report_id?: string | null
  forecast_horizon?: 'regular_session' | 'final_hour' | 'completed_session' | 'current_state'
  forecast?: Record<string, unknown> | null
  review?: Record<string, unknown> | null
  objective_evaluation?: Record<string, unknown>
  equity?: Record<string, unknown>
  equity_decision_run_id?: string | null
  equity_status?: string
  market_analysis?: Record<string, unknown>
  theme_analyses?: Array<Record<string, unknown>>
  market_regime?: Record<string, unknown>
  rankings?: Array<Record<string, unknown>>
  candidate_gate?: Array<Record<string, unknown>>
  option_decisions?: Array<Record<string, unknown>>
  equity_option_context?: Array<Record<string, unknown>>
  portfolio_warnings?: string[]
  quality?: Record<string, unknown>
  warnings?: string[]
  execution_ready?: boolean
  disclaimer?: string
  generated_at?: string
}

export interface TraceNode {
  id: string
  decision_run_id: string | null
  parent_node_id: string | null
  depends_on_node_ids: string[]
  sequence: number
  lane: string
  node_type: string
  label: string
  status: string
  input_summary: Record<string, unknown>
  output_summary: Record<string, unknown>
  evidence_refs: Array<Record<string, unknown>>
  metrics: Record<string, unknown>
  error_code: string | null
  error_message: string | null
  started_at: string | null
  completed_at: string | null
}

export interface TraceEdge {
  from: string
  to: string
  kind: string
}

export interface DecisionTraceGraph {
  schema_version: string
  report_id: string
  nodes: TraceNode[]
  edges: TraceEdge[]
}

export interface TraceNodeDetail extends TraceNode {
  decision_run?: {
    id: string
    stage: string
    status: string
    provider: string
    model: string | null
    tool_call_count: number
    temperature?: number | null
    prompt_tokens?: number | null
    completion_tokens?: number | null
    estimated_cost?: number | null
    started_at?: string | null
    completed_at?: string | null
  }
  tool_calls?: Array<Record<string, unknown>>
}

export interface RawModelTurn {
  sequence: number
  response_message: Record<string, unknown>
  raw_provider_response: Record<string, unknown>
  raw_response_bytes: number
  raw_response_truncated: boolean
  prompt_tokens: number | null
  completion_tokens: number | null
  returned_reasoning_fields?: string[]
  created_at: string | null
}

export interface RawResponsePayload {
  node_id: string
  unvalidated?: boolean
  warning?: string
  model_turns: RawModelTurn[]
}

export interface ResearchReportPayload extends ResearchReportIndex {
  technical_report: TechnicalReport | null
  decision_report: DecisionReport | null
  trace_summary: {
    node_count: number
    model_run_count: number
  }
}
