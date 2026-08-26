import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import InstrumentDecisionView from '@/views/InstrumentDecisionView.vue'
import { router } from '@/router'
import type { ObservationGroup } from '@/types/api'

afterEach(() => vi.restoreAllMocks())

describe('InstrumentDecisionView', () => {
  it('keeps the evidence workspace useful in local demo mode without disguising the data state', async () => {
    vi.spyOn(api, 'createDailyDataset').mockRejectedValue(new Error('backend offline'))
    vi.spyOn(api, 'listObservationGroups').mockResolvedValue([
      {
        version_id: 'semiconductors-v1',
        group_id: 'semiconductors',
        version: 1,
        status: 'active',
        display_name: '半导体',
        description: '核心芯片观察组',
        symbols: ['INTC', 'AMD'],
        benchmark_symbols: ['SOXX'],
        tags: ['sector'],
        display_order: 1,
        content_sha256: 'group-hash-1',
        created_at: '2026-08-21T00:00:00Z',
        activated_at: '2026-08-21T00:00:00Z',
      } as ObservationGroup,
      {
        version_id: 'optical-v1',
        group_id: 'optical-modules',
        version: 1,
        status: 'active',
        display_name: '光模块',
        description: '光模块观察组',
        symbols: ['LITE', 'COHR'],
        benchmark_symbols: ['QQQ'],
        tags: ['sector'],
        display_order: 2,
        content_sha256: 'group-hash-2',
        created_at: '2026-08-21T00:00:00Z',
        activated_at: '2026-08-21T00:00:00Z',
      } as ObservationGroup,
    ])
    await router.push('/')
    await router.isReady()

    const wrapper = mount(InstrumentDecisionView, {
      global: {
        plugins: [router],
        stubs: {
          AppShell: true,
          DecisionChartWorkspace: true,
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('EVIDENCE DESK')
    expect(wrapper.text()).toContain('LOCAL DEMO')
    expect(wrapper.text()).toContain('当前事实')
    expect(wrapper.text()).toContain('AI 决策')
    expect(wrapper.text()).toContain('策略会读取同一份冻结日 K')
    expect(wrapper.text()).toContain('美股大盘')
    expect(wrapper.text()).toContain('半导体')
    expect(wrapper.text()).toContain('本次分析证据')
    expect(wrapper.text()).toContain('决策检查清单')
    expect(wrapper.text()).not.toContain('日 K 决策工作台')
    expect(wrapper.text()).not.toContain('先看完整事实，再看算法策略')

    const sectorGroups = wrapper.findAll('.watch-group-toggle')
    expect(sectorGroups).toHaveLength(2)
    expect(sectorGroups[0].attributes('aria-expanded')).toBe('true')
    await sectorGroups[0].trigger('click')
    expect(sectorGroups[0].attributes('aria-expanded')).toBe('false')
  })

  it('keeps indicator recommendation separate and hides the legacy self-selection group', async () => {
    vi.spyOn(api, 'createDailyDataset').mockRejectedValue(new Error('backend offline'))
    vi.spyOn(api, 'listObservationGroups').mockResolvedValue([
      {
        version_id: 'indicator-v1', group_id: 'core-watchlist', version: 1, status: 'active', source: 'universe', universe_revision_id: null,
        display_name: '指标推荐', description: '自动生成', symbols: ['INTC'], benchmark_symbols: ['QQQ'], tags: ['watchlist', 'indicator-recommendation'], display_order: 0,
        content_sha256: 'indicator-hash', created_at: '2026-08-21T00:00:00Z', activated_at: '2026-08-21T00:00:00Z',
      } as ObservationGroup,
      {
        version_id: 'self-v1', group_id: 'self-selected-group', version: 1, status: 'active', source: 'manual', universe_revision_id: null,
        display_name: '自选组', description: '用户维护', symbols: ['INTC'], benchmark_symbols: ['QQQ'], tags: ['watchlist', 'self-selected'], display_order: 10,
        content_sha256: 'self-hash', created_at: '2026-08-21T00:00:00Z', activated_at: '2026-08-21T00:00:00Z',
      } as ObservationGroup,
      {
        version_id: 'theme-v1', group_id: 'theme-semiconductors', version: 1, status: 'active', source: 'universe', universe_revision_id: null,
        display_name: '半导体', description: '主题组', symbols: ['INTC'], benchmark_symbols: ['QQQ'], tags: ['universe-synced', 'theme'], display_order: 20,
        content_sha256: 'theme-hash', created_at: '2026-08-21T00:00:00Z', activated_at: '2026-08-21T00:00:00Z',
      } as ObservationGroup,
    ])
    await router.push('/')
    await router.isReady()

    const wrapper = mount(InstrumentDecisionView, {
      global: {
        plugins: [router],
        stubs: {
          AppShell: true,
          DecisionChartWorkspace: true,
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    expect(wrapper.findAll('.watchlist-block')).toHaveLength(2)
    expect(wrapper.find('.watchlist-core-block').text()).toContain('指标推荐')
    const sectorWatchlist = wrapper.find('.watchlist-block:not(.watchlist-core-block)')
    expect(sectorWatchlist.text()).toContain('SECTOR WATCHLIST')
    expect(sectorWatchlist.text()).toContain('半导体')
    expect(sectorWatchlist.text()).not.toContain('自选组')
    expect(sectorWatchlist.text()).not.toContain('自选个股')
    expect(wrapper.find('.watch-group-self').exists()).toBe(false)
  })

  it('loads the immutable dataset from a cross-section link instead of refreezing current data', async () => {
    const create = vi.spyOn(api, 'createDailyDataset')
    const getDataset = vi.spyOn(api, 'getDailyDataset').mockResolvedValue({ dataset_id: 'frozen-1' } as never)
    const getChart = vi.spyOn(api, 'getDailyChart').mockResolvedValue({
      dataset_id: 'frozen-1',
      instruments: {
        INTC: {
          symbol: 'INTC',
          price: { symbol: 'INTC', bars: [{ date: '2026-08-21', open: 20, high: 21, low: 19, close: 20.5, volume: 1000, adjustment: 'QFQ' }] },
          series: [],
          indicator_snapshot_id: null,
          quality: { status: 'ok', warnings: [] },
        },
      },
    } as never)
    const getStrategies = vi.spyOn(api, 'getDailyStrategies').mockResolvedValue({
      dataset_id: 'frozen-1',
      strategy_decisions: [],
      deterministic_synthesis: {},
    })
    vi.spyOn(api, 'listObservationGroups').mockResolvedValue([])

    await router.push('/instruments/INTC?dataset=frozen-1&run=run-1')
    await router.isReady()
    const wrapper = mount(InstrumentDecisionView, {
      global: {
        plugins: [router],
        stubs: {
          AppShell: true,
          DecisionChartWorkspace: true,
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    expect(create).not.toHaveBeenCalled()
    expect(getDataset).toHaveBeenCalledWith('frozen-1')
    expect(getChart).toHaveBeenCalledWith('frozen-1')
    expect(getStrategies).toHaveBeenCalledWith('frozen-1')
    expect(wrapper.find('.demo-banner').exists()).toBe(false)
    expect(wrapper.find('.connection-state').text()).toContain('EVIDENCE API')
  })
})
