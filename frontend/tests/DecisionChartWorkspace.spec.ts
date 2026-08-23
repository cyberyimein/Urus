import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DecisionChartWorkspace from '@/components/decision/DecisionChartWorkspace.vue'
import type { ChartSeries, DecisionChartProjection } from '@/types/dailyEvidence'

const dates = ['2026-08-19', '2026-08-20', '2026-08-21']
const bars = dates.map((date, index) => ({
  date,
  open: 99 + index,
  high: 101 + index,
  low: 98 + index,
  close: 100 + index,
  volume: 1000 + index * 100,
}))

function lineSeries(series_id: string, values: number[], pane: ChartSeries['pane'] = 'price'): ChartSeries {
  return {
    series_id,
    pane,
    kind: 'line',
    unit: 'price',
    points: dates.map((time, index) => ({ time, value: values[index] })),
  }
}

const projection = {
  schema_version: 'urus.decision_chart_projection.v1',
  dataset_id: 'dataset-1',
  scope: {
    scope_type: 'instrument',
    scope_id: 'INTC',
    symbols: ['INTC'],
    benchmark_symbols: [],
    trading_date: '2026-08-21',
  },
  timezone: 'America/New_York',
  instruments: {
    INTC: {
      symbol: 'INTC',
      price: { symbol: 'INTC', bars },
      series: [
        lineSeries('bollinger_upper_20_2', [104, 105, 106]),
        lineSeries('bollinger_middle_20', [100, 101, 102]),
        lineSeries('bollinger_lower_20_2', [96, 97, 98]),
        lineSeries('rsi12', [25, 28, 32], 'momentum'),
      ],
      indicator_snapshot_id: 'indicator-1',
      quality: { status: 'ok', bar_count: 3, latest_bar_date: '2026-08-21', input_bar_hash: 'hash', warnings: [] },
    },
  },
  overlays: [
    {
      overlay_id: 'mean-series',
      symbol: 'INTC',
      strategy_name: 'mean_reversion_v1',
      kind: 'series_highlight',
      series_ids: ['bollinger_upper_20_2', 'bollinger_middle_20', 'bollinger_lower_20_2'],
    },
    {
      overlay_id: 'breakout-line',
      symbol: 'INTC',
      strategy_name: 'breakout_volume_v1',
      kind: 'confirmation_line',
      price: 103,
      start_time: '2026-08-19',
      end_time: '2026-08-21',
    },
    {
      overlay_id: 'left-side-rsi',
      symbol: 'INTC',
      strategy_name: 'quality_left_side_reversal_v1',
      kind: 'series_highlight',
      series_ids: ['rsi12'],
    },
    {
      overlay_id: 'left-side-support',
      symbol: 'INTC',
      strategy_name: 'quality_left_side_reversal_v1',
      kind: 'price_zone',
      lower_price: 98,
      upper_price: 100,
      start_time: '2026-08-19',
      end_time: '2026-08-21',
      label: '成交密集支撑区',
      tone: 'warning',
    },
  ],
  state_segments: [],
  events: [],
  quality: {
    status: 'ok',
    symbols: {},
    requested_symbol_count: 1,
    available_symbol_count: 1,
    errors: [],
    warnings: [],
  },
} as DecisionChartProjection

const layers = {
  ma20: false,
  ma50: false,
  ma200: false,
  bollinger: false,
  volume: false,
  rsi: false,
  macd: false,
  relative: false,
}

describe('DecisionChartWorkspace strategy overlays', () => {
  it('renders indicator references as curves and price thresholds as bounded lines', async () => {
    const wrapper = mount(DecisionChartWorkspace, {
      props: {
        projection,
        symbol: 'INTC',
        layers,
        strategyFilter: 'mean_reversion_v1',
      },
    })

    expect(wrapper.findAll('path.series-strategy-highlight')).toHaveLength(3)
    const breakoutLine = wrapper.find('.strategy-overlay-layer line')
    expect(Number(breakoutLine.attributes('x1'))).toBeLessThan(Number(breakoutLine.attributes('x2')))

    await wrapper.setProps({ strategyFilter: null })
    expect(wrapper.findAll('path[class*="series-bollinger"]')).toHaveLength(0)
  })

  it('renders left-side support as a zone and selects RSI12', () => {
    const wrapper = mount(DecisionChartWorkspace, {
      props: {
        projection,
        symbol: 'INTC',
        layers,
        strategyFilter: 'quality_left_side_reversal_v1',
      },
    })

    expect(wrapper.find('.strategy-overlay-zone').exists()).toBe(true)
    expect(wrapper.find('.rsi-line.series-strategy-highlight').exists()).toBe(true)
    expect(wrapper.text()).toContain('RSI12')
  })
})
