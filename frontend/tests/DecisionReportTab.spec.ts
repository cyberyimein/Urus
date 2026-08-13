import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'

import DecisionReportTab from '@/components/research/DecisionReportTab.vue'
import type { DecisionReport, TechnicalReport } from '@/types/research'

const report: DecisionReport = {
  schema_version: 'urus.equity_decision.v3',
  status: 'succeeded',
  decision_phase: 'current_state',
  analysis_mode: 'current_state',
  trigger_type: 'manual',
  market_regime: {
    classification: 'selective risk-on',
    confidence: 0.72,
    summary: '趋势仍偏多，但软件扩散不足。',
    supporting_factors: ['QQQ 保持均线上方'],
    contradicting_factors: ['CTA 边际压力减弱'],
    evidence: [{ path: 'observations.current_state.market', observation: '当前市场快照' }],
  },
  rankings: [
    {
      rank: 1,
      symbol: 'NVDA',
      action: 'watch',
      score: 82,
      confidence: 0.74,
      thesis: '趋势领先，但不适合追价。',
      risks: ['接近 Gamma Flip'],
      missing_fields: [],
      invalidation_conditions: ['跌破 20D 均线'],
      evidence: [{ path: 'observations.current_state.instruments[NVDA]', observation: 'NVDA 技术证据' }],
    },
  ],
  equity_option_context: [
    {
      symbol: 'NVDA',
      available: true,
      gamma_regime: 'positive_gamma',
      primary_gamma_flip: 176.5,
      max_pain: 175,
      volatility_pricing: { iv: 31.4, hv_30d: 38.8, iv_hv_spread: -7.4, iv_hv_regime: 'moderate_discount' },
      risk_flags: ['spot_near_gamma_flip'],
      evidence_path: 'observations.current_state.options.symbols[NVDA]',
    },
  ],
  portfolio_warnings: [],
  disclaimer: 'Research only.',
}

const technical: TechnicalReport = {
  instruments: {
    themes: {
      半导体: [{
        symbol: 'NVDA',
        trend: '高于 MA20',
        quote: { last_price: 176.2, change_percent: 1.4 },
        technical: {
          as_of: '2026-08-12',
          returns_percent: { '1d': 1.4, '5d': 2.2, '20d': 6.8, '60d': 12.1, '252d': 88.4 },
          moving_average: { '20d': 170, '50d': 164, '100d': 151, '200d': 128 },
          rsi14: { value: 63.4, state: 'positive' },
          macd_12_26_9: { dif: 2.4, dea: 1.8, histogram: 0.6 },
          realized_volatility: { '20d': { value: 31.2 } },
          atr14_percent: { value: 2.1 },
          volume_effort_result: { volume_ratio_20d: 1.3, signal: 'normal_up' },
          high_low_distance_percent: { '252d_high': -2.4, '252d_low': 78.1 },
        },
        relative_strength: { excess_returns_percent: { '5d': 1.1, '20d': 4.6, '60d': 9.2 } },
      }],
    },
  },
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('DecisionReportTab', () => {
  it('renders a conclusion-first current-state report and opens a symbol drawer', async () => {
    const wrapper = mount(DecisionReportTab, { props: { report, technical } })

    expect(wrapper.text()).toContain('当前市场状态')
    expect(wrapper.text()).toContain('趋势仍偏多，但软件扩散不足。')
    expect(wrapper.text()).toContain('关注标的')
    expect(wrapper.findAll('.ranking-card')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('执行就绪')

    await wrapper.find('.attention-table tbody tr').trigger('click')
    expect(wrapper.emitted('select-symbol')?.[0]).toEqual(['NVDA'])

    await wrapper.setProps({ selectedSymbol: 'NVDA' })
    expect(document.body.textContent).toContain('NVDA')
    expect(document.body.textContent).toContain('风险与证据')
    expect(document.body.querySelector('.inspection-drawer-shell')).not.toBeNull()
    expect(document.body.querySelector('.instrument-detail-drawer')?.getAttribute('role')).toBe('complementary')
    expect(document.body.querySelector('.instrument-detail-drawer')?.hasAttribute('aria-modal')).toBe(false)

    const drawerTabs = document.body.querySelectorAll<HTMLButtonElement>('.drawer-tabs button')
    expect(drawerTabs).toHaveLength(4)
    drawerTabs[1].click()
    await wrapper.vm.$nextTick()
    expect(document.body.textContent).toContain('收益与均线')
    expect(document.body.textContent).toContain('63.4')
    expect(document.body.textContent).not.toContain('技术详情尚未单独投影')
  })

  it('keeps the technical report path visible when AI output is unavailable', () => {
    const wrapper = mount(DecisionReportTab, {
      props: { report: null, status: 'failed', errorMessage: '模型返回不完整' },
    })

    expect(wrapper.text()).toContain('AI 现状分析不可用')
    expect(wrapper.text()).toContain('模型返回不完整')
  })

  it('does not render an empty decision cockpit for a failed report payload', () => {
    const wrapper = mount(DecisionReportTab, {
      props: {
        report: { ...report, status: 'failed', market_regime: {}, rankings: [], equity_option_context: [] },
        errorMessage: 'synthesis rankings must cover every task symbol',
      },
    })

    expect(wrapper.text()).toContain('AI 现状分析不可用')
    expect(wrapper.text()).toContain('synthesis rankings must cover every task symbol')
    expect(wrapper.find('.attention-section').exists()).toBe(false)
  })
})
