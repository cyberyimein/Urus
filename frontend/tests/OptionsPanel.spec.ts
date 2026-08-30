import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import OptionsPanel from '@/components/OptionsPanel.vue'
import type { OptionsData } from '@/types/api'
import type { PostCloseOptionAlignment } from '@/types/research'

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
        hv_30d: 40,
        iv_hv_spread: -15,
        iv_hv_ratio: 0.625,
        iv_hv_regime: 'deep_discount',
        term_match_method: 'provider_composite_proxy',
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
          spot_gamma_profile: {
            available: true,
            points: [
              { spot: 650, call_gex: 5, put_gex: -15, net_gex: -10 },
              { spot: 680, call_gex: 12, put_gex: -12, net_gex: 0 },
              { spot: 688, call_gex: 20, put_gex: -10, net_gex: 10 },
              { spot: 720, call_gex: 15, put_gex: -5, net_gex: 10 },
            ],
            gamma_flip_levels: [680],
            primary_gamma_flip: 680,
            current_spot: 688,
            current_spot_net_gex: 10,
            usable_iv_contracts: 2,
            range_percent: 30,
            point_count: 121,
            risk_free_rate_percent: 4,
            dividend_yield_percent: 0,
          },
          exposure: {
            totals: {
              call_dex: 100,
              put_dex: -70,
              net_dex: 30,
              absolute_dex: 170,
              call_gex: 20,
              put_gex: -10,
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
            strike_gex_sign_changes: [],
            gamma_noise_threshold: 0.2,
            by_strike: [
              {
                strike: 690,
                call_dex: 100,
                put_dex: -70,
                net_dex: 30,
                absolute_dex: 170,
                call_gex: 20,
                put_gex: -10,
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

const postCloseAlignment: PostCloseOptionAlignment = {
  available: true,
  status: 'flagged',
  source_phase: 'post_close_review',
  method: 'regular_close_vs_max_pain_and_dex_walls',
  proximity_percent: 0.6,
  price_definition: '优先使用官方 regular_price；仅在缺失时回退到 last_price。',
  causality_note: '接近 DEX Wall 仅表示影响候选；DEX 是模型化敞口，价位接近不能单独证明因果。',
  symbols: [{
    symbol: 'QQQ',
    status: 'flagged',
    close_price: 688.1,
    close_time: '2026-08-03T07:00:00Z',
    price_source: 'observations.post_close_review.market.primary.regular_price',
    price_kind: 'regular_price',
    spot: 688,
    flags: ['near_max_pain', 'near_dex_wall'],
    flagged: true,
    expirations: [{
      expiration: '2026-08-03',
      max_pain: 688,
      max_pain_distance: 0.1,
      max_pain_distance_percent: 0.01,
      near_max_pain: true,
      dex_walls: [{
        kind: 'net_dex',
        label: 'Net DEX Wall',
        strike: 690,
        exposure: 30,
        distance: 1.9,
        distance_percent: 0.28,
        near: true,
      }],
      near_dex_wall: true,
      dex_influence_candidate: true,
      flags: ['near_max_pain', 'near_dex_wall'],
    }],
  }],
  flagged_symbols: ['QQQ'],
  flag_count: 1,
  unavailable_symbols: [],
  warnings: [],
}

describe('OptionsPanel', () => {
  it('renders the validation metrics and explicit model boundaries', () => {
    const wrapper = mount(OptionsPanel, { props: { options, postCloseAlignment } })

    expect(wrapper.text()).toContain('QQQ 期权总览')
    expect(wrapper.text()).toContain('Max Pain')
    expect(wrapper.text()).toContain('HV30')
    expect(wrapper.text()).toContain('显著折价')
    expect(wrapper.text()).toContain('DEX 与 Gamma 墙')
    expect(wrapper.text()).toContain('VEX / Vanna')
    expect(wrapper.text()).toContain('订阅占用 0 / 剩余 20')
    expect(wrapper.find('.horizontal-exposure-chart').exists()).toBe(true)
    expect(wrapper.findAll('.focus-row').length).toBeGreaterThan(0)
    expect(wrapper.text()).toContain('现价附近')
    expect(wrapper.text()).toContain('Call DEX Wall')
    expect(wrapper.text()).toContain('正 Gamma 区间')
    expect(wrapper.find('.strike-column.gamma-positive').exists()).toBe(true)
    expect(wrapper.text()).toContain('现价 Gamma 曲线与 Flip')
    expect(wrapper.text()).toContain('主 Gamma Flip')
    expect(wrapper.find('.spot-gamma-chart').exists()).toBe(true)
    expect(wrapper.findAll('.spot-gamma-flip')).toHaveLength(1)
    expect(wrapper.text()).toContain('收盘后期权价位回看')
    expect(wrapper.text()).toContain('收盘价接近 Max Pain')
    expect(wrapper.text()).toContain('DEX 影响候选')
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
