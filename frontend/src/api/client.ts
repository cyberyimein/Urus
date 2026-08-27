import type {
  FrontendReadModel,
  HealthResponse,
  ManualAnalysisCreateResponse,
  RunCreateResponse,
  RunDetail,
  RunListItem,
  RunProgress,
  RunType,
  SnapshotResponse,
  VersionResponse,
  WatchlistResponse,
} from '@/types/api'
import type {
  DecisionReport,
  DecisionTraceGraph,
  RawResponsePayload,
  ResearchReportIndex,
  ResearchReportPayload,
  ReportDisplayManifest,
  ReportDisplayOptionPayload,
  TechnicalReport,
  TraceNodeDetail,
} from '@/types/research'
import type { RuntimeSettingsResponse, RuntimeSettingsUpdate } from '@/types/settings'
import type {
  HistoryCollectionProjection,
  UniverseCapacityPlan,
  UniverseResponse,
  UniverseUpdate,
} from '@/types/universe'
import type {
  DailyEvidenceResponse,
  DecisionChartProjection,
  DailyDecisionDataset,
  StrategyBundleResponse,
} from '@/types/dailyEvidence'
import type {
  ObservationGroup,
  ObservationGroupDetail,
  ObservationGroupSync,
  ObservationRun,
} from '@/types/api'
import type {
  CrossSectionCatalogItem,
  CrossSectionProjection,
} from '@/types/crossSection'
import type {
  RemoteDecisionEvent,
  RemoteDecisionIntent,
  RemoteDecisionPreflight,
  RemoteDecisionRun,
  RemoteDecisionSource,
} from '@/types/remoteDecision'

// Local Vite serves `/api` through the dev proxy, which keeps browser requests
// same-origin and avoids loopback cross-port restrictions. Deployments can
// still provide an explicit absolute VITE_API_BASE_URL.
const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? '/api').replace(/\/$/, '')

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(message: string, status: number, code = 'api_error') {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...init,
      cache: init?.cache ?? 'no-store',
      headers: {
        'Content-Type': 'application/json',
        ...(init?.headers ?? {}),
      },
    })
  } catch {
    throw new ApiError('无法连接后端，请确认 FastAPI 已启动。', 0, 'network_error')
  }

  const payload = await response.json().catch(() => null) as Record<string, any> | null
  if (!response.ok) {
    const error = payload?.error
    throw new ApiError(
      typeof error?.message === 'string' ? error.message : `后端请求失败（${response.status}）`,
      response.status,
      typeof error?.code === 'string' ? error.code : 'api_error',
    )
  }
  return payload as T
}

