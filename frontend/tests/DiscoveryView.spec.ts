import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import DiscoveryView from '@/views/DiscoveryView.vue'
import type { ObservationRun } from '@/types/api'
import type { DiscoveryProjection, GroupPhaseChangeEvent } from '@/types/discovery'

enableAutoUnmount(afterEach)
beforeEach(() => {
  vi.spyOn(api, 'listFocusSymbols').mockResolvedValue([])
})
afterEach(() => vi.restoreAllMocks())

const routerLinkStub = {
  props: ['to'],
  template: '<a :data-to="JSON.stringify(to)"><slot /></a>',
}

function observationRun(runId: string, status: string, tradingDate: string, completedAt = `${tradingDate}T23:00:00Z`) {
  return {
    run_id: runId,
    status,
    trading_date: tradingDate,
    completed_at: completedAt,
    created_at: `${tradingDate}T22:00:00Z`,
  } as ObservationRun
}

function discoveryProjection(runId: string, withEvent = true, comparisonStatus: 'ok' | 'unavailable' = 'ok') {
  return {
    schema_version: 'urus.discovery_projection.v1',
    run: { run_id: runId, status: 'succeeded', trading_date: '2026-09-19' },
    comparison: {
      mode: 'previous_trading_session',
      status: comparisonStatus,
      current_trading_date: '2026-09-19',
      previous_trading_date: comparisonStatus === 'ok' ? '2026-09-18' : null,
      previous_trading_dates: comparisonStatus === 'ok' ? ['2026-09-18'] : [],
      compare_run_id: null,
    },
    events: withEvent ? [{
      event_id: 'indicator_transition:event-1',
      kind: 'indicator_transition',
      symbol: 'INTC',
      lens: { id: 'rsi14', name: 'RSI 14', version: 'v1' },
      groups: [{ id: 'semiconductors', name: '半导体' }],
      current: { run_id: runId, trading_date: '2026-09-19', snapshot_ids: ['snapshot-current'], dataset_ids: ['dataset-current'] },
      compare: { run_id: null, trading_date: '2026-09-18', snapshot_ids: ['snapshot-previous'], dataset_ids: ['dataset-previous'] },
      before: { value: 29, display_value: '29.0', state: 'oversold', state_label: '超卖' },
      after: { value: 52, display_value: '52.0', state: 'balanced', state_label: '平衡' },
      transition: { from: 'oversold', to: 'balanced' },
      priority_reason: 'RSI 14 从超卖切换为平衡。',
      route: {
        name: 'instrument-decision',
        params: { symbol: 'INTC' },
        query: { dataset: 'dataset-current', run: runId, discoveryEvent: 'indicator_transition:event-1', lens: 'rsi14' },
      },
    }] : [],
    quality: {
      status: 'ok',
      warnings: [],
      capabilities: { indicator_transition: 'available', strategy_transition: 'deferred', group_phase_change: 'available' },
    },
    content_sha256: 'discovery-hash',
  } as DiscoveryProjection
}

function groupPhaseEvent(runId: string): GroupPhaseChangeEvent {
  return {
    event_id: 'group_phase_change:group-1',
    kind: 'group_phase_change',
    group: { id: 'semiconductors', name: '半导体' },
    groups: [{ id: 'semiconductors', name: '半导体' }],
    current: { run_id: runId, trading_date: '2026-09-19', snapshot_ids: ['group-current'], dataset_ids: ['group-dataset-current'] },
    compare: { run_id: null, trading_date: '2026-09-18', snapshot_ids: ['group-previous'], dataset_ids: ['group-dataset-previous'] },
    before: { value: null, display_value: null, state: 'mixed', state_label: '内部混合' },
    after: { value: null, display_value: null, state: 'broad_strength', state_label: '广泛走强' },
    stage: { id: 'broad_strength_start', label: '参与扩散至广泛强势' },
    metrics: {
      participation: 0.75,
      breadth_ma20: 0.75,
      breadth_ma20_delta: 0.125,
      relative_20d: 1.25,
      relative_20d_delta: 0.5,
      leader_concentration: 0.4,
      valid_symbol_count: 4,
      requested_symbol_count: 5,
      coverage: 0.8,
      leaders_added: ['AMD'],
      leaders_removed: [],
    },
    duration: { status: 'unavailable', label: '持续时长未知' },
    transition: { from: 'mixed', to: 'broad_strength', impulse: 'improving' },
    priority_reason: '半导体：参与扩散至广泛强势。',
    route: {
      name: 'group-observation',
      params: { groupId: 'semiconductors' },
      query: { run: runId, discoveryEvent: 'group_phase_change:group-1' },
    },
  }
}

