import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AppShell from '@/components/AppShell.vue'

describe('AppShell', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('keeps operational entry points inside the evidence workspace', () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      schema_version: 'urus.database_health.v1',
      status: 'ok',
      checked_at: '2026-09-05T00:00:00Z',
      next_check_at: '2026-09-06T00:00:00Z',
      integrity_check: 'ok',
      foreign_key_violations: 0,
      duration_ms: 1,
      database_size_bytes: 1,
      message: null,
    }), { status: 200 })))
    const wrapper = mount(AppShell, {
      global: {
        stubs: {
          RouterLink: { props: ['to'], template: '<a :data-to="to"><slot /></a>' },
        },
      },
    })

    expect(wrapper.get('nav').text()).toContain('收盘观测')
    expect(wrapper.get('nav').text()).not.toContain('历史报告')
    expect(wrapper.get('nav').text()).not.toContain('手动分析')
    expect(wrapper.get('nav').text()).not.toContain('开发工具')
  })

  it('shows the cached database health state in the top bar', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      schema_version: 'urus.database_health.v1',
      status: 'error',
      checked_at: '2026-09-05T00:00:00Z',
      next_check_at: '2026-09-06T00:00:00Z',
      integrity_check: 'database disk image is malformed',
      foreign_key_violations: 0,
      duration_ms: 12,
      database_size_bytes: 123,
      message: 'SQLite 完整性检查失败',
    }), { status: 200 })))

    const wrapper = mount(AppShell, {
      global: {
        stubs: {
          RouterLink: { props: ['to'], template: '<a :data-to="to"><slot /></a>' },
        },
      },
    })

    await flushPromises()

    expect(wrapper.get('.database-health').text()).toContain('DB 异常')
    expect(wrapper.get('.database-health').attributes('data-tone')).toBe('error')
  })
})
