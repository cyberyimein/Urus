import { describe, expect, it } from 'vitest'

import { dataStateLabel, formatDate, formatNumber, nullable } from '@/utils/format'

describe('display formatting', () => {
  it('renders missing fields as unavailable rather than zero', () => {
    expect(nullable(null)).toBe('不可用')
    expect(nullable(undefined)).toBe('不可用')
    expect(formatNumber(null)).toBe('不可用')
    expect(formatNumber(0)).toBe('0.00')
  })

  it('parses API timestamps as UTC and renders an explicit JST label', () => {
    expect(formatDate('2026-08-02T17:27:00+00:00')).toContain('08/03 02:27 JST')
    expect(formatDate('2026-08-02T17:27:00')).toContain('08/03 02:27 JST')
    expect(dataStateLabel('live')).toBe('真实数据')
    expect(dataStateLabel('placeholder')).toBe('占位数据')
  })
})
