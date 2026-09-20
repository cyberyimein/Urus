import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import App from '@/App.vue'
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
  ai: { available: false, status: 'disabled', reason: 'remote workflow unavailable' },
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

const overlappingProjection: CrossSectionProjection = (() => {
  const overlappingGroup = {
    ...projection.groups[0],
    group_id: 'core-watchlist',
    group_name: '核心关注列表',
    display_name: '核心关注列表',
    group_version_id: 'group-version-2',
    snapshot_id: 'snapshot-2',
    dataset_id: 'dataset-1',
    symbol_count: 4,
    valid_symbol_count: 4,
  }
  const overlappingRows = projection.rows.map((row) => ({
    ...row,
    id: row.id.replace('semiconductors', 'core-watchlist'),
    group_id: 'core-watchlist',
    group_name: '核心关注列表',
    group_version_id: 'group-version-2',
    snapshot_id: 'snapshot-2',
    dataset_id: 'dataset-1',
  }))
  const uniqueRow = {
    ...projection.rows[0],
    id: 'core-watchlist:TSM:rsi14',
    group_id: 'core-watchlist',
    group_name: '核心关注列表',
    group_version_id: 'group-version-2',
    snapshot_id: 'snapshot-2',
    dataset_id: 'dataset-1',
    symbol: 'TSM',
    value: 55,
    previous_value: 48,
    change: 7,
    display_value: '55.00',
    state: 'balanced',
    state_label: '平衡',
    transition: null,
  }
  return {
    ...projection,
    groups: [projection.groups[0], overlappingGroup],
    rows: [...projection.rows, ...overlappingRows, uniqueRow],
    quality: { ...projection.quality, projected_group_count: 2, projected_row_count: 7, valid_row_count: 7 },
  }
})()

const relativeStrengthProjection: CrossSectionProjection = {
  ...projection,
  lens: { ...projection.lens, id: 'relative_strength_20d' },
  indicator: { ...projection.indicator!, id: 'relative_strength_20d', name: 'Relative Strength 20D', unit: 'percent' },
  groups: [
    { ...projection.groups[0], group_id: 'group-qqq', group_name: '组 QQQ', group_version_id: 'group-qqq-v1', snapshot_id: 'snapshot-qqq', dataset_id: 'dataset-rs', benchmark_symbols: ['QQQ'], symbol_count: 1, valid_symbol_count: 1 },
    { ...projection.groups[0], group_id: 'group-spy', group_name: '组 SPY', group_version_id: 'group-spy-v1', snapshot_id: 'snapshot-spy', dataset_id: 'dataset-rs', benchmark_symbols: ['SPY'], symbol_count: 1, valid_symbol_count: 1 },
  ],
  rows: [
    { ...projection.rows[0], id: 'group-qqq:INTC:relative_strength_20d', group_id: 'group-qqq', group_name: '组 QQQ', group_version_id: 'group-qqq-v1', snapshot_id: 'snapshot-qqq', dataset_id: 'dataset-rs', value: 2.2, previous_value: 1.1, change: 1.1, display_value: '2.20%', previous_display_value: '1.10%', state: 'leading', state_label: '相对领先', benchmark_symbols: ['QQQ'] },
    { ...projection.rows[0], id: 'group-spy:INTC:relative_strength_20d', group_id: 'group-spy', group_name: '组 SPY', group_version_id: 'group-spy-v1', snapshot_id: 'snapshot-spy', dataset_id: 'dataset-rs', value: -0.4, previous_value: 0.2, change: -0.6, display_value: '-0.40%', previous_display_value: '0.20%', state: 'lagging', state_label: '相对落后', benchmark_symbols: ['SPY'] },
  ],
  transitions: [],
  quality: { ...projection.quality, projected_group_count: 2, projected_row_count: 2, valid_row_count: 2, dataset_ids: ['dataset-rs'], snapshot_ids: ['snapshot-qqq', 'snapshot-spy'] },
}

beforeEach(() => {
  vi.spyOn(api, 'listFocusSymbols').mockResolvedValue([])
})

afterEach(() => vi.restoreAllMocks())

