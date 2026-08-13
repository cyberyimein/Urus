import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import ResearchReportsView from '@/views/ResearchReportsView.vue'
import type { ResearchReportIndex } from '@/types/research'

const report: ResearchReportIndex = {
  report_id: 'report-1',
  session_id: 'report-1',
  workflow_run_id: 'workflow-1',
  dataset_key: 'manual-analysis:test',
  cutoff_time: '2026-08-13T06:00:00Z',
  status: 'succeeded',
  policy: {},
  technical_report_schema_version: 'urus.technical_report.v1',
  decision_report_schema_version: 'urus.ai_decision_report.v5',
  equity_decision_run_id: 'decision-run-1',
  error_code: null,
  error_message: null,
  started_at: '2026-08-13T06:00:00Z',
  completed_at: '2026-08-13T06:01:00Z',
  created_at: '2026-08-13T06:00:00Z',
  trigger_type: 'manual',
  official_cycle: false,
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
}

afterEach(() => vi.restoreAllMocks())

describe('ResearchReportsView', () => {
  it('requires a second confirmation before deleting a report', async () => {
    vi.spyOn(api, 'listAllResearchReports').mockResolvedValue([report])
    const remove = vi.spyOn(api, 'deleteResearchReport').mockResolvedValue({ report_id: report.report_id, deleted: true })
    const wrapper = mount(ResearchReportsView, {
      global: {
        stubs: {
          AppShell: true,
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('手动 · 当前状态')
    await wrapper.get('.report-delete-button').trigger('click')
    expect(wrapper.text()).toContain('删除报告及 AI 轨迹，不删除采集数据')
    expect(remove).not.toHaveBeenCalled()

    await wrapper.get('.danger-button').trigger('click')
    await flushPromises()
    expect(remove).toHaveBeenCalledWith(report.report_id)
    expect(wrapper.text()).toContain('还没有历史报告。')
  })
})
