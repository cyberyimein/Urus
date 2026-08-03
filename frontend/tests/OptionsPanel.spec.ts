import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import OptionsPanel from '@/components/OptionsPanel.vue'
import type { OptionsData } from '@/types/api'

const options: OptionsData = {
  is_mock: false,
  status: 'available',
  available: true,
  data_state: 'live',
  provider: 'moomoo_openapi',
  source_mode: 'snapshot',
  captured_at: '2026-08-03T00:00:00Z',
  requested_symbols: ['SPY', 'QQQ', 'SMH', 'IGV', 'INTC'],
  unavailable_symbols: [],
  subscription_quota: {
    option_used_quota: 0,
    option_remain_quota: 20,
    own_option_used_quota: 0,
  },
  model_assumptions: ['DEX uses the natural Delta sign.'],
  warnings: ['OI is updated daily.'],
  note: 'snapshot analytics',
  symbols: [
    {
      symbol: 'QQQ',
      spot: 688,
      spot_time: '2026-07-31 16:00:00',
      overview: {
        iv: 25,
        iv_rank: 49,
        iv_percentile: 68,
        call_volume: 100,
        put_volume: 120,
        call_open_interest: 200,
        put_open_interest: 300,
      },
      expirations: [
        {
          expiration: '2026-08-03',
          days_to_expiry: 1,
          contract_count: 2,
          max_pain: 680,
          expected_move: { amount: 8, percent: 1.16, atm_strike: 688 },
          exposure: {
            totals: {
              call_dex: 100,
              put_dex: -70,
              net_dex: 30,
              absolute_dex: 170,
              call_gex: 20,
              put_gex: 10,
              modeled_net_gex: 10,
              absolute_gex: 30,
            },
            walls: {
              call_dex: { strike: 690, exposure: 100 },
              put_dex: { strike: 680, exposure: -70 },
              net_dex: { strike: 690, exposure: 30 },
              call_gamma: { strike: 690, exposure: 20 },
              put_gamma: { strike: 680, exposure: 10 },
              absolute_gamma: { strike: 690, exposure: 30 },
            },
            gamma_zones: [
              {
                sign: 'positive',
                start_strike: 690,
                end_strike: 690,
                strike_count: 1,
                total_modeled_net_gex: 10,
                peak_strike: 690,
                peak_exposure: 10,
              },
            ],
            gamma_flip_levels: [],
            gamma_noise_threshold: 0.2,
            by_strike: [
              {
                strike: 690,
                call_dex: 100,
                put_dex: -70,
                net_dex: 30,
                absolute_dex: 170,
                call_gex: 20,
                put_gex: 10,
                modeled_net_gex: 10,
                absolute_gex: 30,
                gamma_regime: 'positive',
              },
            ],
            usable_delta_contracts: 2,
            usable_gamma_contracts: 2,
          },
        },
      ],
    },
  ],
}

describe('OptionsPanel', () => {
  it('renders the validation metrics and explicit model boundaries', () => {
    const wrapper = mount(OptionsPanel, { props: { options } })

    expect(wrapper.text()).toContain('QQQ 期权总览')
    expect(wrapper.text()).toContain('Max Pain')
    expect(wrapper.text()).toContain('DEX 与 Gamma 墙')
    expect(wrapper.text()).toContain('VEX / Vanna')
    expect(wrapper.text()).toContain('订阅占用 0 / 剩余 20')
    expect(wrapper.find('.horizontal-exposure-chart').exists()).toBe(true)
    expect(wrapper.findAll('.focus-row').length).toBeGreaterThan(0)
    expect(wrapper.text()).toContain('现价附近')
    expect(wrapper.text()).toContain('Call DEX Wall')
    expect(wrapper.text()).toContain('正 Gamma 区间')
    expect(wrapper.find('.strike-column.gamma-positive').exists()).toBe(true)
  })

  it('renders the disabled state without pretending data is live', () => {
    const wrapper = mount(OptionsPanel, {
      props: {
        options: {
          is_mock: true,
          status: 'not_implemented',
          available: false,
          data_state: 'placeholder',
          note: 'Moomoo disabled',
        },
      },
    })

    expect(wrapper.text()).toContain('期权数据尚未采集')
    expect(wrapper.text()).toContain('Moomoo disabled')
  })
})
