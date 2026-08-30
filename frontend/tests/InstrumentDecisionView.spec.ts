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

  it('renders the selected stock option structure and post-close flags inside the stock page', async () => {
    vi.spyOn(api, 'createDailyDataset').mockRejectedValue(new Error('backend offline'))
    vi.spyOn(api, 'listObservationGroups').mockResolvedValue([])
    vi.spyOn(api, 'getObservationOptions').mockResolvedValue({
      run_id: 'observation-1',
      status: 'available',
      trading_date: '2026-08-21',
      cutoff_time: '2026-08-22T05:30:00Z',
      available: true,
      options: {
        is_mock: false,
        status: 'available',
        available: true,
        data_state: 'live',
        provider: 'test-provider',
        source_mode: 'snapshot',
        captured_at: '2026-08-22T05:30:00Z',
        requested_symbols: ['INTC'],
        unavailable_symbols: [],
        symbols: [{
          symbol: 'INTC',
          spot: 31,
          spot_time: '2026-08-22T05:30:00Z',
          overview: {},
          expirations: [{
            expiration: '2026-08-28',
            days_to_expiry: 6,
            contract_count: 2,
            max_pain: 31,
            expected_move: { amount: 1.2, percent: 3.9, atm_strike: 31 },
            exposure: {
              totals: {
                call_dex: 100,
                put_dex: -60,
                net_dex: 40,
                absolute_dex: 160,
                call_gex: 0,
                put_gex: 0,
                modeled_net_gex: 12,
                absolute_gex: 12,
              },
              walls: {
                call_dex: { strike: 32, exposure: 100 },
                put_dex: { strike: 30, exposure: -60 },
                net_dex: { strike: 31, exposure: 40 },
              },
              by_strike: [],
              gamma_zones: [],
              gamma_noise_threshold: 0,
              usable_delta_contracts: 2,
              usable_gamma_contracts: 2,
            },
          }],
        }],
        subscription_quota: {},
        model_assumptions: [],
        warnings: [],
        note: '',
      },
      alignment: {
            available: true,
            status: 'flagged',
            source_phase: 'post_close_review',
            method: 'regular_close_vs_option_levels',
            proximity_percent: 0.6,
            price_definition: 'regular close',
            causality_note: 'DEX 影响候选只表示结构性邻近，不证明因果。',
            symbols: [{
              symbol: 'INTC',
              status: 'flagged',
              close_price: 31,
              close_time: '2026-08-22T05:30:00Z',
              price_source: 'market.primary',
              price_kind: 'regular_price',
              spot: 31,
              flags: ['near_max_pain', 'near_dex_wall'],
              flagged: true,
              expirations: [{
                expiration: '2026-08-28',
                max_pain: 31,
                max_pain_distance: 0,
                max_pain_distance_percent: 0,
                near_max_pain: true,
                dex_walls: [{
                  kind: 'net_dex',
                  label: 'Net DEX Wall',
                  strike: 31,
                  exposure: 40,
                  distance: 0,
                  distance_percent: 0,
                  near: true,
                }],
                near_dex_wall: true,
                dex_influence_candidate: true,
                flags: ['near_max_pain', 'near_dex_wall'],
              }],
            }],
            flagged_symbols: ['INTC'],
            flag_count: 1,
            unavailable_symbols: [],
            warnings: [],
          },
      message: null,
    })

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

    expect(wrapper.find('[data-testid="instrument-options-evidence"]').exists()).toBe(true)
    expect(wrapper.find('.decision-chart-column > [data-testid="instrument-options-evidence"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('INTC 期权位置')
    expect(wrapper.text()).toContain('REGULAR CLOSE')
    expect(wrapper.text()).toContain('PRICE LOCATION')
    expect(wrapper.text()).toContain('收盘价接近 Max Pain')
    expect(wrapper.text()).toContain('DEX 影响候选')
    expect(wrapper.find('.instrument-options-level-map').exists()).toBe(true)
    expect(wrapper.findAll('.instrument-options-flag')).toHaveLength(2)
  })

  it('does not bind an options snapshot from another trading date', async () => {
    vi.spyOn(api, 'createDailyDataset').mockRejectedValue(new Error('backend offline'))
    vi.spyOn(api, 'listObservationGroups').mockResolvedValue([])
    const getObservationOptions = vi.spyOn(api, 'getObservationOptions').mockResolvedValue({
      run_id: null,
      status: 'unavailable',
      trading_date: '2026-08-21',
      cutoff_time: null,
      available: false,
      options: {
        is_mock: true,
        status: 'not_collected',
        available: false,
        data_state: 'placeholder',
        note: 'fixture',
      },
      alignment: null,
      message: '没有找到 2026-08-21 对应的盘后观察期权快照。',
    })

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

    expect(getObservationOptions).toHaveBeenCalledWith('2026-08-21', 'INTC')
    expect(wrapper.find('.instrument-options-error').exists()).toBe(true)
    expect(wrapper.text()).toContain('没有找到 2026-08-21 对应的盘后观察期权快照')
  })
})
