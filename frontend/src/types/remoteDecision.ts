export type RemoteDecisionIntent =
  | 'instrument_arbitration'
  | 'group_arbitration'
  | 'indicator_attention'
  | 'strategy_attention'

export type RemoteDecisionStatus =
  | 'queued'
  | 'submitting'
  | 'running'
  | 'stopping'
  | 'succeeded'
  | 'failed'
  | 'stopped'
  | 'accepted'
  | 'rejected_result'

export interface RemoteDecisionSource {
  dataset_id?: string
  symbol?: string
  observation_run_id?: string
  snapshot_id?: string
  group_version_id?: string
  lens_id?: string
  lens_type?: string
  lens_version?: string
  content_sha256?: string
}

export interface RemoteDecisionIssue {
  code: string
  message: string
  details: Record<string, unknown>
}

export interface RemoteDecisionBinding {
  intent_type: RemoteDecisionIntent
  workflow_ref: string
  status: string
  definition_hash: string
  compiled_hash: string
  capability_manifest_hash?: string | null
  input_schema_version: string
  output_schema_version: string
}

export interface RemoteDecisionPreflight {
  enabled: boolean
  blockers: RemoteDecisionIssue[]
  warnings: RemoteDecisionIssue[]
  intent_type: RemoteDecisionIntent
  source: RemoteDecisionSource
  source_summary: Record<string, unknown>
  binding: RemoteDecisionBinding | null
  input_sha256: string | null
  preflight_fingerprint: string | null
}

export interface RemoteDecisionArtifactSummary {
  output_schema_version: string
  completeness: string
  artifact_sha256: string
  validation_status: string
  accepted_at: string | null
}

export interface RemoteDecisionRun {
  local_run_id: string
  anomalo_run_id: string | null
  intent_type: RemoteDecisionIntent
  request_intent_id: string
  idempotency_key: string
  scope_type: string
  scope_id: string
  scope_version: string | null
  dataset_id: string | null
  lens_type: string | null
  lens_id: string | null
  lens_version: string | null
  source: RemoteDecisionSource
  workflow_ref: string
  input_schema_version: string
  input_sha256: string
  status: RemoteDecisionStatus
  remote_status: string | null
  validation_status: string
  latest_event_sequence: number
  error_code: string | null
  safe_error_message: string | null
  result: Record<string, any> | null
  artifact: RemoteDecisionArtifactSummary | null
  created_at: string
  submitted_at: string | null
  started_at: string | null
  completed_at: string | null
}

export interface RemoteDecisionEvent {
  sequence: number
  event_type: string
  event_timestamp: string | null
  node_id: string | null
  attempt: number | null
  child_run_id: string | null
  data: Record<string, any>
  created_at: string
}
