import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import { router } from '@/router'
import CrossSectionView from '@/views/CrossSectionView.vue'
import type { ObservationRun } from '@/types/api'
import type { CrossSectionProjection } from '@/types/crossSection'

const run: ObservationRun = {
  run_id: 'run-1',
  status: 'succeeded',
  trigger_mode: 'scheduled',
  trading_date: '2026-08-21',
  idempotency_key: 'key',
  group_ids: ['semiconductors'],
  group_version_ids: ['group-version-1'],
  group_snapshots: [],
  group_count: 1,
  successful_group_count: 1,
  failed_group_count: 0,
  content_sha256: 'run-hash',
  created_at: '2026-08-21T00:00:00Z',
  completed_at: '2026-08-21T00:00:01Z',
  error_message: null,
}

const projection: CrossSectionProjection = {
  schema_version: 'urus.cross_section_projection.v1',
  scope_type: 'observation_run',
  scope_id: 'run-1',
  observation_run_id: 'run-1',
  trading_date: '2026-08-21',
  cutoff_time: '2026-08-21T21:00:00Z',
  comparison: {
    mode: 'previous_trading_session',
    status: 'ok',
    current_trading_date: '2026-08-21',
    previous_trading_date: '2026-08-20',
    previous_trading_dates: ['2026-08-20'],
    available_group_count: 1,
    group_count: 1,
    previous_snapshot_ids: ['snapshot-0'],
    previous_dataset_ids: ['dataset-0'],
  },
  lens: { type: 'indicator', id: 'rsi14', version: 'technical_v5', feature_version: 'technical_v5' },
  indicator: {
    id: 'rsi14',
    name: 'RSI 14',
    kind: 'indicator',
    description: '14 日相对强弱指标。',
    version: 'technical_v5',
    feature_version: 'technical_v5',
    unit: 'index',
    source_path: 'symbols[].rsi14',
    thresholds: { oversold: 30, overbought: 70 },
    content_sha256: 'indicator-hash',
  },
  group_version_ids: ['group-version-1'],
  failed_groups: [],
  groups: [{
    group_id: 'semiconductors',
    group_name: '半导体',
    group_version_id: 'group-version-1',
    group_version: 1,
    snapshot_id: 'snapshot-1',
    dataset_id: 'dataset-1',
    trading_date: '2026-08-21',
    previous_trading_date: '2026-08-20',
    benchmark_symbols: ['QQQ'],
    symbol_count: 3,
    valid_symbol_count: 3,
    missing_symbol_count: 0,
    quality_status: 'ok',
    state_counts: { oversold: 1, balanced: 1, overbought: 1 },
    stance_counts: {},
    distribution: { count: 3, median: 42, q1: 35, q3: 57, min: 28, max: 72 },
    previous_distribution: { count: 3, median: 40, q1: 34, q3: 55, min: 29, max: 68 },
    distribution_median_change: 2,
    warnings: [],
  }],
  rows: [
    {
      id: 'semiconductors:INTC:rsi14',
      group_id: 'semiconductors',
      group_name: '半导体',
      group_version_id: 'group-version-1',
      snapshot_id: 'snapshot-1',
      dataset_id: 'dataset-1',
      symbol: 'INTC',
      valid: true,
      status: 'ok',
      quality_status: 'ok',
      value: 28,
      previous_value: 35,
      change: -7,
      display_value: '28.00',
      state: 'oversold',
      state_label: '超卖',
      unit: 'index',
      thresholds: { oversold: 30, overbought: 70 },
      benchmark_symbols: ['QQQ'],
      transition: { type: 'state_changed', from: 'balanced', to: 'oversold' },
      evidence_refs: [],
      warnings: [],
    },
    {
      id: 'semiconductors:AMD:rsi14',
      group_id: 'semiconductors',
      group_name: '半导体',
      group_version_id: 'group-version-1',
      snapshot_id: 'snapshot-1',
      dataset_id: 'dataset-1',
      symbol: 'AMD',
      valid: true,
      status: 'ok',
      quality_status: 'ok',
      value: 42,
      previous_value: 40,
      change: 2,
      display_value: '42.00',
      state: 'balanced',
      state_label: '平衡',
      unit: 'index',
      thresholds: { oversold: 30, overbought: 70 },
      benchmark_symbols: ['QQQ'],
      transition: null,
      evidence_refs: [],
      warnings: [],
    },
    {
      id: 'semiconductors:NVDA:rsi14',
      group_id: 'semiconductors',
      group_name: '半导体',
      group_version_id: 'group-version-1',
      snapshot_id: 'snapshot-1',
      dataset_id: 'dataset-1',
      symbol: 'NVDA',
      valid: true,
      status: 'ok',
      quality_status: 'ok',
      value: 72,
      previous_value: 68,
      change: 4,
      display_value: '72.00',
      state: 'overbought',
      state_label: '超买',
      unit: 'index',
      thresholds: { oversold: 30, overbought: 70 },
      benchmark_symbols: ['QQQ'],
      transition: null,
      evidence_refs: [],
      warnings: [],
    },
  ],
  transitions: [{
    id: 'transition-1',
    group_id: 'semiconductors',
    group_name: '半导体',
    symbol: 'INTC',
    state: 'oversold',
    state_label: '超卖',
    value: 28,
    change: -7,
    transition: { type: 'state_changed', from: 'balanced', to: 'oversold' },
    snapshot_id: 'snapshot-1',
    dataset_id: 'dataset-1',
  }],
  quality: {
    status: 'ok',
    run_status: 'succeeded',
    requested_group_count: 1,
    projected_group_count: 1,
    failed_group_count: 0,
    projected_row_count: 3,
    valid_row_count: 3,
    missing_row_count: 0,
    snapshot_ids: ['snapshot-1'],
    dataset_ids: ['dataset-1'],
    warnings: [],
  },
  ai: { available: false, status: 'disabled', reason: 'Phase E' },
  content_sha256: 'projection-hash',
}

