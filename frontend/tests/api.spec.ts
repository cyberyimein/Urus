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
      'http://127.0.0.1:8000/api/health',
      expect.objectContaining({ cache: 'no-store' }),
    )
  })

  it('turns an unavailable backend into a readable error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
    await expect(api.health()).rejects.toMatchObject<ApiError>({ code: 'network_error' })
  })
})
