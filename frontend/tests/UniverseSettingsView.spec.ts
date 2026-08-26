import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import type { UniverseResponse } from '@/types/universe'
import UniverseSettingsView from '@/views/UniverseSettingsView.vue'

const universe: UniverseResponse = {
  version_id: 'universe-v1', revision: 1, content_sha256: 'a'.repeat(64), source: 'runtime', created_at: '2026-08-13T00:00:00Z',
  items: [
    {
      symbol: 'QQQ', display_name: 'QQQ', asset_type: 'market', theme: '科技大盘', enabled: true,
      roles: { market_benchmark: true, equity_watchlist: false, cta_proxy: true, options_collection: true, event_tracking: false, ai_candidate: true },
      benchmarks: { relative_strength: null, cta_proxy_for: 'NQ' }, collection: { quote: true, daily_history: true, options: true }, notes: '',
    },
    {
      symbol: 'AAPL', display_name: 'Apple', asset_type: 'equity', theme: '大型科技', enabled: true,
      roles: { market_benchmark: false, equity_watchlist: true, cta_proxy: false, options_collection: true, event_tracking: true, ai_candidate: true },
      benchmarks: { relative_strength: 'QQQ', cta_proxy_for: null }, collection: { quote: true, daily_history: true, options: true }, notes: '',
    },
  ],
  derived: { market_symbols: ['QQQ'], instrument_symbols: ['AAPL'], cta_proxy_symbols: ['QQQ'], option_symbols: ['QQQ', 'AAPL'], event_symbols: ['AAPL'], ai_candidate_symbols: ['QQQ', 'AAPL'] },
}

afterEach(() => vi.restoreAllMocks())

describe('UniverseSettingsView', () => {
  it('removes a symbol only from the next saved version', async () => {
    vi.spyOn(api, 'getUniverse').mockResolvedValue(structuredClone(universe))
    const save = vi.spyOn(api, 'updateUniverse').mockResolvedValue({ ...structuredClone(universe), revision: 2, items: [universe.items[0]] })
    const wrapper = mount(UniverseSettingsView, { global: { stubs: { AppShell: true, RouterLink: true } } })
    await flushPromises()

    const row = wrapper.findAll('tbody tr').find((entry) => entry.text().includes('AAPL'))
    await row!.get('button[aria-label="删除标的"]').trigger('click')
    expect(wrapper.text()).toContain('历史版本、已完成报告和正在运行的任务仍保留原配置')
    await wrapper.get('.danger-button').trigger('click')
    expect(wrapper.text()).not.toContain('Apple')

    await wrapper.get('.universe-actions .primary-button').trigger('click')
    await wrapper.find('.confirm-card .primary-button').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledWith(expect.objectContaining({
      base_version_id: 'universe-v1',
      items: [expect.objectContaining({ symbol: 'QQQ' })],
    }))
  })

  it('protects the required QQQ benchmark from deletion', async () => {
    vi.spyOn(api, 'getUniverse').mockResolvedValue(structuredClone(universe))
    const wrapper = mount(UniverseSettingsView, { global: { stubs: { AppShell: true, RouterLink: true } } })
    await flushPromises()
    await wrapper.findAll('tbody tr')[0].get('button[aria-label="删除标的"]').trigger('click')
    expect(wrapper.text()).toContain('QQQ 是当前相对强弱算法的固定基准，不能删除')
    expect(wrapper.find('.danger-button').exists()).toBe(false)
  })

  it('adds and removes cross-cutting themes without changing the primary label unexpectedly', async () => {
    vi.spyOn(api, 'getUniverse').mockResolvedValue(structuredClone(universe))
    const save = vi.spyOn(api, 'updateUniverse').mockResolvedValue(structuredClone(universe))
    const wrapper = mount(UniverseSettingsView, { global: { stubs: { AppShell: true, RouterLink: true } } })
    await flushPromises()

    await wrapper.findAll('tbody tr')[1].trigger('click')
    const input = wrapper.find('.theme-add-row input')
    await input.setValue('AI 基础设施')
    await wrapper.get('.theme-add-row button').trigger('click')
    expect(wrapper.find('.theme-chip-list').text()).toContain('大型科技')
    expect(wrapper.find('.theme-chip-list').text()).toContain('AI 基础设施')

    await wrapper.find('button[aria-label="移除主题 大型科技"]').trigger('click')
    expect(wrapper.find('.theme-chip-list').text()).toContain('AI 基础设施')
    await wrapper.get('.universe-actions .primary-button').trigger('click')
    await wrapper.find('.confirm-card .primary-button').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledWith(expect.objectContaining({
      items: expect.arrayContaining([
        expect.objectContaining({ symbol: 'AAPL', theme: 'AI 基础设施', themes: ['AI 基础设施'] }),
      ]),
    }))
  })

  it('filters by watchlist while keeping indicator recommendation membership read-only', async () => {
    vi.spyOn(api, 'getUniverse').mockResolvedValue(structuredClone(universe))
    const wrapper = mount(UniverseSettingsView, { global: { stubs: { AppShell: true, RouterLink: true } } })
    await flushPromises()

    const coreButton = wrapper.findAll('.watchlist-tabs button').find((button) => button.text().startsWith('指标推荐'))
    expect(coreButton).toBeTruthy()
    await coreButton!.trigger('click')
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
    await wrapper.find('tbody tr').trigger('click')
    expect(wrapper.find('input[aria-label="核心关注列表"]').exists()).toBe(false)
    expect(wrapper.find('.protected-role').text()).toContain('已由部署 Universe 纳入')
    await wrapper.get('button[aria-label="关闭"]').trigger('click')

    const listButton = wrapper.findAll('.watchlist-tabs button').find((button) => button.text().startsWith('大型科技'))
    expect(listButton).toBeTruthy()
    await listButton!.trigger('click')
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
    expect(wrapper.find('tbody').text()).toContain('AAPL')

    await wrapper.find('tbody tr').trigger('click')
    await wrapper.get('.theme-add-row input').setValue('AI 基础设施')
    await wrapper.get('.theme-add-row button').trigger('click')
    const membership = wrapper.findAll('.list-check').find((label) => label.text().includes('大型科技'))
    expect(membership).toBeTruthy()
    await membership!.get('input').setValue(false)
    expect(wrapper.find('.theme-chip-list').text()).not.toContain('大型科技')
  })
})
