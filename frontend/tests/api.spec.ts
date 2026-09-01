import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError } from '@/api/client'

afterEach(() => vi.unstubAllGlobals())

describe('api client', () => {
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
})
