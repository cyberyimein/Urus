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
import type { UniverseResponse, UniverseUpdate } from '@/types/universe'
import type {
  DailyEvidenceResponse,
  DecisionChartProjection,
  DailyDecisionDataset,
  StrategyBundleResponse,
} from '@/types/dailyEvidence'

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
}