const strategyCatalog = {
  ...projection.indicator!,
  id: 'trend_momentum_v1',
  name: 'trend_momentum_v1',
  kind: 'strategy' as const,
  description: '确定性策略输出的横向比较视图。',
  unit: 'score',
  thresholds: {},
}

const strategyProjection: CrossSectionProjection = {
  ...projection,
  lens: { type: 'strategy', id: 'trend_momentum_v1', version: '1.0.0', implementation_sha256: 'strategy-hash' },
  indicator: undefined,
  strategy: strategyCatalog,
  groups: [{
    ...projection.groups[0],
    state_counts: { confirmed: 1, near_confirmation: 1, forming: 1 },
    stance_counts: { bullish: 1, bearish: 1, neutral: 1 },
    distribution: { count: 3, median: 0, q1: -30, q3: 35, min: -70, max: 70 },
  }, {
    ...projection.groups[0],
    group_id: 'core-watchlist',
    group_name: '核心观察组',
    display_name: '核心观察组',
    state_counts: { forming: 1 },
    stance_counts: { neutral: 1 },
    symbol_count: 1,
    valid_symbol_count: 1,
    distribution: { count: 1, median: 0, q1: 0, q3: 0, min: 0, max: 0 },
  }],
  rows: [
    {
      ...projection.rows[0],
      id: 'semiconductors:INTC:trend_momentum_v1',
      state: 'confirmed',
      state_label: 'confirmed',
      stance: 'bullish',
      action: 'prioritize',
      score: 70,
      value: 70,
      previous_value: 55,
      change: 15,
      display_value: 'prioritize',
      transition: { type: 'state_changed', from: 'near_confirmation', to: 'confirmed' },
      setup_progress: { stage: 'confirmed', bars_in_stage: 1 },
    },
    {
      ...projection.rows[1],
      id: 'semiconductors:AMD:trend_momentum_v1',
      state: 'near_confirmation',
      state_label: 'near_confirmation',
      stance: 'bearish',
      action: 'watch',
      score: -41,
      value: -41,
      previous_value: -30,
      change: -11,
      display_value: 'watch',
      transition: null,
      setup_progress: { stage: 'near_confirmation', bars_in_stage: 1 },
    },
    {
      ...projection.rows[2],
      id: 'semiconductors:NVDA:trend_momentum_v1',
      state: 'forming',
      state_label: 'forming',
      stance: 'neutral',
      action: 'wait',
      score: 0,
      value: 0,
      previous_value: 2,
      change: -2,
      display_value: 'wait',
      transition: null,
      setup_progress: { stage: 'forming', bars_in_stage: 1 },
    },
  ],
}

