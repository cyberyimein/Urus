import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import SettingsView from '@/views/SettingsView.vue'
import type { RuntimeSettingsResponse } from '@/types/settings'

const payload: RuntimeSettingsResponse = {
  revision: 0,
  source: 'environment',
  updated_at: null,
  schedule: {
    pre_market: { enabled: true, skip_ai_decision: false },
    pre_close: { enabled: true, skip_ai_decision: true },
    post_close_review: { enabled: true, skip_ai_decision: false },
  },
  models: {
    ai_decision_model: 'deepseek/deepseek-v4-flash-0731',
    anomalo_retrieval_agent: 'scheduled-event-investigator',
    input_cost_per_million: 0,
    cached_input_cost_per_million: 0,
    cache_write_cost_per_million: 0,
    output_cost_per_million: 0,
  },
  notes: {
    anomalo_model_control: 'preset_agent',
    anomalo_model_note: '模型由 Anomalo 预设 Agent 配置。',
    credentials_note: '凭据由环境变量管理。',
  },
  capabilities: {
    ai_decision_enabled: true,
    openrouter_configured: true,
    provider: 'openrouter',
  },
}

afterEach(() => vi.restoreAllMocks())

describe('SettingsView', () => {
  it('loads the three schedule controls and keeps tail AI disabled', async () => {
    vi.spyOn(api, 'getSettings').mockResolvedValue(payload)
    const wrapper = mount(SettingsView, { global: { stubs: { AppShell: true, RouterLink: true } } })
    await flushPromises()

    expect(wrapper.text()).toContain('盘前正式决策')
    expect(wrapper.text()).toContain('收盘复盘')
    expect(wrapper.text()).toContain('尾盘数据采集')
    expect(wrapper.text()).toContain('缓存读取价格')
    const tailCard = wrapper.findAll('.schedule-card').find((card) => card.text().includes('尾盘数据采集'))
    expect(tailCard?.findAll('input')[1].element.disabled).toBe(true)
    expect(tailCard?.findAll('input')[1].element.checked).toBe(false)
    expect(tailCard?.text()).toContain('AI 已关闭（只采集）')
  })

  it('saves schedule and model changes with the current revision', async () => {
    vi.spyOn(api, 'getSettings').mockResolvedValue(payload)
    const save = vi.spyOn(api, 'updateSettings').mockResolvedValue({
      ...payload,
      revision: 1,
      source: 'runtime',
      updated_at: '2026-08-13T00:00:00Z',
    })
    const wrapper = mount(SettingsView, { global: { stubs: { AppShell: true, RouterLink: true } } })
    await flushPromises()

    const checkboxes = wrapper.findAll('input[type="checkbox"]')
    await checkboxes[0].setValue(false)
    const modelInput = wrapper.find('input[type="text"]')
    await modelInput.setValue('openai/gpt-oss-120b')
    const priceInputs = wrapper.findAll('input[type="number"]')
    await priceInputs[0].setValue('2.5')
    await priceInputs[1].setValue('0.25')
    await priceInputs[2].setValue('10')
    await priceInputs[3].setValue('12')
    expect(wrapper.text()).toContain('有未保存修改')

    await wrapper.get('button.primary-button').trigger('click')
    await flushPromises()

    expect(save).toHaveBeenCalledWith(expect.objectContaining({
      revision: 0,
      schedule: expect.objectContaining({
        pre_market: { enabled: false, skip_ai_decision: false },
      }),
      models: expect.objectContaining({
        ai_decision_model: 'openai/gpt-oss-120b',
        input_cost_per_million: 2.5,
        cached_input_cost_per_million: 0.25,
        cache_write_cost_per_million: 10,
        output_cost_per_million: 12,
      }),
    }))
    expect(wrapper.text()).toContain('设置已保存')
  })
})
