import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import { router } from '@/router'
import ObservationRunView from '@/views/ObservationRunView.vue'
import type { ObservationRun, ObservationRunSummary } from '@/types/api'

enableAutoUnmount(afterEach)

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

function summary(detail: ObservationRun): ObservationRunSummary {
  return {
    run_id: detail.run_id, status: detail.status, trigger_mode: detail.trigger_mode,
    trading_date: detail.trading_date, created_at: detail.created_at,
    completed_at: detail.completed_at, error_message: detail.error_message,
    universe_revision_id: detail.universe_revision_id ?? null,
    universe_freshness: detail.universe_freshness ?? 'unknown',
    group_count: detail.group_count, successful_group_count: detail.successful_group_count ?? 0,
    failed_group_count: detail.failed_group_count ?? 0,
    quality_issue_count: detail.report?.summary.quality_issue_count ?? 0,
  }
}

afterEach(() => vi.restoreAllMocks())

describe('ObservationRunView', () => {
  it('does not start a detail request when the summary arrives after unmount', async () => {
    let finishList!: (value: ObservationRunSummary[]) => void
    vi.spyOn(api, 'listObservationRunSummaries').mockImplementation(() => new Promise(resolve => { finishList = resolve }))
    const details = vi.spyOn(api, 'getObservationRun').mockResolvedValue(run)
    const wrapper = mount(ObservationRunView, {
      global: { plugins: [router], stubs: { AppShell: true, RouterLink: true } },
    })
    wrapper.unmount()
    finishList([summary(run)])
    await flushPromises()
    expect(details).not.toHaveBeenCalled()
  })

  it('renders the frozen deterministic close report in decision order', async () => {
    vi.spyOn(api, 'listObservationRunSummaries').mockResolvedValue([summary(run)])
    vi.spyOn(api, 'getObservationRun').mockResolvedValue(run)
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
    vi.spyOn(api, 'listObservationRunSummaries').mockResolvedValue([summary(v2Run)])
    vi.spyOn(api, 'getObservationRun').mockResolvedValue(v2Run)

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

  it('clears the previous report and ignores late detail responses when switching runs', async () => {
    const second = { ...run, run_id: 'run-2', report: { ...run.report!, content_sha256: 'second-report' } }
    const third = { ...run, run_id: 'run-3', report: { ...run.report!, content_sha256: 'third-report' } }
    vi.spyOn(api, 'listObservationRunSummaries').mockResolvedValue([summary(run), summary(second), summary(third)])
    let finishSecond!: (value: ObservationRun) => void
    let finishThird!: (value: ObservationRun) => void
    const pendingSecond = new Promise<ObservationRun>(resolve => { finishSecond = resolve })
    const pendingThird = new Promise<ObservationRun>(resolve => { finishThird = resolve })
    const details = vi.spyOn(api, 'getObservationRun').mockImplementation(id => {
      if (id === 'run-2') return pendingSecond
      if (id === 'run-3') return pendingThird
      return Promise.resolve(run)
    })
    const wrapper = mount(ObservationRunView, {
      global: { plugins: [router], stubs: { AppShell: true, RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('report-has')
    await wrapper.get('select').setValue('run-2')
    expect(wrapper.text()).not.toContain('NVDA')
    await wrapper.get('select').setValue('run-3')
    expect(details.mock.calls[1][1]?.aborted).toBe(true)
    finishThird(third)
    await flushPromises()
    expect(wrapper.text()).toContain('third-repo')
    finishSecond(second)
    await flushPromises()
    expect(wrapper.text()).toContain('third-repo')
    expect(wrapper.text()).not.toContain('second-rep')
    wrapper.unmount()
    expect(details.mock.calls[2][1]?.aborted).toBe(true)
  })
})
