import { describe, expect, it } from 'vitest'

import { buildMarketClock } from '@/utils/marketClock'

describe('market clock', () => {
  it('uses Eastern time during the regular session', () => {
    const state = buildMarketClock(new Date('2026-08-13T18:00:00Z'))

    expect(state.session).toBe('regular')
    expect(state.easternTime).toBe('14:00:00')
    expect(state.countdownLabel).toBe('距离收盘')
    expect(state.targetLabel).toBe('收盘 16:00 ET')
    expect(state.progress).toBeGreaterThan(65)
    expect(state.progress).toBeLessThan(75)
  })

  it('counts down to the open before the regular session', () => {
    const state = buildMarketClock(new Date('2026-08-13T12:00:00Z'))

    expect(state.session).toBe('pre_market')
    expect(state.easternTime).toBe('08:00:00')
    expect(state.countdownLabel).toBe('距离开盘')
    expect(state.targetLabel).toBe('开盘 09:30 ET')
  })

  it('skips weekends and market holidays when finding the next open', () => {
    const weekend = buildMarketClock(new Date('2026-08-15T18:00:00Z'))
    const thanksgiving = buildMarketClock(new Date('2026-11-26T18:00:00Z'))

    expect(weekend.session).toBe('closed')
    expect(weekend.holiday).toBeNull()
    expect(weekend.targetLabel).toBe('下次开盘 09:30 ET')
    expect(thanksgiving.session).toBe('closed')
    expect(thanksgiving.holiday).toBe('感恩节')
  })
})
