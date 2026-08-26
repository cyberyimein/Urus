import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import { router } from '@/router'
import GroupObservationView from '@/views/GroupObservationView.vue'
import type { GroupDailySnapshot, ObservationGroup } from '@/types/api'

const group = {
  version_id: 'group-version-1',
  group_id: 'semiconductors',
  version: 1,
  status: 'active',
  display_name: '半导体',
  description: '核心芯片观察组',
  symbols: ['INTC', 'AMD'],
  benchmark_symbols: ['QQQ'],
  tags: ['sector'],
  display_order: 1,
  content_sha256: 'group-hash',
  created_at: '2026-08-21T00:00:00Z',
  activated_at: '2026-08-21T00:00:00Z',
} as ObservationGroup

const snapshot = {
  schema_version: 'urus.group_daily_snapshot.v3',
  feature_version: 'technical_v5',
  dataset_id: 'dataset-1',
  indicator_snapshot_ids: ['indicator-1', 'indicator-2'],
  group: {
    group_id: 'semiconductors',
    version_id: 'group-version-1',
    version: 1,
    display_name: '半导体',
    symbols: ['INTC', 'AMD'],
    benchmark_symbols: ['QQQ'],
  },
  trading_date: '2026-08-21',
  quality: { requested_symbol_count: 2, valid_symbol_count: 2, missing_symbol_count: 0, status: 'ok', warnings: [] },
  features: {
    valid_symbol_count: 2,
    requested_symbol_count: 2,
    missing_symbol_count: 0,
    returns_percent: { '20d': { count: 2, median: 4.2, q1: 1.5, q3: 6.9, min: 1.5, max: 6.9 } },
    breadth: { above_ma20: 1, above_ma50: 0.5, above_ma200: 0.5 },
    rsi_distribution: {},
    rsi_extremes: {},
    macd_positive_percent: 0.5,
    volume_expansion_percent: 0.5,
    relative_strength: { benchmark: 'QQQ', median_excess_20d: 2.1, positive_excess_20d_percent: 1 },
    cross_sectional_dispersion_1d: 1.2,
    leaders: [{ symbol: 'AMD', return_percent: 6.9 }],
    laggards: [{ symbol: 'INTC', return_percent: 1.5 }],
    leader_concentration: 0.7,
  },
  symbols: [
    { symbol: 'AMD', valid: true, quality_status: 'ok', latest_close: 90, returns_percent: { '20': 6.9 }, trend: { state: 'strong' }, rsi14: 62, macd_histogram: 1.2, relative_excess_percent: { '20d': 2.4 }, volume_ratio_20d: 1.3 },
    { symbol: 'INTC', valid: true, quality_status: 'ok', latest_close: 24, returns_percent: { '20': 1.5 }, trend: { state: 'mixed' }, rsi14: 48, macd_histogram: -0.2, relative_excess_percent: { '20d': -1.1 }, volume_ratio_20d: 0.9 },
  ],
  charts: {
    relative_strength: { benchmark: 'QQQ', series: [{ time: '2026-08-21', value: 101, benchmark_value: 100 }], dispersion: [] },
    breadth: { series: { above_ma20: [{ time: '2026-08-21', value: 1 }], above_ma50: [], above_ma200: [] } },
    rotation: [{ symbol: 'AMD', x_relative_20d: 2.4, y_relative_change: 0.8, size: 1.3, stance: 'bullish', trend: 'strong' }],
    heatmap: [{ symbol: 'AMD', trend: 'strong', momentum: 'neutral', volume: 'volume_up_demand', relative: 'leading', return_20d: 6.9 }],
    small_multiples: [
      { symbol: 'AMD', points: [{ time: '2026-08-21', value: 101, ma20: 100, ma50: 99 }], return_20d: 6.9, trend: 'strong' },
      { symbol: 'INTC', points: [{ time: '2026-08-21', value: 101, ma20: 100, ma50: 99 }], return_20d: 1.5, trend: 'mixed' },
    ],
  },
  group_decision: { state: 'broad_strength', stance: 'bullish', action: 'prioritize', reasons: ['广度改善。'] },
  group_strategy_decisions: [],
  changes: {
    previous_trading_date: '2026-08-20',
    group_state: { from: 'mixed', to: 'broad_strength', changed: true },
    median_20d_delta_percent: 1,
    breadth_ma20_delta: 0.5,
    relative_20d_delta_percent: 0.8,
    leaders_added: ['AMD'],
    leaders_removed: [],
  },
  strategy_decisions: [],
  deterministic_synthesis: {},
  content_sha256: 'snapshot-hash',
} as GroupDailySnapshot

