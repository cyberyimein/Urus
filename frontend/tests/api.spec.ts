import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError } from '@/api/client'

afterEach(() => vi.unstubAllGlobals())

describe('api client', () => {
  it('turns an unavailable backend into a readable error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
    await expect(api.health()).rejects.toMatchObject<ApiError>({ code: 'network_error' })
  })
})

