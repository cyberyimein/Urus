import { describe, expect, it } from 'vitest'

import { formatDate, formatNumber, nullable } from '@/utils/format'

describe('display formatting', () => {
  it('renders missing fields as unavailable rather than zero', () => {
    expect(nullable(null)).toBe('不可用')
    expect(nullable(undefined)).toBe('不可用')
    expect(formatNumber(null)).toBe('不可用')
    expect(formatNumber(0)).toBe('0.00')
  })

  it('formats valid timestamps and rejects invalid values', () => {
    expect(formatDate('2026-08-02T17:27:00+00:00')).not.toBe('不可用')
    expect(formatDate('not-a-date')).toBe('不可用')
  })
})