afterEach(() => vi.restoreAllMocks())

describe('GroupObservationView', () => {
  it('renders the group snapshot and starts a manual observation run', async () => {
    vi.spyOn(api, 'listObservationGroups').mockResolvedValue([group])
    vi.spyOn(api, 'getObservationGroup').mockResolvedValue({ group, latest_snapshot: snapshot })
    const createRun = vi.spyOn(api, 'createObservationRun').mockResolvedValue({
      run_id: 'run-1',
      status: 'succeeded',
      trigger_mode: 'manual',
      trading_date: '2026-08-21',
      idempotency_key: 'key',
      group_ids: ['semiconductors'],
      group_version_ids: ['group-version-1'],
      group_snapshots: [],
      group_count: 1,
      content_sha256: 'run-hash',
      created_at: '2026-08-21T00:00:00Z',
      completed_at: '2026-08-21T00:00:01Z',
      error_message: null,
    })

    await router.push('/groups/semiconductors')
    await router.isReady()
    const wrapper = mount(GroupObservationView, {
      global: {
        plugins: [router],
        stubs: {
          AppShell: true,
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('半导体')
    expect(wrapper.text()).toContain('MEDIAN 20D')
    expect(wrapper.text()).toContain('+4.2%')
    expect(wrapper.text()).toContain('较 2026-08-20')
    expect(wrapper.findAll('.phase-c-small-card')).toHaveLength(2)

    await wrapper.find('.heatmap-row:not(.heatmap-header)').trigger('click')
    expect(wrapper.find('.phase-c-symbol-focus').text()).toContain('AMD')
    expect(wrapper.find('.phase-c-symbol-focus').text()).toContain('RSI 62.0')

    await wrapper.find('.phase-c-hero-actions .primary-button').trigger('click')
    await flushPromises()

    expect(createRun).toHaveBeenCalledWith(expect.objectContaining({
      group_ids: ['semiconductors'],
      trigger_mode: 'manual',
    }))
    expect(wrapper.text()).toContain('Observation Run succeeded')
  })

  it('separates indicator recommendation from theme groups and hides the legacy self-selection group', async () => {
    const indicator = {
      ...group,
      version_id: 'indicator-version-1',
      group_id: 'universe-core-watchlist',
      source: 'universe',
      display_name: '核心关注列表',
      description: '由当前部署 Universe 的 equity_watchlist 自动同步。',
      symbols: ['INTC', 'AMD'],
      tags: ['universe-synced', 'watchlist'],
      display_order: 0,
    } as ObservationGroup
    const legacy = {
      ...group,
      version_id: 'legacy-version-1',
      group_id: 'core-watchlist',
      source: 'manual',
      display_name: '核心观察组',
      tags: ['watchlist', 'user-qualified'],
      display_order: 10,
    } as ObservationGroup
    vi.spyOn(api, 'listObservationGroups').mockResolvedValue([indicator, legacy, group])
    vi.spyOn(api, 'getObservationGroup').mockResolvedValue({ group: indicator, latest_snapshot: snapshot })

    await router.push('/groups/universe-core-watchlist')
    await router.isReady()
    const wrapper = mount(GroupObservationView, {
      global: {
        plugins: [router],
        stubs: {
          AppShell: true,
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    expect(wrapper.find('.phase-c-indicator-section').text()).toContain('指标推荐')
    expect(wrapper.find('.phase-c-indicator-section').text()).not.toContain('核心关注列表')
    expect(wrapper.find('.phase-c-theme-section').text()).toContain('SECTOR WATCHLIST')
    expect(wrapper.find('.phase-c-theme-section').text()).toContain('半导体')
    expect(wrapper.text()).not.toContain('核心观察组')
    expect(wrapper.text()).not.toContain('自选组')
  })
})
