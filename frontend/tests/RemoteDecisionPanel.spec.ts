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

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

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

  it('keeps historical runs visible when the current dataset has no matching run', async () => {
    const historicalRun = { ...run, local_run_id: 'run-history-1', dataset_id: 'older-dataset', source: { ...run.source, dataset_id: 'older-dataset' } }
    vi.spyOn(api, 'listRemoteDecisions').mockResolvedValue([historicalRun])
    vi.spyOn(api, 'preflightRemoteDecision').mockResolvedValue({
      enabled: true,
      blockers: [],
      warnings: [],
      intent_type: 'instrument_arbitration',
      source: { dataset_id: 'current-dataset', symbol: 'INTC' },
      source_summary: {},
      binding: null,
      input_sha256: null,
      preflight_fingerprint: null,
    })

    await router.push('/instruments/INTC?dataset=current-dataset')
    await router.isReady()
    const wrapper = mount(RemoteDecisionPanel, {
      props: {
        intentType: 'instrument_arbitration',
        source: { dataset_id: 'current-dataset', symbol: 'INTC' },
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

    expect(wrapper.find('.remote-decision-history').exists()).toBe(true)
    expect(wrapper.text()).toContain('DECISION HISTORY')
    expect(wrapper.text()).toContain('已完成 AI 仲裁。')
    expect(wrapper.find('.remote-decision-run-summary').exists()).toBe(false)
  })

  it('refreshes the matching history row when a restored run changes status', async () => {
    vi.useFakeTimers()
    const queued = { ...run, status: 'queued', remote_status: 'queued', result: null } as RemoteDecisionRun
    const accepted = { ...run, status: 'accepted' } as RemoteDecisionRun
    vi.spyOn(api, 'listRemoteDecisions').mockResolvedValue([queued])
    const refresh = vi.spyOn(api, 'getRemoteDecision').mockResolvedValue(accepted)
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
          RouterLink: { props: ['to'], template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    expect(wrapper.find('.remote-history-item').text()).toContain('排队中')
    await vi.advanceTimersByTimeAsync(1500)
    await flushPromises()

    expect(refresh).toHaveBeenCalledWith(run.local_run_id)
    expect(wrapper.find('.remote-history-item').text()).toContain('已验收')
  })
})
