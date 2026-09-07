import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import OpportunityViewPanel from '@/components/decision/OpportunityViewPanel.vue'
import type {
  DailyOpportunityViewResponse,
  OpportunityView,
  OpportunityViewRow,
} from '@/types/opportunityView'

afterEach(() => vi.restoreAllMocks())

function opportunity(
  datasetId = 'dataset-1',
  symbol = 'INTC',
  overrides: Partial<OpportunityView> = {},
): OpportunityView {
  return {
    schema_version: 'urus.buy_opportunity.view.v1',
    view_id: 'setup-1:view:abc',
    dataset_id: datasetId,
    scan_id: 'scan-2026-08-21-very-long-technical-identifier',
    setup_id: 'setup-1',
    trigger_id: 'trigger-1',
    symbol,
    family_version: 's1.v1',
    rules_hash: 'a'.repeat(64),
    viewed_at: '2026-08-22T05:30:00Z',
    knowledge_at: '2026-08-22T05:00:00Z',
    market_as_of: '2026-08-21',
    source_mode: 'historical_reconstruction',
    data_source: 'real',
    price_basis: 'adjusted',
    price_mapping_id: null,
    quality: 'partial',
    quality_warnings: ['事件日历覆盖有限'],
    freshness: 'unknown',
    status: 'triggered_unverified',
    upper_trigger: 100,
    invalidation_stop: 95,
    atr: 4,
    upper_limit: 110,
    reference_price: 104,
    reference_kind: 'daily_close',
    reference_quote: null,
    distance_to_trigger_atr: -1,
    condition: '等待后续证据确认价格条件。',
    invalidation: '触及 S 即失效。',
    event_risk: { status: 'unknown', codes: [], known_events: [], horizon_sessions: null },
    research_support: 'unknown',
    reason_codes: ['daily_reference_is_not_current_quote'],
    evidence_refs: [],
    content_sha256: 'b'.repeat(64),
    ...overrides,
  }
}

function row(
  datasetId = 'dataset-1',
  symbol = 'INTC',
  view: OpportunityView | null = opportunity(datasetId, symbol),
): OpportunityViewRow {
  return {
    symbol,
    status: view?.status ?? 'unavailable',
    scan: { scan_id: view?.scan_id ?? 'scan-rejected', status: view ? 'eligible' : 'rejected', rejection_codes: view ? [] : ['coverage_miss'] },
    opportunity: view,
    reference: { price: view?.reference_price ?? null, kind: view?.reference_kind === 'daily_close' ? 'daily_close' : 'none', market_as_of: view?.market_as_of ?? null },
    quality: { status: view?.quality ?? 'unavailable', warnings: view ? ['事件日历覆盖有限'] : ['没有足够的结构证据'] },
    reason_codes: view?.reason_codes ?? ['coverage_miss'],
  }
}

function response(datasetId = 'dataset-1', rows: OpportunityViewRow[] = [row(datasetId)]): DailyOpportunityViewResponse {
  return {
    schema_version: 'urus.daily_opportunity_view_response.v1',
    dataset_id: datasetId,
    viewed_at: '2026-08-22T05:30:00Z',
    knowledge_at: '2026-08-22T05:00:00Z',
    views: rows,
    warnings: [],
  }
}

