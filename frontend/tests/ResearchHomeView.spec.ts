import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import ResearchHomeView from '@/views/ResearchHomeView.vue'
import type { ResearchReportIndex } from '@/types/research'

const report = (id: string, cutoff: string, trigger: 'manual' | 'scheduled'): ResearchReportIndex => ({
  report_id: id,
  session_id: id,
  workflow_run_id: `workflow-${id}`,
  dataset_key: `${trigger}-analysis:test`,
  cutoff_time: cutoff,
  status: 'succeeded',
  policy: {},
  technical_report_schema_version: 'urus.technical_report.v1',
  decision_report_schema_version: 'urus.ai_decision_report.v5',
  equity_decision_run_id: `decision-${id}`,
  error_code: null,
  error_message: null,
  started_at: cutoff,
  completed_at: cutoff,
  created_at: cutoff,
  trigger_type: trigger,
  official_cycle: trigger !== 'manual',
  trading_date: '2026-08-13',
  run_summary: {
    run_count: 1,
    tool_call_count: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    duration_ms: 1000,
    providers: [],
    models: [],
    skill_hashes: [],
    statuses: ['succeeded'],
  },
})

afterEach(() => vi.restoreAllMocks())

describe('ResearchHomeView', () => {
  it('focuses the research center on opening reports instead of explanatory cards', async () => {
    vi.spyOn(api, 'listAllResearchReports').mockResolvedValue([
      report('latest', '2026-08-13T06:00:00Z', 'manual'),
      report('older', '2026-08-12T06:00:00Z', 'scheduled'),
    ])
    const wrapper = mount(ResearchHomeView, {
      global: {
        stubs: {
          AppShell: true,
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('研究中心')
    expect(wrapper.text()).toContain('最近更新')
    expect(wrapper.text()).toContain('手动分析')
    expect(wrapper.text()).toContain('开发工具')
    expect(wrapper.text()).not.toContain('其他报告')
    expect(wrapper.findAll('.report-card')).toHaveLength(0)
  })
})
