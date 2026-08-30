import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PostCloseOptionAlignment from '@/components/PostCloseOptionAlignment.vue'
import type { PostCloseOptionAlignment as PostCloseOptionAlignmentData } from '@/types/research'

const alignment = {
  available: true,
  status: 'partial',
  source_phase: 'post_close_review',
  method: 'regular_close_vs_max_pain_and_dex_walls',
  proximity_percent: 0.6,
  price_definition: '正式对照仅使用官方 regular_price。',
  causality_note: 'DEX 影响候选不证明因果。',
  symbols: [{
    symbol: 'INTC',
    status: 'unavailable',
    close_price: null,
    close_time: null,
    price_source: null,
    price_kind: 'last_price_fallback',
    spot: 100.86,
    flags: [],
    flagged: false,
    expirations: [{
      expiration: '2026-08-28',
      max_pain: 91,
      max_pain_distance: null,
      max_pain_distance_percent: null,
      near_max_pain: false,
      dex_walls: [{
        kind: 'net_dex',
        label: 'Net DEX Wall',
        strike: 95,
        exposure: 100,
        distance: null,
        distance_percent: null,
        near: false,
      }],
      near_dex_wall: false,
      dex_influence_candidate: false,
      flags: [],
    }],
  }],
  flagged_symbols: [],
  flag_count: 0,
  unavailable_symbols: ['INTC'],
  warnings: ['INTC 缺少官方收盘价。'],
} as unknown as PostCloseOptionAlignmentData

describe('PostCloseOptionAlignment', () => {
  it('renders unavailable rows as indeterminate instead of clear', () => {
    const wrapper = mount(PostCloseOptionAlignment, {
      props: { alignment, onlySymbol: 'INTC' },
    })

    const row = wrapper.find('tbody tr')
    expect(row.attributes('data-status')).toBe('unavailable')
    expect(row.find('.post-close-alignment-unavailable').text()).toBe('无法判定')
    expect(row.find('.post-close-alignment-clear').exists()).toBe(false)
  })
})
