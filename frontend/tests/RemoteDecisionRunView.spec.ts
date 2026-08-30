import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import RemoteDecisionRunView from '@/views/RemoteDecisionRunView.vue'
import { router } from '@/router'

afterEach(() => vi.restoreAllMocks())

describe('RemoteDecisionRunView', () => {
  it('renders an accepted structured decision when summary and notable cards are absent', async () => {
    vi.spyOn(api, 'getRemoteDecision').mockResolvedValue({
      local_run_id: 'run-accepted',
      anomalo_run_id: 'anomalo-accepted',
      intent_type: 'instrument_arbitration',
      request_intent_id: 'request-accepted',
      idempotency_key: 'key-accepted',
      scope_type: 'instrument',
      scope_id: 'INTC',
      scope_version: '1',
      dataset_id: 'dataset-accepted',
      lens_type: null,
      lens_id: null,
      lens_version: null,
      source: { dataset_id: 'dataset-accepted', symbol: 'INTC' },
      workflow_ref: 'urus-instrument-arbitration@3',
      input_schema_version: 'urus.remote_decision_input.v1',
      input_sha256: 'a'.repeat(64),
      status: 'accepted',
      remote_status: 'succeeded',
      validation_status: 'accepted',
      latest_event_sequence: 2,
      error_code: null,
      safe_error_message: null,
      result: {
        schema_version: 'urus.remote_decision_artifact.v1',
        completeness: 'complete',
        summary: null,
        decision: {
          scope: { trading_date: '2026-08-28' },
          consensus_state: 'aligned',
          bullish_count: 0,
          bearish_count: 2,
          neutral_count: 3,
          suggested_action: 'avoid',
          conflict_summary: '至少两个策略方向一致。',
          strategy_set: [{ name: 'trend_momentum_v1', version: '1.0.0' }],
        },
        notable_cards: [],
        warnings: [],
      },
      artifact: null,
      created_at: '2026-08-28T21:36:16Z',
      submitted_at: '2026-08-28T21:36:17Z',
      started_at: '2026-08-28T21:36:19Z',
      completed_at: '2026-08-28T21:39:19Z',
    })
    vi.spyOn(api, 'getRemoteDecisionEvents').mockResolvedValue([])

    await router.push('/decision-runs/run-accepted')
    await router.isReady()
    const wrapper = mount(RemoteDecisionRunView, {
      global: {
        plugins: [router],
        stubs: {
          AppShell: true,
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('回避 · 方向一致')
    expect(wrapper.text()).toContain('至少两个策略方向一致。')
    expect(wrapper.text()).toContain('看空策略')
    expect(wrapper.text()).toContain('策略集合')
    expect(wrapper.text()).toContain('trend_momentum_v1 · 1.0.0')
    expect(wrapper.text()).not.toContain('Workflow 正在读取冻结证据。')
  })
})
