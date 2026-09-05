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

  it('applies selected-symbol expansion to offline fallback watch groups', async () => {
    vi.spyOn(api, 'createDailyDataset').mockRejectedValue(new Error('backend offline'))
    vi.spyOn(api, 'listObservationGroups').mockRejectedValue(new Error('observation API offline'))
    vi.spyOn(api, 'getObservationOptions').mockResolvedValue({ run_id: null, status: 'unavailable', trading_date: '2026-08-21', cutoff_time: null, available: false, options: { is_mock: true, status: 'not_collected', available: false, data_state: 'placeholder' }, alignment: null, message: null })

    await router.push('/instruments/INTC')
    await router.isReady()
    const wrapper = mount(InstrumentDecisionView, {
      global: {
        plugins: [router],
        stubs: { AppShell: true, DecisionChartWorkspace: true, RemoteDecisionPanel: true, RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    const toggles = wrapper.findAll('.watch-group-toggle')
    expect(toggles).toHaveLength(2)
    expect(toggles[0].attributes('aria-expanded')).toBe('true')
    expect(toggles[1].attributes('aria-expanded')).toBe('false')
  })

  it('keeps indicator recommendation separate and hides the persisted self-selection group', async () => {
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

  it('renders the backend setup plan values instead of recomputing setup distances in the browser', async () => {
    vi.spyOn(api, 'getDailyDataset').mockResolvedValue({
      dataset_id: 'frozen-plan-1',
      schema_version: 'urus.daily_decision_dataset.v1',
      feature_version: 'technical_v5',
      trading_date: '2026-08-21',
      cutoff_time: '2026-08-22T05:30:00Z',
      market_timezone: 'America/New_York',
      bar_completion_policy: 'official_exchange_close_only_v1',
      scope: { scope_type: 'instrument', scope_id: 'AAA', scope_version: 1, symbols: ['AAA'], benchmark_symbols: ['QQQ'], trading_date: '2026-08-21' },
      bar_manifest: [],
      indicator_snapshot_ids: [],
      group_snapshot_ids: [],
      quality: { status: 'ok', symbols: {}, requested_symbol_count: 1, available_symbol_count: 1, errors: [], warnings: [] },
      status: 'ok',
      content_sha256: 'dataset-plan-hash',
    })
    vi.spyOn(api, 'getDailyChart').mockResolvedValue({
      dataset_id: 'frozen-plan-1',
      instruments: {
        AAA: {
          symbol: 'AAA',
          price: { symbol: 'AAA', bars: [{ date: '2026-08-21', open: 100, high: 103, low: 97, close: 100, volume: 1000 }] },
          series: [],
          indicator_snapshot_id: null,
          quality: { status: 'ok', bar_count: 1, latest_bar_date: '2026-08-21', input_bar_hash: null, warnings: [] },
        },
      },
    } as never)
    vi.spyOn(api, 'getDailyStrategies').mockResolvedValue({
      dataset_id: 'frozen-plan-1',
      strategy_decisions: [{
        decision_id: 'aaa-plan-decision',
        scope: { symbol: 'AAA' },
        strategy: { name: 'trend_momentum_v1', version: '1.0' },
        status: 'ok',
        stance: 'bullish',
        action: 'prioritize',
        score: 72,
        setup_progress: {
          stage: 'forming',
          confirmation_distance_atr: 9.99,
          invalidation_distance_atr: 8.88,
        },
        setup_plan: {
          direction: 'bullish',
          stage: 'near_confirmation',
          confirmation_price: 102.5,
          confirmation_operator: '>=',
          confirmation_reference: 'close',
          observation_price: 100,
          remaining_price: 2.5,
          remaining_percent: 2.5,
          remaining_atr: 0.4,
          invalidation_price: 98,
          invalidation_operator: '<',
          invalidation_reference: 'close',
          invalidation_distance_price: 2,
          invalidation_distance_percent: 2,
          invalidation_distance_atr: 0.32,
        },
        reasons: [{ code: 'fixture', detail: 'backend setup plan' }],
        risks: [],
        confirmation_conditions: ['legacy confirmation text'],
        invalidation_conditions: ['legacy invalidation text'],
        evidence_refs: [],
        visual_anchors: [],
        horizon: { value: 5, unit: 'trading_day' },
      }],
      deterministic_synthesis: { consensus_state: 'aligned', suggested_action: 'prioritize' },
    } as never)
    vi.spyOn(api, 'listObservationGroups').mockResolvedValue([{
      version_id: 'fixture-v1',
      group_id: 'fixture',
      version: 1,
      status: 'active',
      display_name: 'Fixture',
      description: '',
      symbols: ['AAA'],
      benchmark_symbols: ['QQQ'],
      tags: ['sector'],
      display_order: 1,
      content_sha256: 'group-hash',
      created_at: '2026-08-21T00:00:00Z',
      activated_at: '2026-08-21T00:00:00Z',
    } as ObservationGroup])
    vi.spyOn(api, 'getObservationOptions').mockResolvedValue({
      run_id: null,
      status: 'unavailable',
      trading_date: '2026-08-21',
      cutoff_time: null,
      available: false,
      options: { is_mock: true, status: 'not_collected', available: false, data_state: 'placeholder' },
      alignment: null,
      message: null,
    })

    await router.push('/instruments/AAA?dataset=frozen-plan-1')
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

    await wrapper.find('.strategy-decision-row').trigger('click')
    const detail = wrapper.find('.strategy-detail')
    expect(detail.text()).toContain('102.50')
    expect(detail.text()).toContain('0.40 ATR')
    expect(detail.text()).toContain('98.00')
    expect(detail.text()).not.toContain('9.99 ATR')
    expect(detail.text()).not.toContain('8.88 ATR')
    expect(detail.text()).not.toContain('legacy confirmation text')
    expect(detail.text()).not.toContain('legacy invalidation text')
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
            source_phase: 'post_close_observation',
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

  it('shows the selected symbol summary instead of the cross-sectional conflict', async () => {
    vi.spyOn(api, 'createDailyDataset').mockResolvedValue({
      dataset: {
        dataset_id: 'dataset-cross-symbol',
        schema_version: 'urus.daily_decision_dataset.v1',
        scope: { scope_type: 'observation_run', scope_id: 'run-1', symbols: ['AAA', 'BBB'], benchmark_symbols: ['QQQ'] },
        trading_date: '2026-08-21',
        market_timezone: 'America/New_York',
        status: 'ok',
        quality: { symbols: { AAA: { status: 'ok', warnings: [] } } },
        bar_manifest: [{ symbol: 'AAA', source: 'fixture', adjustment: 'QFQ' }],
        content_sha256: 'dataset-hash',
      },
      chart: {
        dataset_id: 'dataset-cross-symbol',
        instruments: {
          AAA: {
            symbol: 'AAA',
            price: { symbol: 'AAA', bars: [{ date: '2026-08-21', open: 20, high: 21, low: 19, close: 20.5, volume: 1000, adjustment: 'QFQ' }] },
            series: [],
            indicator_snapshot_id: null,
            quality: { status: 'ok', warnings: [] },
          },
        },
      },
      strategy_decisions: [
        {
          decision_id: 'aaa-decision',
          scope: { symbol: 'AAA' },
          strategy: { name: 'trend_momentum_v1', version: '1.0' },
          status: 'ok',
          stance: 'bullish',
          action: 'prioritize',
          score: 70,
          setup_progress: { stage: 'confirmed' },
          reasons: [{ code: 'fixture', detail: 'AAA is aligned' }],
          risks: [],
          confirmation_conditions: [],
          invalidation_conditions: [],
          evidence_refs: [],
          visual_anchors: [],
          horizon: { value: 5, unit: 'trading_day' },
        },
        {
          decision_id: 'bbb-decision',
          scope: { symbol: 'BBB' },
          strategy: { name: 'trend_momentum_v1', version: '1.0' },
          status: 'ok',
          stance: 'bearish',
          action: 'avoid',
          score: -70,
          setup_progress: { stage: 'confirmed' },
          reasons: [{ code: 'fixture', detail: 'BBB is weak' }],
          risks: [],
          confirmation_conditions: [],
          invalidation_conditions: [],
          evidence_refs: [],
          visual_anchors: [],
          horizon: { value: 5, unit: 'trading_day' },
        },
      ],
      deterministic_synthesis: {
        consensus_state: 'conflicted',
        suggested_action: 'watch',
        conflict_summary: 'AAA 与 BBB 的方向不同。',
        symbol_summaries: {
          AAA: { symbol: 'AAA', consensus_state: 'aligned', suggested_action: 'prioritize' },
          BBB: { symbol: 'BBB', consensus_state: 'aligned', suggested_action: 'avoid' },
        },
      },
    } as never)
    vi.spyOn(api, 'listObservationGroups').mockResolvedValue([
      {
        version_id: 'fixture-v1',
        group_id: 'fixture',
        version: 1,
        status: 'active',
        display_name: 'Fixture',
        description: '',
        symbols: ['AAA', 'BBB'],
        benchmark_symbols: ['QQQ'],
        tags: ['sector'],
        display_order: 1,
        content_sha256: 'group-hash',
        created_at: '2026-08-21T00:00:00Z',
        activated_at: '2026-08-21T00:00:00Z',
      } as ObservationGroup,
    ])
    vi.spyOn(api, 'getObservationOptions').mockResolvedValue({
      run_id: null,
      status: 'unavailable',
      trading_date: '2026-08-21',
      cutoff_time: null,
      available: false,
      options: { is_mock: true, status: 'not_collected', available: false, data_state: 'placeholder' },
      alignment: null,
      message: null,
    })

    await router.push('/instruments/AAA')
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

    const synthesisPanel = wrapper.get('.synthesis-panel')
    expect(synthesisPanel.text()).toContain('方向一致')
    expect(synthesisPanel.text()).toContain('优先观察')
    expect(synthesisPanel.text()).not.toContain('策略冲突')
  })

  it('shows the final action, structure, change and setup summary on the first screen', async () => {
    vi.spyOn(api, 'createDailyDataset').mockResolvedValue({
      dataset: {
        schema_version: 'urus.daily_decision_dataset.v1',
        feature_version: 'technical_v5',
        dataset_id: 'dataset-summary',
        trading_date: '2026-08-21',
        cutoff_time: '2026-08-22T05:30:00Z',
        market_timezone: 'America/New_York',
        bar_completion_policy: 'official_exchange_close_only_v1',
        scope: { scope_type: 'instrument', scope_id: 'AAA', scope_version: 1, symbols: ['AAA'], benchmark_symbols: ['QQQ'], trading_date: '2026-08-21' },
        bar_manifest: [{ symbol: 'AAA', bar_count: 260, start_date: '2025-08-20', end_date: '2026-08-21', input_bar_hash: 'bars', source: 'fixture', adjustment: 'QFQ', exchange: 'XNYS', source_revisions: [], quality_status: 'ok' }],
        indicator_snapshot_ids: ['indicator-summary'],
        group_snapshot_ids: [],
        quality: { status: 'ok', symbols: { AAA: { status: 'ok', bar_count: 260, latest_bar_date: '2026-08-21', input_bar_hash: 'bars', warnings: [] } }, requested_symbol_count: 1, available_symbol_count: 1, errors: [], warnings: [] },
        status: 'ok',
        content_sha256: 'dataset-summary-hash',
      },
      chart: {
        schema_version: 'urus.decision_chart_projection.v1',
        dataset_id: 'dataset-summary',
        scope: { scope_type: 'instrument', scope_id: 'AAA', scope_version: 1, symbols: ['AAA'], benchmark_symbols: ['QQQ'], trading_date: '2026-08-21' },
        timezone: 'America/New_York',
        instruments: { AAA: { symbol: 'AAA', price: { symbol: 'AAA', bars: [{ date: '2026-08-21', open: 100, high: 103, low: 97, close: 100, volume: 1000 }] }, series: [], indicator_snapshot_id: 'indicator-summary', quality: { status: 'ok', bar_count: 260, latest_bar_date: '2026-08-21', input_bar_hash: 'bars', warnings: [] } } },
        overlays: [], state_segments: [], events: [], quality: { status: 'ok', symbols: {}, requested_symbol_count: 1, available_symbol_count: 1, errors: [], warnings: [] }, content_sha256: 'chart-summary-hash',
      },
      strategy_decisions: [{
        decision_id: 'summary-decision',
        scope: { symbol: 'AAA' },
        strategy: { name: 'trend_momentum_v1', version: '1.0' },
        status: 'ok', stance: 'bullish', action: 'prioritize', score: 72,
        setup_progress: { stage: 'near_confirmation', confirmation_distance_atr: 0.4, invalidation_distance_atr: 0.32 },
        setup_plan: {
          direction: 'bullish', stage: 'near_confirmation', confirmation_price: 102.5, confirmation_operator: '>=', confirmation_reference: 'close', observation_price: 100,
          remaining_price: 2.5, remaining_percent: 2.5, remaining_atr: 0.4, invalidation_price: 98, invalidation_operator: '<', invalidation_reference: 'close',
          invalidation_distance_price: 2, invalidation_distance_percent: 2, invalidation_distance_atr: 0.32,
        },
        reasons: [{ code: 'fixture', detail: '结构偏强，等待触发。' }], risks: [], confirmation_conditions: [], invalidation_conditions: [], evidence_refs: [], visual_anchors: [], horizon: { value: 5, unit: 'trading_day' },
      }],
      deterministic_synthesis: { consensus_state: 'aligned', suggested_action: 'prioritize' },
    } as never)
    vi.spyOn(api, 'listObservationGroups').mockResolvedValue([])
    vi.spyOn(api, 'getObservationOptions').mockResolvedValue({ run_id: null, status: 'unavailable', trading_date: '2026-08-21', cutoff_time: null, available: false, options: { is_mock: true, status: 'not_collected', available: false, data_state: 'placeholder' }, alignment: null, message: null })

    await router.push('/instruments/AAA')
    await router.isReady()
    const wrapper = mount(InstrumentDecisionView, {
      global: {
        plugins: [router],
        stubs: { AppShell: true, DecisionChartWorkspace: true, RemoteDecisionPanel: true, RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('最终行动')
    expect(text).toContain('结构')
    expect(text).toContain('变化')
    expect(text).toContain('Setup')
    expect(text).toContain('优先观察')
    expect(text).toContain('触发条件')
    expect(text).toContain('102.50')
    expect(text).toContain('失效位')
    expect(text).toContain('98.00')
  })

  it('shows a condition-based setup without fake price-distance placeholders', async () => {
    vi.spyOn(api, 'createDailyDataset').mockResolvedValue({
      dataset: {
        schema_version: 'urus.daily_decision_dataset.v1', feature_version: 'technical_v5', dataset_id: 'dataset-conditional-setup', trading_date: '2026-08-21', cutoff_time: '2026-08-22T05:30:00Z', market_timezone: 'America/New_York', bar_completion_policy: 'official_exchange_close_only_v1',
        scope: { scope_type: 'instrument', scope_id: 'CCC', scope_version: 1, symbols: ['CCC'], benchmark_symbols: ['QQQ'], trading_date: '2026-08-21' }, bar_manifest: [], indicator_snapshot_ids: [], group_snapshot_ids: [],
        quality: { status: 'ok', symbols: { CCC: { status: 'ok', bar_count: 260, latest_bar_date: '2026-08-21', input_bar_hash: 'bars', warnings: [] } }, requested_symbol_count: 1, available_symbol_count: 1, errors: [], warnings: [] }, status: 'ok', content_sha256: 'conditional-dataset-hash',
      },
      chart: {
        schema_version: 'urus.decision_chart_projection.v1', dataset_id: 'dataset-conditional-setup', scope: { scope_type: 'instrument', scope_id: 'CCC', scope_version: 1, symbols: ['CCC'], benchmark_symbols: ['QQQ'], trading_date: '2026-08-21' }, timezone: 'America/New_York',
        instruments: { CCC: { symbol: 'CCC', price: { symbol: 'CCC', bars: [{ date: '2026-08-21', open: 100, high: 101, low: 99, close: 100, volume: 1000 }] }, series: [], indicator_snapshot_id: null, quality: { status: 'ok', bar_count: 260, latest_bar_date: '2026-08-21', input_bar_hash: 'bars', warnings: [] } } }, overlays: [], state_segments: [], events: [], quality: { status: 'ok', symbols: {}, requested_symbol_count: 1, available_symbol_count: 1, errors: [], warnings: [] }, content_sha256: 'conditional-chart-hash',
      },
      strategy_decisions: [{
        decision_id: 'conditional-setup-decision', scope: { symbol: 'CCC' }, strategy: { name: 'mean_reversion_v1', version: '1.0' }, status: 'ok', stance: 'bullish', action: 'watch', score: 48,
        setup_progress: { stage: 'near_confirmation', confirmation_distance_atr: null, invalidation_distance_atr: null }, setup_plan: null,
        reasons: [{ code: 'fixture', detail: '条件尚未满足。' }], risks: [], confirmation_conditions: ['收盘价站上 MA20 后确认入场。'], invalidation_conditions: ['收盘价跌破 MA50 时失效。'], evidence_refs: [], visual_anchors: [], horizon: { value: 5, unit: 'trading_day' },
      }],
      deterministic_synthesis: { consensus_state: 'mixed', suggested_action: 'watch' },
    } as never)
    vi.spyOn(api, 'listObservationGroups').mockResolvedValue([])
    vi.spyOn(api, 'getObservationOptions').mockResolvedValue({ run_id: null, status: 'unavailable', trading_date: '2026-08-21', cutoff_time: null, available: false, options: { is_mock: true, status: 'not_collected', available: false, data_state: 'placeholder' }, alignment: null, message: null })

    await router.push('/instruments/CCC')
    await router.isReady()
    const wrapper = mount(InstrumentDecisionView, {
      global: {
        plugins: [router],
        stubs: { AppShell: true, DecisionChartWorkspace: true, RemoteDecisionPanel: true, RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('条件型 Setup')
    expect(text).toContain('无价格距离')
    expect(text).toContain('收盘价站上 MA20 后确认入场。')
    expect(text).toContain('收盘价跌破 MA50 时失效。')
    expect(text).not.toContain('确认距 —')
    expect(text).not.toContain('失效距 —')
  })

  it('only expands groups containing the selected symbol and preserves manual expansions', async () => {
    vi.spyOn(api, 'createDailyDataset').mockRejectedValue(new Error('backend offline'))
    vi.spyOn(api, 'listObservationGroups').mockResolvedValue([
      {
        version_id: 'group-a-v1', group_id: 'group-a', version: 1, status: 'active', display_name: '组 A', description: '', symbols: ['AAA'], benchmark_symbols: ['QQQ'], tags: [], display_order: 1,
        content_sha256: 'group-a-hash', created_at: '2026-08-21T00:00:00Z', activated_at: '2026-08-21T00:00:00Z',
      } as ObservationGroup,
      {
        version_id: 'group-b-v1', group_id: 'group-b', version: 1, status: 'active', display_name: '组 B', description: '', symbols: ['AAA'], benchmark_symbols: ['QQQ'], tags: [], display_order: 2,
        content_sha256: 'group-b-hash', created_at: '2026-08-21T00:00:00Z', activated_at: '2026-08-21T00:00:00Z',
      } as ObservationGroup,
      {
        version_id: 'group-c-v1', group_id: 'group-c', version: 1, status: 'active', display_name: '组 C', description: '', symbols: ['BBB'], benchmark_symbols: ['QQQ'], tags: [], display_order: 3,
        content_sha256: 'group-c-hash', created_at: '2026-08-21T00:00:00Z', activated_at: '2026-08-21T00:00:00Z',
      } as ObservationGroup,
    ])
    vi.spyOn(api, 'getObservationOptions').mockResolvedValue({ run_id: null, status: 'unavailable', trading_date: '2026-08-21', cutoff_time: null, available: false, options: { is_mock: true, status: 'not_collected', available: false, data_state: 'placeholder' }, alignment: null, message: null })

    await router.push('/instruments/AAA')
    await router.isReady()
    const wrapper = mount(InstrumentDecisionView, {
      global: {
        plugins: [router],
        stubs: { AppShell: true, DecisionChartWorkspace: true, RemoteDecisionPanel: true, RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    let toggles = wrapper.findAll('.watch-group-toggle')
    expect(toggles).toHaveLength(3)
    expect(toggles[0].attributes('aria-expanded')).toBe('true')
    expect(toggles[1].attributes('aria-expanded')).toBe('true')
    expect(toggles[2].attributes('aria-expanded')).toBe('false')

    await toggles[2].trigger('click')
    expect(toggles[2].attributes('aria-expanded')).toBe('true')
    await wrapper.findAll('.watch-symbol').find((item) => item.text().includes('BBB'))?.trigger('click')
    await flushPromises()

    toggles = wrapper.findAll('.watch-group-toggle')
    expect(toggles.every((item) => item.attributes('aria-expanded') === 'true')).toBe(true)
  })

  it('prefers the selected sector group over an overlapping indicator recommendation', async () => {
    vi.spyOn(api, 'createDailyDataset').mockRejectedValue(new Error('backend offline'))
    vi.spyOn(api, 'listObservationGroups').mockResolvedValue([
      {
        version_id: 'indicator-v1', group_id: 'indicator', version: 1, status: 'active', source: 'universe', display_name: '指标推荐', description: '', symbols: ['AAA'], benchmark_symbols: ['QQQ'], tags: ['watchlist'], display_order: 1,
        content_sha256: 'indicator-hash', created_at: '2026-08-21T00:00:00Z', activated_at: '2026-08-21T00:00:00Z',
      } as ObservationGroup,
      {
        version_id: 'sector-v1', group_id: 'sector', version: 1, status: 'active', source: 'universe', display_name: '半导体', description: '', symbols: ['AAA'], benchmark_symbols: ['QQQ'], tags: ['theme'], display_order: 2,
        content_sha256: 'sector-hash', created_at: '2026-08-21T00:00:00Z', activated_at: '2026-08-21T00:00:00Z',
      } as ObservationGroup,
      {
        version_id: 'other-v1', group_id: 'other', version: 1, status: 'active', source: 'universe', display_name: '其他主题', description: '', symbols: ['BBB'], benchmark_symbols: ['QQQ'], tags: ['theme'], display_order: 3,
        content_sha256: 'other-hash', created_at: '2026-08-21T00:00:00Z', activated_at: '2026-08-21T00:00:00Z',
      } as ObservationGroup,
    ])
    vi.spyOn(api, 'getObservationOptions').mockResolvedValue({ run_id: null, status: 'unavailable', trading_date: '2026-08-21', cutoff_time: null, available: false, options: { is_mock: true, status: 'not_collected', available: false, data_state: 'placeholder' }, alignment: null, message: null })

    await router.push('/instruments/AAA')
    await router.isReady()
    const wrapper = mount(InstrumentDecisionView, {
      global: {
        plugins: [router],
        stubs: { AppShell: true, DecisionChartWorkspace: true, RemoteDecisionPanel: true, RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    const toggles = wrapper.findAll('.watch-group-toggle')
    expect(toggles).toHaveLength(3)
    expect(toggles[0].attributes('aria-expanded')).toBe('false')
    expect(toggles[1].attributes('aria-expanded')).toBe('true')
    expect(toggles[2].attributes('aria-expanded')).toBe('false')
  })

  it('expands the indicator recommendation when no sector contains the selected symbol', async () => {
    vi.spyOn(api, 'createDailyDataset').mockRejectedValue(new Error('backend offline'))
    vi.spyOn(api, 'listObservationGroups').mockResolvedValue([
      {
        version_id: 'indicator-v1', group_id: 'indicator-only', version: 1, status: 'active', source: 'universe', display_name: '指标推荐', description: '', symbols: ['CCC'], benchmark_symbols: ['QQQ'], tags: ['watchlist'], display_order: 1,
        content_sha256: 'indicator-only-hash', created_at: '2026-08-21T00:00:00Z', activated_at: '2026-08-21T00:00:00Z',
      } as ObservationGroup,
      {
        version_id: 'sector-v1', group_id: 'sector-only', version: 1, status: 'active', source: 'universe', display_name: '半导体', description: '', symbols: ['AAA'], benchmark_symbols: ['QQQ'], tags: ['theme'], display_order: 2,
        content_sha256: 'sector-only-hash', created_at: '2026-08-21T00:00:00Z', activated_at: '2026-08-21T00:00:00Z',
      } as ObservationGroup,
    ])
    vi.spyOn(api, 'getObservationOptions').mockResolvedValue({ run_id: null, status: 'unavailable', trading_date: '2026-08-21', cutoff_time: null, available: false, options: { is_mock: true, status: 'not_collected', available: false, data_state: 'placeholder' }, alignment: null, message: null })

    await router.push('/instruments/CCC')
    await router.isReady()
    const wrapper = mount(InstrumentDecisionView, {
      global: {
        plugins: [router],
        stubs: { AppShell: true, DecisionChartWorkspace: true, RemoteDecisionPanel: true, RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    const toggles = wrapper.findAll('.watch-group-toggle')
    expect(toggles[0].attributes('aria-expanded')).toBe('true')
    expect(toggles[1].attributes('aria-expanded')).toBe('false')
  })
})
