import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError } from '@/api/client'

afterEach(() => vi.unstubAllGlobals())

describe('api client', () => {
  it('limits opportunity requests to the selected symbol and supports cancellation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ views: [] })))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()
    await api.getDailyOpportunityView('dataset-1', ' orcl ', controller.signal)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/daily-evidence/datasets/dataset-1/opportunity-view?symbol=ORCL',
      expect.objectContaining({ signal: controller.signal }),
    )
    const abort = new DOMException('Request cancelled', 'AbortError')
    controller.abort()
    fetchMock.mockRejectedValue(abort)
    await expect(api.getDailyOpportunityView('dataset-1', 'ORCL', controller.signal)).rejects.toBe(abort)
  })

  it('does not reuse a stale observation response', async () => {
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

  it('requests options for a selected observation date', async () => {
    const payload = {
      run_id: 'observation-1', status: 'succeeded', trading_date: '2026-08-21',
      cutoff_time: null, available: false, options: { is_mock: true, status: 'placeholder', available: false, data_state: 'placeholder', note: 'fixture' },
      alignment: null, message: null,
    }
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.getObservationOptions('2026-08-21', 'INTC')).resolves.toMatchObject(payload)
    expect(fetchMock).toHaveBeenCalledWith('/api/observation/options?trading_date=2026-08-21&symbol=INTC', expect.objectContaining({
      cache: 'no-store',
    }))
  })

  it('requests the immutable discovery projection for a run', async () => {
    const payload = { schema_version: 'urus.discovery_projection.v1', run: { run_id: 'run-1' } }
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.getObservationDiscovery('run/1')).resolves.toMatchObject(payload)
    expect(fetchMock).toHaveBeenCalledWith('/api/observation/runs/run%2F1/discovery', expect.objectContaining({
      cache: 'no-store',
    }))
  })

  it('adds and removes a focus symbol through the dedicated endpoints', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify([{ symbol: 'INTC', created_at: '2026-09-19T00:00:00Z' }]), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ symbol: 'INTC', created_at: '2026-09-19T00:00:00Z' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ symbol: 'INTC', removed: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await api.listFocusSymbols()
    await api.addFocusSymbol('INTC')
    await api.removeFocusSymbol('INTC')

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/focus-symbols', expect.objectContaining({ cache: 'no-store' }))
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/focus-symbols/INTC', expect.objectContaining({ method: 'PUT' }))
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/focus-symbols/INTC', expect.objectContaining({ method: 'DELETE' }))
  })
})
