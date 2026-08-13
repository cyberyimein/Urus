import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError } from '@/api/client'

afterEach(() => vi.unstubAllGlobals())

describe('api client', () => {
  it('does not reuse a stale dashboard response after a completed run', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: 'ok', environment: 'test', database: 'ok' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await api.health()

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/health',
      expect.objectContaining({ cache: 'no-store' }),
    )
  })

  it('turns an unavailable backend into a readable error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
    await expect(api.health()).rejects.toMatchObject<ApiError>({ code: 'network_error' })
  })

  it('queues a manual current-state analysis through the dedicated endpoint', async () => {
    const payload = {
      run_id: 'manual-1', status: 'pending', session_context: 'intraday',
      trigger_type: 'manual', analysis_mode: 'current_state', official_cycle: false,
      eligible_for_scoring: false, updates_official_cta_state: false,
    }
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.createManualAnalysis()).resolves.toMatchObject(payload)
    expect(fetchMock).toHaveBeenCalledWith('/api/analysis/runs', expect.objectContaining({
      method: 'POST', body: '{}', cache: 'no-store',
    }))
  })
})
