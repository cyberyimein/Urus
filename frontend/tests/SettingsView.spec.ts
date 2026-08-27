import { describe, expect, it } from 'vitest'

import { router } from '@/router'

describe('retired runtime settings route', () => {
  it('redirects the removed legacy AI/report settings page to the active Universe settings', async () => {
    await router.push('/settings')

    expect(router.currentRoute.value.name).toBe('universe-settings')
    expect(router.currentRoute.value.path).toBe('/settings/universe')
  })
})