describe('CrossSectionView', () => {
  it('uses separate route components for indicator and strategy workspaces', () => {
    const indicatorComponent = router.resolve('/indicators').matched.at(-1)?.components.default
    const strategyComponent = router.resolve('/strategies').matched.at(-1)?.components.default

    expect(indicatorComponent).toBeDefined()
    expect(strategyComponent).toBeDefined()
    expect(indicatorComponent).not.toBe(strategyComponent)
  })

  it('keeps named feature versions free of a synthetic v prefix', async () => {
    vi.spyOn(api, 'listIndicatorCatalog').mockResolvedValue([projection.indicator!])
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([run])
    vi.spyOn(api, 'getIndicatorCrossSection').mockResolvedValue(projection)

    await router.push('/indicators/rsi14?run=run-1')
    await router.isReady()
    const wrapper = mount(CrossSectionView, {
      props: { lensType: 'indicator' },
      global: { plugins: [router], stubs: { AppShell: true, RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()

    expect(wrapper.get('.cross-section-lens-identity').text()).toBe('rsi14 · technical_v5')
    expect(wrapper.get('.cross-section-lens-tab').text()).toContain('technical_v5')
  })

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
    expect(wrapper.find('.cross-section-ai-button').attributes('disabled')).toBeDefined()
    expect(wrapper.findAll('.cross-section-table-row')).toHaveLength(3)
    expect(wrapper.findAll('.cross-section-symbol-card')).toHaveLength(0)
    expect(wrapper.find('.cross-section-table-row[data-symbol="INTC"]').text()).toContain('平衡 → 超卖')
    expect(wrapper.find('.cross-section-table-row[data-symbol="INTC"]').text()).toContain('状态变化')

    await wrapper.findAll('.cross-section-lens-tab')[1].trigger('click')
    await flushPromises()
    expect(getProjection).toHaveBeenLastCalledWith('run-1', 'macd_histogram')
  })

  it('deduplicates overlapping group facts and applies search, group, and indicator sorting', async () => {
    vi.spyOn(api, 'listIndicatorCatalog').mockResolvedValue([projection.indicator!])
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([run])
    vi.spyOn(api, 'getIndicatorCrossSection').mockResolvedValue(overlappingProjection)

    await router.push('/indicators/rsi14?run=run-1')
    await router.isReady()
    const wrapper = mount(CrossSectionView, {
      props: { lensType: 'indicator' },
      global: { plugins: [router], stubs: { AppShell: true, RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()

    expect(wrapper.findAll('.cross-section-table-row')).toHaveLength(4)
    expect(wrapper.text()).toContain('当前排序：新变化优先')
    expect(wrapper.find('.cross-section-table-row[data-symbol="INTC"]').findAll('.cross-section-group-label').map((item) => item.text())).toEqual(['半导体', '指标推荐'])
    expect(wrapper.findAll('.cross-section-table-row').map((row) => row.attributes('data-symbol'))).toEqual(['INTC', 'TSM', 'NVDA', 'AMD'])

    const search = wrapper.get('[data-testid="cross-section-search"]')
    await search.setValue('TSM')
    expect(wrapper.findAll('.cross-section-table-row')).toHaveLength(1)
    expect(wrapper.find('.cross-section-table-row').attributes('data-symbol')).toBe('TSM')
    await search.setValue('')

    await wrapper.get('[data-testid="cross-section-group-filter"]').setValue('semiconductors')
    expect(wrapper.findAll('.cross-section-table-row')).toHaveLength(3)
    await wrapper.get('[data-testid="cross-section-group-filter"]').setValue('')
    await wrapper.get('[data-testid="cross-section-sort"]').setValue('value')
    expect(wrapper.findAll('.cross-section-table-row').map((row) => row.attributes('data-symbol'))).toEqual(['NVDA', 'TSM', 'AMD', 'INTC'])

    const focusFilter = wrapper.get('[data-testid="cross-section-focus-filter"]')
    expect(focusFilter.find('option[value="focused"]').attributes('disabled')).toBeUndefined()
    expect(focusFilter.find('option[value="unfocused"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).toContain('关注列表已加载 · 0 个')
  })

  it('loads focus symbols and filters focused and unfocused rows without writes', async () => {
    vi.mocked(api.listFocusSymbols).mockResolvedValue([{ symbol: 'AMD', created_at: '2026-09-19T00:00:00Z' }])
    vi.spyOn(api, 'listIndicatorCatalog').mockResolvedValue([projection.indicator!])
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([run])
    vi.spyOn(api, 'getIndicatorCrossSection').mockResolvedValue(projection)

    await router.push('/indicators/rsi14?run=run-1')
    await router.isReady()
    const wrapper = mount(CrossSectionView, {
      props: { lensType: 'indicator' },
      global: { plugins: [router], stubs: { AppShell: true, RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()

    expect(wrapper.get('[data-testid="cross-section-focus-loaded"]').text()).toContain('1 个')
    expect(wrapper.get('option[value="focused"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('option[value="unfocused"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.findAll('.cross-section-table-row[data-focused="true"]')).toHaveLength(1)
    expect(wrapper.find('.cross-section-table-row[data-symbol="AMD"]').attributes('data-focused')).toBe('true')

    await wrapper.get('[data-testid="cross-section-focus-filter"]').setValue('focused')
    expect(wrapper.findAll('.cross-section-table-row')).toHaveLength(1)
    expect(wrapper.find('.cross-section-table-row').attributes('data-symbol')).toBe('AMD')
    await wrapper.get('[data-testid="cross-section-focus-filter"]').setValue('unfocused')
    expect(wrapper.findAll('.cross-section-table-row')).toHaveLength(2)
    expect(wrapper.find('[data-symbol="AMD"]').exists()).toBe(false)
  })

  it('keeps focus filters unknown after failure and restores them after retry', async () => {
    vi.mocked(api.listFocusSymbols)
      .mockRejectedValueOnce(new Error('focus read failed'))
      .mockResolvedValueOnce([{ symbol: 'AMD', created_at: '2026-09-19T00:00:00Z' }])
    vi.spyOn(api, 'listIndicatorCatalog').mockResolvedValue([projection.indicator!])
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([run])
    vi.spyOn(api, 'getIndicatorCrossSection').mockResolvedValue(projection)

    await router.push('/indicators/rsi14?run=run-1')
    await router.isReady()
    const wrapper = mount(CrossSectionView, {
      props: { lensType: 'indicator' },
      global: { plugins: [router], stubs: { AppShell: true, RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()

    expect(wrapper.get('[data-testid="cross-section-focus-error"]').text()).toContain('关注状态未知')
    expect(wrapper.get('option[value="focused"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('option[value="unfocused"]').attributes('disabled')).toBeDefined()
    expect(wrapper.findAll('.cross-section-table-row[data-focused="unknown"]')).toHaveLength(3)
    expect(wrapper.findAll('.cross-section-table-row')).toHaveLength(3)

    await wrapper.get('[data-testid="cross-section-focus-retry"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="cross-section-focus-loaded"]').text()).toContain('1 个')
    expect(wrapper.get('option[value="focused"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('option[value="unfocused"]').attributes('disabled')).toBeUndefined()
  })

  it('keeps relative-strength benchmark identities separate and highlights a discovery target', async () => {
    const relativeCatalog = [{ ...projection.indicator!, id: 'relative_strength_20d', name: 'Relative Strength 20D' }]
    vi.spyOn(api, 'listIndicatorCatalog').mockResolvedValue(relativeCatalog)
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([run])
    vi.spyOn(api, 'getIndicatorCrossSection').mockResolvedValue(relativeStrengthProjection)

    await router.push('/indicators/relative_strength_20d?run=run-1&discoveryEvent=indicator_transition:event-1&symbol=INTC&lens=relative_strength_20d')
    await router.isReady()
    const wrapper = mount(CrossSectionView, {
      props: { lensType: 'indicator' },
      global: { plugins: [router], stubs: { AppShell: true, RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()

    expect(wrapper.findAll('.cross-section-table-row')).toHaveLength(2)
    expect(wrapper.find('.cross-section-table-row[data-benchmark="QQQ"]').exists()).toBe(true)
    expect(wrapper.find('.cross-section-table-row[data-benchmark="SPY"]').exists()).toBe(true)
    expect(wrapper.findAll('.cross-section-table-row[data-highlighted="true"]')).toHaveLength(2)
    expect(wrapper.get('[data-testid="discovery-context"]').text()).toContain('已定位 INTC')
    expect(wrapper.text()).toContain('状态变化')
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
    expect(wrapper.findAll('.cross-section-group-label')).toHaveLength(3)
    expect(wrapper.findAll('.cross-section-table-row')).toHaveLength(3)
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
    expect(wrapper.text()).toContain('STRATEGY COMPARISON')
    expect(wrapper.text()).toContain('阶段、方向与 score 分开显示')
    expect(wrapper.text()).toContain('接近确认')
    expect(wrapper.text()).not.toContain('核心观察组')
    expect(wrapper.findAll('.cross-section-table-row')).toHaveLength(3)
    expect(wrapper.find('.cross-section-table-row[data-symbol="INTC"]').text()).toContain('偏多')
    expect(wrapper.find('.cross-section-table-row[data-symbol="INTC"]').text()).toContain('已确认')
    expect(wrapper.findAll('.cross-section-symbol-card')).toHaveLength(0)
    expect(wrapper.findAll('.cross-section-table-row').map((row) => row.attributes('data-symbol'))[0]).toBe('INTC')
    await wrapper.get('[data-testid="cross-section-sort"]').setValue('score')
    expect(wrapper.findAll('.cross-section-table-row').map((row) => row.attributes('data-symbol'))).toEqual(['INTC', 'NVDA', 'AMD'])
    await wrapper.get('[data-testid="cross-section-sort-direction"]').trigger('click')
    expect(wrapper.findAll('.cross-section-table-row').map((row) => row.attributes('data-symbol'))).toEqual(['AMD', 'NVDA', 'INTC'])
  })

  it('reloads the strategy catalog when the shared route changes from indicators', async () => {
    const listIndicators = vi.spyOn(api, 'listIndicatorCatalog').mockResolvedValue([projection.indicator!])
    const listStrategies = vi.spyOn(api, 'listStrategyCatalog').mockResolvedValue([strategyCatalog])
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([run])
    vi.spyOn(api, 'getIndicatorCrossSection').mockResolvedValue(projection)
    const getStrategyProjection = vi.spyOn(api, 'getStrategyCrossSection').mockResolvedValue(strategyProjection)

    await router.push('/indicators/rsi14?run=run-1')
    await router.isReady()
    const wrapper = mount(App, {
      global: {
        plugins: [router],
        stubs: {
          AppShell: true,
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    await router.push('/strategies?run=run-1')
    await flushPromises()

    expect(listIndicators).toHaveBeenCalled()
    expect(listStrategies).toHaveBeenCalled()
    expect(getStrategyProjection).toHaveBeenCalledWith('run-1', 'trend_momentum_v1')
    expect(getStrategyProjection).not.toHaveBeenCalledWith('run-1', 'rsi14')
    expect(wrapper.text()).toContain('策略横向扫描')
    wrapper.unmount()
  })

  it('uses the current route surface even if a shared view instance is reused', async () => {
    const listIndicators = vi.spyOn(api, 'listIndicatorCatalog').mockResolvedValue([projection.indicator!])
    const listStrategies = vi.spyOn(api, 'listStrategyCatalog').mockResolvedValue([strategyCatalog])
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([run])
    vi.spyOn(api, 'getIndicatorCrossSection').mockResolvedValue(projection)
    const getStrategyProjection = vi.spyOn(api, 'getStrategyCrossSection').mockResolvedValue(strategyProjection)

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

    await router.push('/strategies?run=run-1')
    await flushPromises()

    expect(listIndicators).toHaveBeenCalled()
    expect(listStrategies).toHaveBeenCalled()
    expect(getStrategyProjection).toHaveBeenCalledWith('run-1', 'trend_momentum_v1')
    expect(wrapper.text()).toContain('策略横向扫描')
    expect(wrapper.text()).not.toContain('指标横向扫描')
    wrapper.unmount()
  })

  it('falls back to the newest run with valid rows and leaves the cross-section AI action enabled', async () => {
    const staleRun = {
      ...run,
      run_id: 'run-latest',
      trading_date: '2026-08-22',
      report: { summary: { quality_issue_count: 3 } },
    } as ObservationRun
    const emptyProjection: CrossSectionProjection = {
      ...projection,
      observation_run_id: 'run-latest',
      scope_id: 'run-latest',
      content_sha256: 'empty-projection-hash',
      rows: projection.rows.map((row) => ({
        ...row,
        valid: false,
        status: 'missing',
        quality_status: 'stale',
        value: null,
        previous_value: null,
        change: null,
        display_value: '—',
        state: 'missing',
      })),
      quality: {
        ...projection.quality,
        status: 'partial',
        run_status: 'succeeded',
        valid_row_count: 0,
        missing_row_count: projection.rows.length,
      },
    }
    vi.spyOn(api, 'listIndicatorCatalog').mockResolvedValue([projection.indicator!])
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([staleRun, run])
    vi.spyOn(api, 'listRemoteDecisions').mockResolvedValue([])
    vi.spyOn(api, 'preflightRemoteDecision').mockResolvedValue({
      enabled: true,
      blockers: [],
      warnings: [],
      intent_type: 'indicator_attention',
      source: { observation_run_id: 'run-1', lens_id: 'rsi14' },
      source_summary: {},
      binding: null,
      input_sha256: 'input-hash',
      preflight_fingerprint: 'preflight-fingerprint',
    })
    const getProjection = vi.spyOn(api, 'getIndicatorCrossSection').mockImplementation(
      async (runId) => runId === staleRun.run_id ? emptyProjection : projection,
    )

    await router.push('/indicators')
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

    expect(getProjection).toHaveBeenCalledWith('run-latest', 'rsi14')
    expect(getProjection).toHaveBeenCalledWith('run-1', 'rsi14')
    expect(wrapper.find('select').element.value).toBe('run-1')
    expect(wrapper.text()).toContain('已自动切换到 2026-08-21 的可用冻结 Run')
    expect(wrapper.find('.cross-section-ai-button').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })
})
