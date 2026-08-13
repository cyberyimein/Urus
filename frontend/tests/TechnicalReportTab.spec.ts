import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

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
    expect(wrapper.text()).toContain('RV20 / ATR14')

    await wrapper.find('.instrument-matrix tbody tr').trigger('click')
    expect(wrapper.text()).toContain('收益与趋势')
    expect(wrapper.text()).toContain('动量与波动')
    expect(wrapper.text()).toContain('相对强弱与位置')
    expect(wrapper.text()).toContain('布林与量价')
    expect(wrapper.text()).toContain('Beta · 20 / 60D')
  })
})
