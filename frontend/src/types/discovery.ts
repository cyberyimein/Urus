export interface DiscoveryEventGroup {
  id: string
  name: string
}

export interface DiscoveryValue {
  value: number | null
  display_value?: string | null
  state: string | null
  state_label?: string | null
}

interface DiscoveryEvidencePoint {
  run_id: string | null
  trading_date: string | null
  snapshot_ids: Array<string | null>
  dataset_ids: Array<string | null>
}

export interface IndicatorDiscoveryRoute {
  name: 'instrument-decision'
  params: { symbol: string }
  query: {
    dataset?: string
    run: string
    discoveryEvent: string
    lens: string
  }
}

export interface GroupDiscoveryRoute {
  name: 'group-observation'
  params: { groupId: string }
  query: {
    run: string
    discoveryEvent: string
  }
}

export interface DiscoveryDuration {
  status: 'unavailable'
  label: string
}

interface DiscoveryEventBase {
  event_id: string
  groups: DiscoveryEventGroup[]
  current: DiscoveryEvidencePoint
  compare: DiscoveryEvidencePoint
  before: DiscoveryValue
  after: DiscoveryValue
  transition: Record<string, unknown>
}

export interface IndicatorTransitionEvent extends DiscoveryEventBase {
  kind: 'indicator_transition'
  symbol: string
  lens: {
    id: string
    name: string
    version?: string | null
  }
  priority_reason: string
  route: IndicatorDiscoveryRoute
}

export interface GroupPhaseMetrics {
  participation: number | null
  breadth_ma20: number | null
  breadth_ma20_delta: number | null
  relative_20d: number | null
  relative_20d_delta: number | null
  leader_concentration: number | null
  valid_symbol_count: number | null
  requested_symbol_count: number | null
  coverage: number | null
  leaders_added: string[]
  leaders_removed: string[]
}

export interface GroupPhaseChangeEvent extends DiscoveryEventBase {
  kind: 'group_phase_change'
  group: DiscoveryEventGroup
  stage: {
    id: string
    label: string
  }
  metrics: GroupPhaseMetrics
  duration: DiscoveryDuration
  priority_reason: string
  route: GroupDiscoveryRoute
}

export type DiscoveryEvent = IndicatorTransitionEvent | GroupPhaseChangeEvent

export interface DiscoveryProjection {
  schema_version: string
  run: {
    run_id: string
    status: string
    trading_date: string
  }
  comparison: {
    mode: string
    status: 'ok' | 'partial' | 'unavailable' | string
    current_trading_date: string
    previous_trading_date: string | null
    previous_trading_dates: string[]
    compare_run_id: string | null
  }
  events: DiscoveryEvent[]
  quality: {
    status: string
    warnings: string[]
    capabilities: {
      indicator_transition: string
      strategy_transition: string
      group_phase_change: string
    }
  }
  content_sha256: string
}
