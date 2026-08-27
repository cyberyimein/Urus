import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import RemoteDecisionPanel from '@/components/decision/RemoteDecisionPanel.vue'
import { router } from '@/router'
import type { RemoteDecisionRun } from '@/types/remoteDecision'

const run = {
  local_run_id: 'run-instrument-1',
  anomalo_run_id: 'anomalo-1',
  intent_type: 'instrument_arbitration',
  request_intent_id: 'request-1',
  idempotency_key: 'key-1',
  scope_type: 'instrument',
  scope_id: 'INTC',
  scope_version: null,
  dataset_id: 'dataset-1',
  lens_type: null,
  lens_id: null,
  lens_version: null,
  source: { dataset_id: 'dataset-1', symbol: 'INTC', content_sha256: 'a'.repeat(64) },
  workflow_ref: 'urus-instrument-arbitration@3',
  input_schema_version: 'urus.remote_decision_input.v1',
  input_sha256: 'b'.repeat(64),
  status: 'accepted',
  remote_status: 'succeeded',
  validation_status: 'accepted',
  latest_event_sequence: 3,
  error_code: null,
  safe_error_message: null,
  result: { summary: '已完成 AI 仲裁。' },
  artifact: null,
  created_at: '2026-08-27T11:00:00Z',
  submitted_at: '2026-08-27T11:00:01Z',
  started_at: '2026-08-27T11:00:02Z',
  completed_at: '2026-08-27T11:01:00Z',
} as RemoteDecisionRun

afterEach(() => vi.restoreAllMocks())

describe('RemoteDecisionPanel', () => {
  it('restores the latest run for the exact frozen evidence after remounting', async () => {
    const list = vi.spyOn(api, 'listRemoteDecisions').mockResolvedValue([run])
    vi.spyOn(api, 'preflightRemoteDecision').mockResolvedValue({
      enabled: true,
      blockers: [],
      warnings: [],
      intent_type: 'instrument_arbitration',
      source: run.source,
      source_summary: {},
      binding: null,
      input_sha256: run.input_sha256,
      preflight_fingerprint: 'c'.repeat(64),
    })

    await router.push('/instruments/INTC')
    await router.isReady()
    const wrapper = mount(RemoteDecisionPanel, {
      props: {
        intentType: 'instrument_arbitration',
        source: run.source,
        preflightOnMount: true,
      },
      global: {
        plugins: [router],
        stubs: {
          RemoteDecisionConfirmDialog: true,
          RouterLink: { props: ['to'], template: '<a :data-to="JSON.stringify(to)"><slot /></a>' },
        },
      },
    })
    await flushPromises()

    expect(list).toHaveBeenCalledWith({
      scope_type: 'instrument',
      scope_id: 'INTC',
      dataset_id: 'dataset-1',
      limit: 50,
    })
    expect(wrapper.find('.remote-decision-run-summary').text()).toContain('accepted')
    expect(wrapper.text()).toContain('已完成 AI 仲裁。')
    expect(JSON.parse(wrapper.find('a').attributes('data-to') ?? '{}')).toMatchObject({
      name: 'remote-decision-run',
      params: { localRunId: 'run-instrument-1' },
      query: { return_to: '/instruments/INTC?dataset=dataset-1' },
    })
  })
})
