import type {
  FrontendReadModel,
  HealthResponse,
  RunCreateResponse,
  RunDetail,
  RunListItem,
  RunType,
  SnapshotResponse,
  VersionResponse,
  WatchlistResponse,
} from '@/types/api'

const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000/api').replace(/\/$/, '')

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
  watchlist: () => request<WatchlistResponse>('/watchlist'),
  listRuns: () => request<RunListItem[]>('/runs'),
  getRun: (runId: string) => request<RunDetail>(`/runs/${encodeURIComponent(runId)}`),
  getSnapshot: (snapshotId: string) =>
    request<SnapshotResponse>(`/snapshots/${encodeURIComponent(snapshotId)}`),
  getFrontendReadModel: (snapshotId: string) =>
    request<FrontendReadModel>(`/snapshots/${encodeURIComponent(snapshotId)}/frontend`),
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
}
