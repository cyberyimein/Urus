import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import TechnicalReportTab from '@/components/research/TechnicalReportTab.vue'
import type { TechnicalReport } from '@/types/research'

const metric = (value: number) => ({ value, available: true, unit: 'percent' })
const report: TechnicalReport = {
  instruments: {
    themes: {
      大科技: [{
        symbol: 'AAPL', label: 'Apple', quality_status: 'ok',
        quote: { last_price: 221.4, change_percent: 1.25 },
        relative_strength: {
          excess_returns_percent: { '5d': 0.9, '20d': 4.2, '60d': 7.1 },
          beta: { '20d': 1.08, '60d': 1.02 },
          correlation: { '20d': 0.87, '60d': 0.82 },
        },
        technical: {
          quality_status: 'ok',
          returns_percent: { '1d': 1.25, '5d': 2.1, '20d': 5.4, '60d': 8.2, '120d': 12.3, '252d': 21.8 },
          moving_average: { '10d': 216, '20d': 212, '50d': 205, '100d': 198, '200d': 190 },
          high_low_distance_percent: { '252d_high': -3.2, '252d_low': 35.1 },
          realized_volatility: { '10d': metric(18), '20d': metric(21), '60d': metric(24) },
          atr14: { value: 4.1 }, atr14_percent: metric(1.85),
          rsi14: { value: 63.4, change: 2.1, state: 'positive' },
          rsi_context: {
            available: true,
            zone: 'recovering_from_oversold',
            classification: 'reversal_watch',
            continuation_direction: 'down',
            continuation_score: 2,
            reversal_score: 4,
            score_scale: 8,
            signals: { bullish_divergence_20d: true, crossed_above_30: true },
            metrics: { rsi_slope_3d: 8.2, rsi_slope_5d: 11.4, overbought_days: 0, oversold_days: 0 },
            interpretation: '超卖后出现修复证据，但尚需价格结构继续确认。',
          },
          macd_12_26_9: { dif: 2.2, dea: 1.8, histogram: 0.4, momentum: 'bullish_accelerating' },
          bollinger: {
            '1_sigma': { position_percent: 84 },
            '2_sigma': { position_percent: 67, current_price: 221.4 },
            '3_sigma': { position_percent: 61 },
            bandwidth_20: metric(12.4),
          },
          volume_effort_result: { volume_ratio_20d: 1.3, range_atr_ratio: 0.9, close_location_ratio: 0.75, effort: 'normal', combination: 'normal_up', signal: 'neutral', signal_strength: 'neutral' },
        },
      }],
    },
  },
}

describe('TechnicalReportTab instruments', () => {
  it('renders the real relative-strength schema, RSI14 and grouped technical detail', async () => {
    const wrapper = mount(TechnicalReportTab, { props: { report, activeSection: 'instruments' } })

    expect(wrapper.text()).toContain('RS20 vs QQQ')
    expect(wrapper.text()).toContain('4.20%')
    expect(wrapper.text()).toContain('63.4')
    expect(wrapper.text()).toContain('反转观察')
    expect(wrapper.text()).toContain('RV20 / ATR14')

    await wrapper.find('.instrument-matrix tbody tr').trigger('click')
    expect(wrapper.text()).toContain('收益与趋势')
    expect(wrapper.text()).toContain('动量与波动')
    expect(wrapper.text()).toContain('相对强弱与位置')
    expect(wrapper.text()).toContain('布林与量价')
    expect(wrapper.text()).toContain('Beta · 20 / 60D')
    expect(wrapper.text()).toContain('RSI 复合判断')
    expect(wrapper.text()).toContain('下跌延续分')
    expect(wrapper.text()).toContain('反转修复分')
    expect(wrapper.text()).toContain('20日底背离')
    expect(wrapper.text()).toContain('程序生成的动量上下文')
  })

  afterEach(() => vi.restoreAllMocks())

  it('loads complete option charts from the display projection instead of the AI packet', async () => {
    const optionReport: TechnicalReport = {
      ...report,
      options: {
        current_phase: 'pre_market',
        pre_market: {
          symbols: [{
            symbol: 'QQQ',
            spot: 100,
            overview: { iv: 25, hv_30d: 26 },
            expirations: [{
              expiration: '2026-08-21',
              days_to_expiry: 8,
              max_pain: 100,
              expected_move: { amount: 5 },
              exposure: { totals: { call_dex: 10, put_dex: -5, modeled_net_gex: 20 }, walls: {} },
              spot_gamma_profile: { primary_gamma_flip: 98, current_spot_net_gex: 20 },
            }],
          }],
        },
      },
    }
    vi.spyOn(api, 'getReportDisplayManifest').mockResolvedValue({
      report_id: 'report-1',
      schema_version: 'urus.report_display_projection.v1',
      available: true,
      endpoint: '/api/research-reports/report-1/display',
      source_snapshot_ids: ['snapshot-1'],
    })
    vi.spyOn(api, 'getReportDisplayOptions').mockResolvedValue({
      schema_version: 'urus.report_display_projection.v1',
      report_id: 'report-1',
      symbol: 'QQQ',
      spot: 100,
      as_of: '2026-08-13T10:00:00Z',
      expiration: '2026-08-21',
      data: {
        strike_structure: {
          rows: [
            { strike: 99, net_dex: -10, net_gex: -20, gamma_regime: 'negative' },
            { strike: 100, net_dex: 20, net_gex: 30, gamma_regime: 'positive' },
          ],
        },
        gamma_profile: {
          points: [
            { spot: 90, net_gex: -10 },
            { spot: 95, net_gex: 0 },
            { spot: 98, net_gex: 10 },
            { spot: 100, net_gex: 20 },
          ],
          flips: [
            { spot: 95, direction: 'negative_to_positive', is_primary: false },
            { spot: 98, direction: 'negative_to_positive', is_primary: true },
          ],
          primary_gamma_flip: 98,
        },
      },
      source: {},
      chart_specs: [],
      data_quality: { source_available: true },
    })

    const wrapper = mount(TechnicalReportTab, {
      props: { report: optionReport, reportId: 'report-1', activeSection: 'options' },
    })
    await flushPromises()
    expect(api.getReportDisplayOptions).toHaveBeenCalledWith('report-1', 'QQQ', '2026-08-21')
    expect(wrapper.text()).toContain('2 strikes · 完整展示投影')
    expect(wrapper.text()).toContain('4 points · 完整展示投影')
    expect(wrapper.findAll('.option-strike-table tbody tr')).toHaveLength(2)
    expect(wrapper.find('.profile-spot-line').attributes('x1')).toBe('100')
    expect(wrapper.find('.profile-flip-line').attributes('x1')).toBe('80')
    expect(wrapper.findAll('.profile-zero-point')).toHaveLength(2)
    expect(wrapper.find('.profile-zero-point:not(.primary)').attributes('cx')).toBe('50')
    expect(wrapper.text()).toContain('现价 100')
    expect(wrapper.text()).toContain('0 GEX 95')
    expect(wrapper.text()).toContain('Gamma Flip 98')
  })
})