afterEach(() => vi.restoreAllMocks())

describe('CrossSectionView', () => {
  it('renders all symbols from the selected observation run and keeps AI disabled', async () => {
    const macd = {
      ...projection.indicator!,
      id: 'macd_histogram',
      name: 'MACD Histogram',
      description: 'MACD 柱体。',
      source_path: 'symbols[].macd_histogram',
    }
    vi.spyOn(api, 'listIndicatorCatalog').mockResolvedValue([projection.indicator!, macd])
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([run])
    const getProjection = vi.spyOn(api, 'getIndicatorCrossSection').mockImplementation(
      async (_runId, indicatorId) => indicatorId === 'rsi14' ? projection : { ...projection, lens: { ...projection.lens, id: indicatorId }, indicator: macd },
    )

    await router.push('/indicators/rsi14?run=run-1')
    await router.isReady()
    const wrapper = mount(CrossSectionView, {
      props: { lensType: 'indicator' },
      global: {
        plugins: [router],
        stubs: {
          AppShell: true,
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    expect(getProjection).toHaveBeenCalledWith('run-1', 'rsi14')
    expect(wrapper.text()).toContain('指标横向扫描')
    expect(wrapper.text()).toContain('半导体')
    expect(wrapper.text()).toContain('INTC')
    expect(wrapper.text()).toContain('AMD')
    expect(wrapper.text()).toContain('NVDA')
    expect(wrapper.text()).toContain('超卖')
    expect(wrapper.text()).toContain('超买')
    expect(wrapper.text()).toContain('颜色表示状态，不是交易指令')
    expect(wrapper.text()).toContain('前一交易日 2026-08-20')
    expect(wrapper.findAll('.cross-section-visual-marker--previous')).toHaveLength(3)
    expect(wrapper.find('.cross-section-ai-button').attributes('disabled')).toBeDefined()
    expect(wrapper.findAll('.cross-section-table-row')).toHaveLength(0)
    expect(wrapper.findAll('.cross-section-symbol-card')).toHaveLength(3)
    expect(wrapper.find('.cross-section-symbol-card[data-state="oversold"]')).toBeTruthy()
    expect(wrapper.find('.cross-section-symbol-card[data-state="balanced"]')).toBeTruthy()
    expect(wrapper.find('.cross-section-symbol-card[data-state="overbought"]')).toBeTruthy()
    expect(wrapper.find('.cross-section-scale-zone[data-state="oversold"]')).toBeTruthy()
    expect(wrapper.find('.cross-section-scale-zone[data-state="balanced"]')).toBeTruthy()
    expect(wrapper.find('.cross-section-scale-zone[data-state="overbought"]')).toBeTruthy()
    expect(wrapper.findAll('.cross-section-visual-marker--current')).toHaveLength(3)

    await wrapper.findAll('.cross-section-lens-tab')[1].trigger('click')
    await flushPromises()
    expect(getProjection).toHaveBeenLastCalledWith('run-1', 'macd_histogram')
  })

  it('ignores a failed latest run and selects the latest completed run', async () => {
    const failed = { ...run, run_id: 'run-failed', status: 'failed', trading_date: '2026-08-22' }
    vi.spyOn(api, 'listIndicatorCatalog').mockResolvedValue([projection.indicator!])
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([failed, run])
    const getProjection = vi.spyOn(api, 'getIndicatorCrossSection').mockResolvedValue(projection)

    await router.push('/indicators/rsi14?run=run-failed')
    await router.isReady()
    const wrapper = mount(CrossSectionView, {
      props: { lensType: 'indicator' },
      global: {
        plugins: [router],
        stubs: {
          AppShell: true,
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    expect(getProjection).toHaveBeenCalledWith('run-1', 'rsi14')
    expect(wrapper.find('select').element.value).toBe('run-1')
  })

  it('keeps the synced indicator recommendation group visible on a clean catalog', async () => {
    const recommendationGroup = {
      ...projection.groups[0],
      group_id: 'core-watchlist',
      group_name: '指标推荐',
      display_name: '指标推荐',
    }
    const recommendationProjection = {
      ...projection,
      groups: [recommendationGroup],
      rows: projection.rows.map((row) => ({
        ...row,
        id: row.id.replace('semiconductors', 'core-watchlist'),
        group_id: 'core-watchlist',
        group_name: '指标推荐',
      })),
      transitions: [],
    }
    vi.spyOn(api, 'listIndicatorCatalog').mockResolvedValue([projection.indicator!])
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([run])
    vi.spyOn(api, 'getIndicatorCrossSection').mockResolvedValue(recommendationProjection)

    await router.push('/indicators/rsi14?run=run-1')
    await router.isReady()
    const wrapper = mount(CrossSectionView, {
      props: { lensType: 'indicator' },
      global: {
        plugins: [router],
        stubs: {
          AppShell: true,
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('指标推荐')
    expect(wrapper.findAll('.cross-section-group-card')).toHaveLength(1)
  })

  it('uses strategy-specific stance, setup stage, and zero-centered score visuals', async () => {
    vi.spyOn(api, 'listStrategyCatalog').mockResolvedValue([strategyCatalog])
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([run])
    const getStrategyProjection = vi.spyOn(api, 'getStrategyCrossSection').mockResolvedValue(strategyProjection)

    await router.push('/strategies/trend_momentum_v1?run=run-1')
    await router.isReady()
    const wrapper = mount(CrossSectionView, {
      props: { lensType: 'strategy' },
      global: {
        plugins: [router],
        stubs: {
          AppShell: true,
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    expect(getStrategyProjection).toHaveBeenCalledWith('run-1', 'trend_momentum_v1')
    expect(wrapper.text()).toContain('策略横向扫描')
    expect(wrapper.text()).toContain('STRATEGY DECISION MAP')
    expect(wrapper.text()).toContain('阶段与 score 分开显示')
    expect(wrapper.text()).toContain('接近确认')
    expect(wrapper.text()).not.toContain('核心观察组')
    expect(wrapper.find('.cross-section-group-stage-counts').exists()).toBe(true)
    expect(wrapper.find('.cross-section-group-scale[data-visual-kind="decision"]').attributes('aria-label')).toContain('score -100 至 +100')
    expect(wrapper.findAll('.cross-section-group-card')).toHaveLength(1)
    expect(wrapper.findAll('.cross-section-symbol-card')).toHaveLength(3)
    expect(wrapper.find('.cross-section-symbol-card[data-state="bullish"][data-stage="confirmed"]')).toBeTruthy()
    expect(wrapper.find('.cross-section-symbol-card[data-state="bearish"][data-stage="near_confirmation"]')).toBeTruthy()
    expect(wrapper.findAll('.cross-section-decision-zone')).toHaveLength(9)
    expect(wrapper.findAll('.cross-section-decision-zero')).toHaveLength(3)
    expect(wrapper.text()).toContain('score · -100 至 +100')
  })
})
