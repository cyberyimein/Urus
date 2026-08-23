import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import InstrumentDecisionView from '@/views/InstrumentDecisionView.vue'
import { router } from '@/router'

afterEach(() => vi.restoreAllMocks())

describe('InstrumentDecisionView', () => {
  it('keeps the evidence workspace useful in local demo mode without disguising the data state', async () => {
    vi.spyOn(api, 'createDailyDataset').mockRejectedValue(new Error('backend offline'))
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
    expect(wrapper.text()).toContain('不会把 RSI 数字伪装成买卖信号')
    expect(wrapper.text()).toContain('美股大盘')
    expect(wrapper.text()).toContain('半导体')
    expect(wrapper.text()).toContain('本次分析证据')
    expect(wrapper.text()).toContain('决策检查清单')

    const sectorGroups = wrapper.findAll('.watch-group-toggle')
    expect(sectorGroups).toHaveLength(2)
    expect(sectorGroups[0].attributes('aria-expanded')).toBe('true')
    await sectorGroups[0].trigger('click')
    expect(sectorGroups[0].attributes('aria-expanded')).toBe('false')
  })
})
