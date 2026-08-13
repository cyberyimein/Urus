import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AppShell from '@/components/AppShell.vue'

describe('AppShell', () => {
  it('keeps operational entry points inside the research center', () => {
    const wrapper = mount(AppShell, {
      global: {
        stubs: {
          RouterLink: { props: ['to'], template: '<a :data-to="to"><slot /></a>' },
        },
      },
    })

    expect(wrapper.get('nav').text()).toContain('研究中心')
    expect(wrapper.get('nav').text()).not.toContain('手动分析')
    expect(wrapper.get('nav').text()).not.toContain('开发工具')
  })
})
