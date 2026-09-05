import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import { router } from '@/router'
import ObservationRunView from '@/views/ObservationRunView.vue'
import type { ObservationRun } from '@/types/api'

const run = {
  run_id: 'run-1',
  status: 'succeeded',
  trigger_mode: 'scheduled',
  trading_date: '2026-08-24',
  idempotency_key: 'key',
  group_ids: ['semiconductors'],
  group_version_ids: ['group-v1'],
  group_snapshots: [],
  group_count: 1,
  successful_group_count: 1,
  failed_group_count: 0,
  report: {
    schema_version: 'urus.observation_report.v2',
    mode: 'deterministic-only',
    run_id: 'run-1',
    trading_date: '2026-08-24',
    summary: { requested_group_count: 1, successful_group_count: 1, failed_group_count: 0, quality_issue_count: 1, strategy_conflict_count: 1 },
    group_rankings: [{ group_id: 'semiconductors', group_name: '半导体', state: 'broad_strength', stance: 'bullish', action: 'prioritize', median_20d: 4.2, relative_20d: 2.1, breadth_ma20: 0.75, technical_rank_score: 13.8 }],
    improving_groups: [{ group_id: 'semiconductors', group_name: '半导体', change_score: 2.5 }],
    deteriorating_groups: [],
    anomalies: {
      leaders: [{ group_id: 'semiconductors', group_name: '半导体', symbol: 'NVDA', return_20d: 12, relative_20d: 8, dataset_id: 'dataset-1' }],
      laggards: [{ group_id: 'semiconductors', group_name: '半导体', symbol: 'INTC', return_20d: -9, relative_20d: -6, dataset_id: 'dataset-1' }],
    },
    strategy_conflicts: [{ group_id: 'semiconductors', group_name: '半导体', symbol: 'AMD', summary: '策略方向冲突' }],
    quality_issues: [{ scope: 'symbol', group_id: 'semiconductors', symbol: 'WOLF', status: 'partial', message: '历史不足' }],
    opportunity_lanes: { confirmed: [{ symbol: 'NVDA', group_name: '半导体', strategy_name: 'trend', stage: 'confirmed', score: 70, dataset_id: 'dataset-1' }], near_confirmation: [], forming: [] },
    risk_lanes: { invalidated: [{ symbol: 'INTC', group_name: '半导体', strategy_name: 'reversal', stage: 'invalidated', score: -60, dataset_id: 'dataset-1' }], bearish: [] },
    visuals: { group_momentum_map: [{ group_id: 'semiconductors', group_name: '半导体', relative_20d: 2.1, relative_20d_change: 0.8, breadth_ma20: 0.75 }], breadth_delta: [], state_transitions: [] },
    content_sha256: 'report-hash',
  },
  content_sha256: 'run-hash',
  created_at: '2026-08-24T21:30:00Z',
  completed_at: '2026-08-24T21:31:00Z',
  error_message: null,
} as ObservationRun

afterEach(() => vi.restoreAllMocks())

describe('ObservationRunView', () => {
  it('renders the frozen deterministic close report in decision order', async () => {
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([run])
    vi.spyOn(api, 'createObservationRun').mockResolvedValue(run)

    await router.push('/observation-runs')
    await router.isReady()
    const wrapper = mount(ObservationRunView, {
      global: {
        plugins: [router],
        stubs: { AppShell: true, RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('半导体')
    expect(text).toContain('NVDA')
    expect(text).toContain('INTC')
    expect(text).toContain('AMD')
    expect(text).toContain('WOLF')
    expect(text).toContain('机会泳道')
    expect(text).toContain('风险泳道')
    expect(text).toContain('DETERMINISTIC ONLY')
  })

  it('renders v2 themes and explicitly explains when cross-section ranking is unavailable', async () => {
    const v2Run = {
      ...run,
      report: {
        ...run.report,
        schema_version: 'urus.observation_report.v2',
        ranking_status: 'unavailable',
        leading_themes: [{ group_id: 'leaders', group_name: '领先组', phase: 'broad_strength_stable', category: 'leader' }],
        deteriorating_themes: [{ group_id: 'fading', group_name: '恶化组', phase: 'broad_strength_deteriorating', category: 'deteriorating' }],
        reversal_themes: [{ group_id: 'reversal', group_name: '潜在反转组', phase: 'broad_weakness_improving', category: 'reversal_watch' }],
      },
    } as ObservationRun
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([v2Run])

    await router.push('/observation-runs')
    await router.isReady()
    const wrapper = mount(ObservationRunView, {
      global: {
        plugins: [router],
        stubs: { AppShell: true, RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('领先主题')
    expect(text).toContain('领先组')
    expect(text).toContain('恶化主题')
    expect(text).toContain('恶化组')
    expect(text).toContain('潜在反转主题')
    expect(text).toContain('潜在反转组')
    expect(text).toContain('样本不足')
    expect(text).toContain('排名不可用')
  })
})
