export type IntradayEventStatus = 'candidate' | 'confirmed' | 'invalidated' | 'expired' | string

export interface IntradayEvent {
  id: number
  setup_id: string
  task_name: string
  symbol: string
  strategy: 'S1' | 'S2' | 'S3' | string
  strategy_id: string
  setup_type: string
  transition: string
  status: IntradayEventStatus
  current_status: IntradayEventStatus
  timeframe: string
  rule_version: string
  trading_date: string
  evidence_cutoff: string
  evaluated_at: string | null
  source_capture_id: string
  source_content_sha256: string
  reason_code: string
  message: string
  evidence: Record<string, unknown>
  confirmation_level: number | null
  invalidation_level: number | null
  expires_at: string | null
}

export interface IntradayEventsResponse {
  enabled: boolean
  status: 'disabled' | 'idle' | 'ok' | 'error' | string
  events: IntradayEvent[]
  next_after_id: number | null
  has_more: boolean
  limit: number
  error?: string
}

export interface IntradayEventHealth {
  enabled: boolean
  status: 'disabled' | 'idle' | 'ok' | 'error' | string
  cursor: {
    scheduled_at: string | null
    task_name: string | null
    capture_id: string | null
    content_sha256: string | null
  } | null
  backlog_remaining: boolean
  last_error: string | null
  last_evaluated_at: string | null
  age_seconds: number | null
  stale: boolean
  stale_after_seconds: number
  capabilities: Record<string, {
    available_count: number
    unavailable_count: number
    reasons: Record<string, number>
  }>
}

export interface IntradayFrozenLevel {
  setupId: string
  symbol: string
  tradingDate: string
  setupType: string
  currentStatus: IntradayEventStatus
  confirmationLevel: number | null
  invalidationLevel: number | null
}