function mountDiscovery() {
  return mount(DiscoveryView, {
    global: {
      stubs: {
        AppShell: true,
        RouterLink: routerLinkStub,
      },
    },
  })
}

describe('DiscoveryView', () => {
  it('selects the latest completed run without falling back from the latest ready result', async () => {
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([
      observationRun('run-pending', 'running', '2026-09-20'),
      observationRun('run-old-ready', 'succeeded', '2026-09-18'),
      observationRun('run-latest-ready', 'mixed', '2026-09-19'),
    ])
    const getDiscovery = vi.spyOn(api, 'getObservationDiscovery').mockResolvedValue(
      discoveryProjection('run-latest-ready') as DiscoveryProjection,
    )

    const wrapper = mountDiscovery()
    await flushPromises()

    expect(getDiscovery).toHaveBeenCalledOnce()
    expect(getDiscovery).toHaveBeenCalledWith('run-latest-ready')
    expect(wrapper.get('.discovery-run-strip').text()).toContain('2026-09-19')
    expect(wrapper.get('[data-testid="discovery-event-card"]').text()).toContain('INTC')
  })

  it('keeps the frozen run and dataset identity in both discovery links', async () => {
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([observationRun('run-42', 'succeeded', '2026-09-19')])
    vi.spyOn(api, 'getObservationDiscovery').mockResolvedValue(discoveryProjection('run-42') as DiscoveryProjection)

    const wrapper = mountDiscovery()
    await flushPromises()

    const instrumentLink = JSON.parse(wrapper.get('.primary-button').attributes('data-to') || '{}')
    const crossSectionLink = JSON.parse(wrapper.get('.secondary-button').attributes('data-to') || '{}')
    expect(instrumentLink).toMatchObject({
      name: 'instrument-decision',
      params: { symbol: 'INTC' },
      query: { dataset: 'dataset-current', run: 'run-42', discoveryEvent: 'indicator_transition:event-1', lens: 'rsi14' },
    })
    expect(crossSectionLink).toEqual({
      name: 'indicator-cross-section',
      params: { indicatorId: 'rsi14' },
      query: {
        run: 'run-42',
        discoveryEvent: 'indicator_transition:event-1',
        symbol: 'INTC',
        lens: 'rsi14',
      },
    })
  })

  it('renders group phase signals before individual changes and keeps them outside focus filtering', async () => {
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([observationRun('run-mixed', 'succeeded', '2026-09-19')])
    const projection = discoveryProjection('run-mixed')
    projection.quality.status = 'partial'
    projection.events.unshift(groupPhaseEvent('run-mixed'))
    vi.spyOn(api, 'getObservationDiscovery').mockResolvedValue(projection)

    const wrapper = mountDiscovery()
    await flushPromises()

    expect(wrapper.get('.discovery-hero-aside').text()).toContain('板块阶段变化 + 个股指标变化')
    expect(wrapper.get('[data-testid="discovery-partial-quality"]').text()).toContain('部分来源不可比或投影不可用')
    expect(wrapper.findAll('[data-testid="discovery-group-event-card"]')).toHaveLength(1)
    expect(wrapper.get('[aria-label="板块阶段变化"] h2').text()).toBe('板块阶段变化')
    expect(wrapper.get('[data-testid="discovery-group-event-card"]').text()).toContain('参与扩散至广泛强势')
    expect(wrapper.get('[data-testid="discovery-group-event-card"]').text()).toContain('持续时长未知')
    expect(wrapper.get('[data-testid="discovery-group-event-card"]').text()).toContain('4 / 5')
    expect(wrapper.get('[data-testid="discovery-group-event-card"]').text()).toContain('MA20 参与度')
    expect(wrapper.get('[data-testid="discovery-group-event-card"]').text()).toContain('较前次广度变化')
    expect(wrapper.get('[data-testid="discovery-group-event-card"]').text()).toContain('+12.5%')
    expect(wrapper.get('[data-testid="discovery-group-event-card"]').text()).not.toContain('参与度 / MA20 广度')
    expect(wrapper.get('[data-testid="group-evidence-link"]').attributes('data-to')).toContain('group-observation')

    await wrapper.findAll('.discovery-filter-tab')[1].trigger('click')

    expect(wrapper.find('[data-testid="discovery-filter-empty"]').exists()).toBe(true)
    expect(wrapper.findAll('[data-testid="discovery-group-event-card"]')).toHaveLength(1)
  })

  it('switches indicator scope before applying focus filters and keeps counts explicit', async () => {
    vi.spyOn(api, 'listFocusSymbols').mockResolvedValue([{ symbol: 'AMD', created_at: '2026-09-19T00:00:00Z' }])
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([observationRun('run-indicators', 'succeeded', '2026-09-19')])
    const projection = discoveryProjection('run-indicators')
    projection.events.push(
      {
        ...projection.events[0],
        event_id: 'indicator_transition:event-2',
        symbol: 'NVDA',
        route: {
          ...projection.events[0].route,
          params: { symbol: 'NVDA' },
          query: { ...projection.events[0].route.query, discoveryEvent: 'indicator_transition:event-2' },
        },
      },
      {
        ...projection.events[0],
        event_id: 'indicator_transition:event-3',
        symbol: 'AMD',
        lens: { id: 'macd', name: 'MACD', version: 'v1' },
        route: {
          ...projection.events[0].route,
          params: { symbol: 'AMD' },
          query: { ...projection.events[0].route.query, discoveryEvent: 'indicator_transition:event-3', lens: 'macd' },
        },
      },
    )
    vi.spyOn(api, 'getObservationDiscovery').mockResolvedValue(projection)

    const wrapper = mountDiscovery()
    await flushPromises()

    const selector = wrapper.get('[data-testid="discovery-indicator-select"]')
    const indicatorSection = wrapper.get('[aria-label="个股指标变化"]')
    expect((selector.element as HTMLSelectElement).value).toBe('rsi14')
    expect(selector.text()).toContain('RSI 14（2）')
    expect(selector.text()).toContain('MACD（1）')
    expect(wrapper.findAll('[data-testid="discovery-event-card"]')).toHaveLength(2)
    expect(indicatorSection.get('.discovery-section-heading small').text()).toBe('当前展示 2 / 所选指标 2 / 全部指标 3')

    await selector.setValue('macd')
    expect(wrapper.findAll('[data-testid="discovery-event-card"]')).toHaveLength(1)
    expect(wrapper.get('[data-testid="discovery-event-card"]').text()).toContain('AMD')
    expect(indicatorSection.get('.discovery-section-heading small').text()).toBe('当前展示 1 / 所选指标 1 / 全部指标 3')

    await wrapper.findAll('.discovery-filter-tab')[1].trigger('click')
    expect(wrapper.findAll('[data-testid="discovery-event-card"]')).toHaveLength(1)

    await selector.setValue('__all__')
    expect(wrapper.findAll('[data-testid="discovery-event-card"]')).toHaveLength(1)
    expect(indicatorSection.get('.discovery-section-heading small').text()).toBe('当前展示 1 / 所选指标 3 / 全部指标 3')

    await wrapper.findAll('.discovery-filter-tab')[0].trigger('click')
    expect(wrapper.findAll('[data-testid="discovery-event-card"]')).toHaveLength(3)
    expect(indicatorSection.get('.discovery-section-heading small').text()).toBe('当前展示 3 / 所选指标 3 / 全部指标 3')
  })

  it('shows separate empty states for missing group and indicator signals', async () => {
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([observationRun('run-group-only', 'succeeded', '2026-09-19')])
    const groupOnly = discoveryProjection('run-group-only', false)
    groupOnly.events.push(groupPhaseEvent('run-group-only'))
    vi.spyOn(api, 'getObservationDiscovery').mockResolvedValue(groupOnly)

    const groupWrapper = mountDiscovery()
    await flushPromises()
    expect(groupWrapper.get('[data-testid="discovery-no-indicator-events"]').text()).toContain('本次没有个股指标状态变化')
    expect(groupWrapper.find('[data-testid="discovery-group-event-card"]').exists()).toBe(true)

    vi.restoreAllMocks()
    vi.spyOn(api, 'listFocusSymbols').mockResolvedValue([])
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([observationRun('run-indicator-only', 'succeeded', '2026-09-19')])
    vi.spyOn(api, 'getObservationDiscovery').mockResolvedValue(discoveryProjection('run-indicator-only'))

    const indicatorWrapper = mountDiscovery()
    await flushPromises()
    expect(indicatorWrapper.get('[data-testid="discovery-no-group-events"]').text()).toContain('本次没有满足样本门槛的板块阶段变化')
    expect(indicatorWrapper.find('[data-testid="discovery-event-card"]').exists()).toBe(true)
  })

  it('distinguishes no comparable history from a failed request', async () => {
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([observationRun('run-no-history', 'succeeded', '2026-09-19')])
    vi.spyOn(api, 'getObservationDiscovery').mockResolvedValue(
      discoveryProjection('run-no-history', false, 'unavailable') as DiscoveryProjection,
    )

    const noHistoryWrapper = mountDiscovery()
    await flushPromises()
    expect(noHistoryWrapper.get('[data-testid="discovery-no-events"]').text()).toContain('暂无可比历史')
    expect(noHistoryWrapper.get('[data-testid="discovery-no-events"]').text()).not.toContain('本次没有指标状态变化')

    vi.restoreAllMocks()
    vi.spyOn(api, 'listObservationRuns').mockRejectedValue(new Error('observation service unavailable'))
    const failureWrapper = mountDiscovery()
    await flushPromises()
    expect(failureWrapper.get('[data-testid="discovery-error"]').text()).toContain('发现数据加载失败')
    expect(failureWrapper.get('[data-testid="discovery-error"]').text()).toContain('observation service unavailable')
    expect(failureWrapper.find('[data-testid="discovery-no-run"]').exists()).toBe(false)
  })

  it('filters only the current discovery events and makes an empty focus filter explicit', async () => {
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([observationRun('run-filter', 'succeeded', '2026-09-19')])
    const projection = discoveryProjection('run-filter')
    projection.events.push({
      ...projection.events[0],
      event_id: 'indicator_transition:event-2',
      symbol: 'AMD',
      lens: { id: 'macd', name: 'MACD', version: 'v1' },
      route: {
        ...projection.events[0].route,
        params: { symbol: 'AMD' },
        query: { ...projection.events[0].route.query, discoveryEvent: 'indicator_transition:event-2', lens: 'macd' },
      },
    })
    vi.spyOn(api, 'getObservationDiscovery').mockResolvedValue(projection)

    const wrapper = mountDiscovery()
    await flushPromises()
    await wrapper.get('[data-testid="discovery-indicator-select"]').setValue('__all__')
    const filterButtons = wrapper.findAll('.discovery-filter-tab')

    await filterButtons[1].trigger('click')
    expect(wrapper.get('[data-testid="discovery-filter-empty"]').text()).toContain('当前还没有个人重点关注记录')

    await filterButtons[0].trigger('click')
    expect(wrapper.findAll('[data-testid="discovery-event-card"]')).toHaveLength(2)
    await filterButtons[2].trigger('click')
    expect(wrapper.findAll('[data-testid="discovery-event-card"]')).toHaveLength(2)
  })

  it('separates focused and unfocused events without changing the discovery payload', async () => {
    vi.spyOn(api, 'listFocusSymbols').mockResolvedValue([{ symbol: 'INTC', created_at: '2026-09-19T00:00:00Z' }])
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([observationRun('run-split', 'succeeded', '2026-09-19')])
    const projection = discoveryProjection('run-split')
    projection.events.push({
      ...projection.events[0],
      event_id: 'indicator_transition:event-2',
      symbol: 'AMD',
      route: {
        ...projection.events[0].route,
        params: { symbol: 'AMD' },
        query: { ...projection.events[0].route.query, discoveryEvent: 'indicator_transition:event-2' },
      },
    })
    vi.spyOn(api, 'getObservationDiscovery').mockResolvedValue(projection)

    const wrapper = mountDiscovery()
    await flushPromises()
    const filterButtons = wrapper.findAll('.discovery-filter-tab')

    await filterButtons[1].trigger('click')
    expect(wrapper.findAll('[data-testid="discovery-event-card"]')).toHaveLength(1)
    expect(wrapper.get('[data-testid="discovery-event-card"]').text()).toContain('INTC')
    await filterButtons[2].trigger('click')
    expect(wrapper.findAll('[data-testid="discovery-event-card"]')).toHaveLength(1)
    expect(wrapper.get('[data-testid="discovery-event-card"]').text()).toContain('AMD')
    expect(wrapper.get('[aria-label="个股指标变化"] .discovery-section-heading small').text()).toBe('当前展示 1 / 所选指标 2 / 全部指标 2')
  })

  it('keeps focus state unknown after a failed GET and restores filters after retry', async () => {
    const listFocus = vi.spyOn(api, 'listFocusSymbols')
      .mockRejectedValueOnce(new Error('focus read failed'))
      .mockResolvedValueOnce([{ symbol: 'INTC', created_at: '2026-09-19T00:00:00Z' }])
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([observationRun('run-unknown', 'succeeded', '2026-09-19')])
    const projection = discoveryProjection('run-unknown')
    projection.events.push({
      ...projection.events[0],
      event_id: 'indicator_transition:event-2',
      symbol: 'AMD',
    })
    vi.spyOn(api, 'getObservationDiscovery').mockResolvedValue(projection)
    const addFocus = vi.spyOn(api, 'addFocusSymbol')

    const wrapper = mountDiscovery()
    await flushPromises()

    expect(listFocus).toHaveBeenCalledOnce()
    expect(wrapper.findAll('[data-testid="discovery-event-card"]')).toHaveLength(2)
    expect(wrapper.get('.discovery-focus-status').text()).toContain('关注状态未知')
    expect(wrapper.find('[data-testid="focus-toggle"]').exists()).toBe(false)
    expect(wrapper.findAll('.discovery-filter-tab')[1].attributes('disabled')).toBeDefined()
    expect(wrapper.findAll('.discovery-filter-tab')[2].attributes('disabled')).toBeDefined()
    expect(addFocus).not.toHaveBeenCalled()

    await wrapper.get('[data-testid="discovery-focus-error"] button').trigger('click')
    await flushPromises()

    expect(listFocus).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-testid="discovery-focus-error"]').exists()).toBe(false)
    expect(wrapper.get('.discovery-focus-status').text()).toContain('重点关注')
    expect(wrapper.findAll('.discovery-filter-tab')[1].attributes('disabled')).toBeUndefined()
    await wrapper.findAll('.discovery-filter-tab')[1].trigger('click')
    expect(wrapper.findAll('[data-testid="discovery-event-card"]')).toHaveLength(1)
    expect(wrapper.get('[data-testid="discovery-event-card"]').text()).toContain('INTC')
  })

  it('updates focus state only after success and keeps it unchanged on failure', async () => {
    vi.spyOn(api, 'listObservationRuns').mockResolvedValue([observationRun('run-focus', 'succeeded', '2026-09-19')])
    vi.spyOn(api, 'getObservationDiscovery').mockResolvedValue(discoveryProjection('run-focus'))
    const addFocus = vi.spyOn(api, 'addFocusSymbol').mockRejectedValue(new Error('focus write failed'))

    const wrapper = mountDiscovery()
    await flushPromises()
    const toggle = wrapper.get('[data-testid="focus-toggle"]')
    await toggle.trigger('click')
    await flushPromises()

    expect(addFocus).toHaveBeenCalledWith('INTC')
    expect(toggle.text()).toContain('加入重点')
    expect(wrapper.get('.discovery-focus-status').text()).toContain('未重点')
    expect(wrapper.get('[data-testid="discovery-focus-error"]').text()).toContain('focus write failed')

    addFocus.mockResolvedValue({ symbol: 'INTC', created_at: '2026-09-19T00:00:00Z' })
    await wrapper.get('[data-testid="discovery-focus-error"] button').trigger('click')
    await flushPromises()
    expect(toggle.text()).toContain('移出重点')
    expect(wrapper.get('.discovery-focus-status').text()).toContain('重点关注')

    const removeFocus = vi.spyOn(api, 'removeFocusSymbol').mockResolvedValue({ symbol: 'INTC', removed: true })
    await toggle.trigger('click')
    await flushPromises()
    expect(removeFocus).toHaveBeenCalledWith('INTC')
    expect(toggle.text()).toContain('加入重点')
  })
})
