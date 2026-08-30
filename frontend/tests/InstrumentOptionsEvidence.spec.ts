import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import InstrumentOptionsEvidence from '@/components/InstrumentOptionsEvidence.vue'
import type { OptionsData } from '@/types/api'

const options: OptionsData = {
  is_mock: false,
  status: 'available',
  available: true,
  data_state: 'live',
  provider: 'test-provider',
  source_mode: 'snapshot',
  captured_at: '2026-08-05T05:30:00Z',
  requested_symbols: ['INTC'],
  unavailable_symbols: [],
  symbols: [{
    symbol: 'INTC',
    spot: 100.86,
    spot_time: '2026-08-05T05:30:00Z',
    overview: {},
    expirations: [{
      expiration: '2026-08-05',
      days_to_expiry: 1,
      contract_count: 6,
      max_pain: 91,
      expected_move: { amount: 5.24, percent: 5.2, atm_strike: 100 },
      exposure: {
        totals: {
          call_dex: 56_891_700,
          put_dex: -3_217_800,
          net_dex: 26_300_000,
          absolute_dex: 60_109_500,
          call_gex: 0,
          put_gex: 0,
          modeled_net_gex: 22_634_500,
          absolute_gex: 22_634_500,
        },
        walls: {
          call_dex: { strike: 95, exposure: 56_891_700 },
          put_dex: { strike: 90, exposure: -3_217_800 },
          net_dex: { strike: 95, exposure: 56_666_000 },
        },
        by_strike: [],
        gamma_zones: [],
        gamma_noise_threshold: 0,
        usable_delta_contracts: 6,
        usable_gamma_contracts: 6,
      },
    }],
  }],
  subscription_quota: {},
  model_assumptions: [],
  warnings: [],
  note: '',
}

describe('InstrumentOptionsEvidence', () => {
  it('labels option spot fallback honestly and exposes expected-move bounds', () => {
    const wrapper = mount(InstrumentOptionsEvidence, {
      props: { options, alignment: null, symbol: 'INTC' },
    })

    const priceCard = wrapper.find('.instrument-options-decision-price')
    expect(priceCard.find('span').text()).toBe('OPTION SPOT')
    expect(priceCard.find('small').text()).toContain('官方收盘价缺失')
    expect(wrapper.find('.instrument-options-alignment-status').text()).toBe('无回看结果')
    expect(wrapper.findAll('.instrument-options-decision-detail')[0].find('small').text()).toBe('OPTION SPOT 距离 5.81%')
    expect(wrapper.find('.instrument-options-level-map-head').text()).toContain('option spot reference · close unavailable')
    expect(wrapper.find('.instrument-options-expected-band-title').text()).toBe('EXPECTED MOVE · 95.62 — 106.10')
    expect(wrapper.find('.instrument-options-level-legend').text()).toContain('OPTION SPOT 参考价')
    expect(wrapper.find('.instrument-options-support strong').text()).toBe('95.62 — 106.10')
    expect(wrapper.findAll('.instrument-options-level-axis span')).toHaveLength(2)
  })
})
