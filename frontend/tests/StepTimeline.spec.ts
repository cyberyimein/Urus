import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import StepTimeline from '@/components/StepTimeline.vue'

describe('StepTimeline', () => {
  it('shows successful, skipped, and failed states without hiding the explanation', () => {
    const wrapper = mount(StepTimeline, {
      props: {
        steps: [
          {
            id: 'one', run_id: 'run', position: 1, step_code: '1a', status: 'succeeded',
            started_at: '2026-08-02T00:00:00Z', completed_at: null, summary: 'mock market',
            error_message: null, payload: null,
          },
          {
            id: 'two', run_id: 'run', position: 2, step_code: '1b', status: 'skipped',
            started_at: null, completed_at: null, summary: 'no event', error_message: null, payload: null,
          },
          {
            id: 'three', run_id: 'run', position: 3, step_code: '2', status: 'failed',
            started_at: null, completed_at: null, summary: 'broken', error_message: 'test failure', payload: null,
          },
        ],
      },
    })

    expect(wrapper.findAll('.step-row')).toHaveLength(3)
    expect(wrapper.find('[data-status="skipped"]').text()).toContain('已跳过')
    expect(wrapper.find('[data-status="failed"]').text()).toContain('test failure')
  })
})