describe('OpportunityViewPanel', () => {
  it('puts the latest close and a plain-language conclusion before the current plan', async () => {
    const getView = vi.spyOn(api, 'getDailyOpportunityView').mockResolvedValue(response())
    const wrapper = mount(OpportunityViewPanel, { props: { datasetId: 'dataset-1', symbol: 'INTC' } })
    await flushPromises()

    expect(getView).toHaveBeenCalledWith('dataset-1', 'INTC', expect.any(AbortSignal))
    expect(wrapper.text()).toContain('当前日线机会')
    expect(wrapper.text()).toContain('截至 2026-08-21 收盘')
    expect(wrapper.text()).toContain('突破条件待验证')
    expect(wrapper.text()).toContain('突破待验证')
    expect(wrapper.text()).toContain('日线参考价')
    expect(wrapper.text()).toContain('104.00')
    expect(wrapper.text()).toContain('100.00 → 110.00')
    expect(wrapper.text()).toContain('95.00')
    expect(wrapper.text()).not.toContain('收益研究支持')
    expect(wrapper.text()).not.toContain('事件日历覆盖有限')
    expect(wrapper.find('[data-testid="opportunity-view-current-plan"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="opportunity-view-history"]').exists()).toBe(false)
    expect(wrapper.find('.opportunity-view-trace').element.open).toBe(false)
  })

  it('moves an invalidated setup into a collapsed history and removes its old range from the current card', async () => {
    const historical = opportunity('dataset-1', 'INTC', {
      status: 'invalidated',
      setup_observed_at: '2026-08-13',
      setup_valid_from: '2026-08-14',
      setup_invalidated_at: '2026-08-19',
      quality_warnings: ['event_coverage_unknown', 'historical_reconstruction', 'per_bar_session_id_unavailable'],
    })
    vi.spyOn(api, 'getDailyOpportunityView').mockResolvedValue(response('dataset-1', [row('dataset-1', 'INTC', historical)]))
    const wrapper = mount(OpportunityViewPanel, { props: { datasetId: 'dataset-1', symbol: 'INTC' } })
    await flushPromises()

    expect(wrapper.get('[data-testid="opportunity-view-current-summary"]').text()).toContain('当前无有效突破计划')
    expect(wrapper.get('[data-testid="opportunity-view-current-summary"]').text()).toContain('当前没有符合该策略的候选')
    expect(wrapper.get('[data-testid="opportunity-view-status"]').text()).toBe('历史计划已失效')
    expect(wrapper.findAll('strong').filter((item) => item.text() === '当前无有效突破计划')).toHaveLength(1)
    expect(wrapper.text().match(/当前无有效突破计划/g)).toHaveLength(1)
    expect(wrapper.find('[data-testid="opportunity-view-no-current-plan"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="opportunity-view-current-plan"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="opportunity-view-history"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="opportunity-view-history"]').element.open).toBe(false)

    const history = wrapper.get('[data-testid="opportunity-view-history"]')
    expect(history.text()).toContain('历史计划与复盘')
    expect(history.text()).toContain('形成日')
    expect(history.text()).toContain('2026-08-13')
    expect(history.text()).toContain('生效日')
    expect(history.text()).toContain('2026-08-14')
    expect(history.text()).toContain('结束日')
    expect(history.text()).toContain('2026-08-19')
    expect(history.text()).toContain('当时的计划区间')
    expect(history.text()).toContain('100.00 → 110.00')
    expect(history.text()).toContain('失效')
    expect(wrapper.find('[data-testid="opportunity-view-quality"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('收益研究支持')
    expect(wrapper.text()).not.toContain('event_coverage_unknown')
    expect(wrapper.text()).not.toContain('historical_reconstruction')
    expect(wrapper.text()).not.toContain('per_bar_session_id_unavailable')
  })

  it('labels a preparing setup as waiting for breakout confirmation', async () => {
    const preparing = opportunity('dataset-1', 'INTC', { status: 'preparing', trigger_id: null })
    vi.spyOn(api, 'getDailyOpportunityView').mockResolvedValue(response('dataset-1', [row('dataset-1', 'INTC', preparing)]))
    const wrapper = mount(OpportunityViewPanel, { props: { datasetId: 'dataset-1', symbol: 'INTC' } })
    await flushPromises()

    expect(wrapper.text()).toContain('等待突破确认')
    expect(wrapper.find('[data-testid="opportunity-view-current-plan"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="opportunity-view-history"]').exists()).toBe(false)
  })

  it('scopes visible price evidence warnings to the selected symbol and hides event/provenance metadata', async () => {
    const payload = response()
    payload.warnings = [
      'daily_close_older_than_current_session',
      'event_coverage_unknown',
      'SPY:daily_close_older_than_current_session',
      'INTC:historical_reconstruction',
      'AMD:per_bar_session_id_unavailable',
    ]
    vi.spyOn(api, 'getDailyOpportunityView').mockResolvedValue(payload)
    const wrapper = mount(OpportunityViewPanel, { props: { datasetId: 'dataset-1', symbol: 'INTC' } })
    await flushPromises()

    const quality = wrapper.get('[data-testid="opportunity-view-quality"]')
    expect(quality.text()).toContain('daily_close_older_than_current_session')
    expect(quality.text()).not.toContain('event_coverage_unknown')
    expect(quality.text()).not.toContain('historical_reconstruction')
    expect(quality.text()).not.toContain('per_bar_session_id_unavailable')
    expect(quality.text()).toContain('另有 1 个标的存在同类限制')
  })

  it('renders an unavailable reason without inventing a price when opportunity is null', async () => {
    const getView = vi.spyOn(api, 'getDailyOpportunityView').mockResolvedValue(response('dataset-1', [row('dataset-1', 'INTC', null)]))
    const wrapper = mount(OpportunityViewPanel, { props: { datasetId: 'dataset-1', symbol: 'INTC' } })
    await flushPromises()

    expect(getView).toHaveBeenCalledTimes(1)
    expect(wrapper.get('[data-testid="opportunity-view-current-summary"]').text()).toContain('证据覆盖不足')
    expect(wrapper.find('[data-testid="opportunity-view-no-current-plan"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('当前没有符合该突破策略的有效候选')
    expect(wrapper.text()).not.toContain('100.00')
    expect(wrapper.text()).not.toContain('当前可买')
  })

  it('surfaces a read failure as an error state', async () => {
    vi.spyOn(api, 'getDailyOpportunityView').mockRejectedValue(new Error('服务暂时不可用'))
    const wrapper = mount(OpportunityViewPanel, { props: { datasetId: 'dataset-1', symbol: 'INTC' } })
    await flushPromises()

    expect(wrapper.find('[data-testid="opportunity-view-error"]').text()).toContain('服务暂时不可用')
    expect(wrapper.get('[data-testid="opportunity-view-status"]').text()).toBe('读取失败')
  })

  it('aborts the previous request and keeps a late response from replacing the current symbol view', async () => {
    let resolveFirst!: (value: DailyOpportunityViewResponse) => void
    let resolveSecond!: (value: DailyOpportunityViewResponse) => void
    const first = new Promise<DailyOpportunityViewResponse>((resolve) => { resolveFirst = resolve })
    const second = new Promise<DailyOpportunityViewResponse>((resolve) => { resolveSecond = resolve })
    const getView = vi.spyOn(api, 'getDailyOpportunityView').mockImplementation((datasetId) => datasetId === 'dataset-a' ? first : second)
    const wrapper = mount(OpportunityViewPanel, { props: { datasetId: 'dataset-a', symbol: 'INTC' } })
    await wrapper.setProps({ datasetId: 'dataset-b', symbol: 'AMD' })

    const firstSignal = getView.mock.calls[0]?.[2]
    expect(firstSignal?.aborted).toBe(true)
    resolveSecond(response('dataset-b', [row('dataset-b', 'AMD', opportunity('dataset-b', 'AMD'))]))
    await flushPromises()
    expect(wrapper.text()).toContain('AMD')
    expect(wrapper.text()).not.toContain('INTC')

    resolveFirst(response('dataset-a', [row('dataset-a', 'INTC')]))
    await flushPromises()
    expect(getView).toHaveBeenNthCalledWith(1, 'dataset-a', 'INTC', expect.any(AbortSignal))
    expect(getView).toHaveBeenNthCalledWith(2, 'dataset-b', 'AMD', expect.any(AbortSignal))
    expect(wrapper.text()).toContain('AMD')
  })

  it('does not request without a dataset and clears a disabled view', async () => {
    const getView = vi.spyOn(api, 'getDailyOpportunityView')
    const wrapper = mount(OpportunityViewPanel, { props: { symbol: 'INTC' } })
    await flushPromises()

    expect(getView).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="opportunity-view-unknown"]').text()).toContain('暂无冻结 Daily Dataset')

    await wrapper.setProps({ datasetId: 'dataset-1', disabled: true })
    await flushPromises()
    expect(getView).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="opportunity-view-unknown"]').text()).toContain('当前证据尚未就绪或为演示数据')
  })

  it('aborts an in-flight request when unmounted', async () => {
    let resolve!: (value: DailyOpportunityViewResponse) => void
    const pending = new Promise<DailyOpportunityViewResponse>((done) => { resolve = done })
    const getView = vi.spyOn(api, 'getDailyOpportunityView').mockReturnValue(pending)
    const wrapper = mount(OpportunityViewPanel, { props: { datasetId: 'dataset-1', symbol: 'INTC' } })
    await flushPromises()
    const signal = getView.mock.calls[0]?.[2]

    wrapper.unmount()
    expect(signal?.aborted).toBe(true)
    resolve(response())
  })
})