export const api = {
  health: () => request<HealthResponse>('/health'),
  version: () => request<VersionResponse>('/version'),
  getSettings: () => request<RuntimeSettingsResponse>('/settings'),
  updateSettings: (requestBody: RuntimeSettingsUpdate) =>
    request<RuntimeSettingsResponse>('/settings', {
      method: 'PUT',
      body: JSON.stringify(requestBody),
    }),
  getUniverse: () => request<UniverseResponse>('/settings/universe'),
  createUniverseCapacityPlan: (requestBody: UniverseUpdate) =>
    request<UniverseCapacityPlan>('/settings/universe/capacity-plan', {
      method: 'POST',
      body: JSON.stringify(requestBody),
    }),
  getUniverseHistoryStatus: () =>
    request<HistoryCollectionProjection>('/settings/universe/history-status'),
  refreshUniverseHistoryCapacity: () =>
    request<HistoryCollectionProjection>('/settings/universe/history-capacity/refresh', {
      method: 'POST',
      body: JSON.stringify({}),
    }),
  updateUniverse: (requestBody: UniverseUpdate) =>
    request<UniverseResponse>('/settings/universe', {
      method: 'PUT',
      body: JSON.stringify(requestBody),
    }),
  watchlist: () => request<WatchlistResponse>('/watchlist'),
  listRuns: () => request<RunListItem[]>('/runs'),
  getRun: (runId: string) => request<RunDetail>(`/runs/${encodeURIComponent(runId)}`),
  getRunProgress: (runId: string) =>
    request<RunProgress>(`/runs/${encodeURIComponent(runId)}/progress`),
  getSnapshot: (snapshotId: string) =>
    request<SnapshotResponse>(`/snapshots/${encodeURIComponent(snapshotId)}`),
  getFrontendReadModel: (snapshotId: string) =>
    request<FrontendReadModel>(`/snapshots/${encodeURIComponent(snapshotId)}/frontend`),
  listResearchReports: (runId: string) =>
    request<ResearchReportIndex[]>(`/runs/${encodeURIComponent(runId)}/research-reports`),
  listAllResearchReports: (limit = 50) =>
    request<ResearchReportIndex[]>(`/research-reports?limit=${encodeURIComponent(String(limit))}`),
  getResearchReport: (reportId: string) =>
    request<ResearchReportPayload>(`/research-reports/${encodeURIComponent(reportId)}`),
  deleteResearchReport: (reportId: string) =>
    request<{ report_id: string; deleted: boolean }>(`/research-reports/${encodeURIComponent(reportId)}`, {
      method: 'DELETE',
    }),
  getTechnicalReport: (reportId: string) =>
    request<TechnicalReport>(`/research-reports/${encodeURIComponent(reportId)}/technical`),
  getDecisionReport: (reportId: string) =>
    request<DecisionReport>(`/research-reports/${encodeURIComponent(reportId)}/decision`),
  getReportDisplayManifest: (reportId: string) =>
    request<ReportDisplayManifest>(`/research-reports/${encodeURIComponent(reportId)}/display/manifest`),
  getReportDisplayOptions: (reportId: string, symbol: string, expiration?: string) => {
    const query = expiration ? `?expiration=${encodeURIComponent(expiration)}` : ''
    return request<ReportDisplayOptionPayload>(
      `/research-reports/${encodeURIComponent(reportId)}/display/options/${encodeURIComponent(symbol)}${query}`,
    )
  },
  getDecisionTrace: (reportId: string) =>
    request<DecisionTraceGraph>(`/research-reports/${encodeURIComponent(reportId)}/trace`),
  getTraceNode: (reportId: string, nodeId: string) =>
    request<TraceNodeDetail>(`/research-reports/${encodeURIComponent(reportId)}/trace/nodes/${encodeURIComponent(nodeId)}`),
  getTraceNodeRawResponse: (reportId: string, nodeId: string) =>
    request<RawResponsePayload>(`/research-reports/${encodeURIComponent(reportId)}/trace/nodes/${encodeURIComponent(nodeId)}/raw-response`),
  createRun: (requestBody: {
    run_type: RunType
    symbols?: string[]
    simulate_macro_event?: boolean
    simulate_instrument_event?: boolean
  }) =>
    request<RunCreateResponse>('/runs', {
      method: 'POST',
      body: JSON.stringify(requestBody),
    }),
  createManualAnalysis: (requestBody: { symbols?: string[] } = {}) =>
    request<ManualAnalysisCreateResponse>('/analysis/runs', {
      method: 'POST',
      body: JSON.stringify(requestBody),
    }),
  retryManualAnalysisAi: (runId: string) =>
    request<{ run_id: string; status: string }>(`/analysis/runs/${encodeURIComponent(runId)}/retry-ai`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),
  createDailyDataset: (requestBody: {
    scope_type: 'instrument' | 'group' | 'observation_run'
    scope_id: string
    scope_version?: number | null
    symbols: string[]
    benchmark_symbols?: string[]
    trading_date?: string
    cutoff_time?: string
  }) =>
    request<DailyEvidenceResponse>('/daily-evidence/datasets', {
      method: 'POST',
      body: JSON.stringify(requestBody),
    }),
  getDailyDataset: (datasetId: string) =>
    request<DailyDecisionDataset>(`/daily-evidence/datasets/${encodeURIComponent(datasetId)}`),
  getDailyChart: (datasetId: string) =>
    request<DecisionChartProjection>(`/daily-evidence/datasets/${encodeURIComponent(datasetId)}/chart`),
  getDailyStrategies: (datasetId: string) =>
    request<StrategyBundleResponse>(`/daily-evidence/datasets/${encodeURIComponent(datasetId)}/strategies`),
  listObservationGroups: () => request<ObservationGroup[]>('/observation/groups'),
  syncObservationGroups: () => request<ObservationGroupSync>('/observation/groups/sync', {
    method: 'POST',
    body: JSON.stringify({}),
  }),
  getObservationGroup: (groupId: string) =>
    request<ObservationGroupDetail>(`/observation/groups/${encodeURIComponent(groupId)}`),
  createObservationRun: (requestBody: {
    group_ids?: string[]
    trading_date?: string
    cutoff_time?: string
    trigger_mode?: 'manual' | 'scheduled'
    request_intent_id?: string
    universe_revision_id?: string
    universe_freshness?: string
    universe_source_url?: string
  }) => request<ObservationRun>('/observation/runs', {
    method: 'POST',
    body: JSON.stringify(requestBody),
  }),
  listObservationRuns: (limit = 30) =>
    request<ObservationRun[]>(`/observation/runs?limit=${encodeURIComponent(String(limit))}`),
  getObservationRun: (runId: string) =>
    request<ObservationRun>(`/observation/runs/${encodeURIComponent(runId)}`),
  listIndicatorCatalog: () =>
    request<CrossSectionCatalogItem[]>('/observation/indicator-catalog'),
  getIndicatorCrossSection: (runId: string, indicatorId: string) =>
    request<CrossSectionProjection>(
      `/observation/runs/${encodeURIComponent(runId)}/indicators/${encodeURIComponent(indicatorId)}`,
    ),
  listStrategyCatalog: () =>
    request<CrossSectionCatalogItem[]>('/observation/strategy-catalog'),
  getStrategyCrossSection: (runId: string, strategyId: string) =>
    request<CrossSectionProjection>(
      `/observation/runs/${encodeURIComponent(runId)}/strategies/${encodeURIComponent(strategyId)}`,
    ),
  getObservationRunGroupSnapshot: (runId: string, groupId: string) =>
    request<{ observation_run_id: string; group_id: string; snapshot_id: string; dataset_id: string; group_version_id: string; snapshot: any }>(
      `/observation/runs/${encodeURIComponent(runId)}/groups/${encodeURIComponent(groupId)}`,
    ),
  preflightRemoteDecision: (requestBody: { intent_type: RemoteDecisionIntent; source: RemoteDecisionSource }) =>
    request<RemoteDecisionPreflight>('/remote-decisions/preflight', {
      method: 'POST',
      body: JSON.stringify(requestBody),
    }),
  submitRemoteDecision: (requestBody: { intent_type: RemoteDecisionIntent; source: RemoteDecisionSource; preflight_fingerprint: string; request_intent_id: string }) =>
    request<RemoteDecisionRun>('/remote-decisions', {
      method: 'POST',
      body: JSON.stringify(requestBody),
    }),
  listRemoteDecisions: (query: { scope_type?: string; scope_id?: string; dataset_id?: string; limit?: number } = {}) => {
    const params = new URLSearchParams()
    Object.entries(query).forEach(([key, value]) => { if (value !== undefined) params.set(key, String(value)) })
    const suffix = params.toString() ? `?${params.toString()}` : ''
    return request<RemoteDecisionRun[]>(`/remote-decisions${suffix}`)
  },
  getRemoteDecision: (localRunId: string) =>
    request<RemoteDecisionRun>(`/remote-decisions/${encodeURIComponent(localRunId)}`),
  getRemoteDecisionEvents: (localRunId: string, afterSequence = 0) =>
    request<RemoteDecisionEvent[]>(`/remote-decisions/${encodeURIComponent(localRunId)}/events?after_sequence=${encodeURIComponent(String(afterSequence))}`),
  stopRemoteDecision: (localRunId: string) =>
    request<RemoteDecisionRun>(`/remote-decisions/${encodeURIComponent(localRunId)}/stop`, { method: 'POST', body: JSON.stringify({}) }),
  rerunRemoteDecision: (localRunId: string, requestIntentId?: string) =>
    request<RemoteDecisionRun>(`/remote-decisions/${encodeURIComponent(localRunId)}/rerun`, { method: 'POST', body: JSON.stringify(requestIntentId ? { request_intent_id: requestIntentId } : {}) }),
}
