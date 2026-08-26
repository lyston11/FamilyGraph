import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ElementPlus from 'element-plus'

import PendingProfileRefs from '@/components/member/PendingProfileRefs.vue'
import type { SpaceProfileRefInfo } from '@/types/api'

function makeRef(overrides: Partial<SpaceProfileRefInfo> = {}): SpaceProfileRefInfo {
  return {
    profile_id: 42,
    name: '先祖',
    added_at: '2026-08-26T00:00:00',
    ...overrides,
  }
}

describe('PendingProfileRefs（AC-F2 待确档引用，v2 Gap1）', () => {
  it('有引用时展示「待确档引用」区与最小名字条目', () => {
    const wrapper = mount(PendingProfileRefs, {
      props: { refs: [makeRef(), makeRef({ profile_id: 43, name: '远祖' })] },
      global: { plugins: [ElementPlus] },
    })

    expect(wrapper.find('[data-test="profile-ref-section"]').exists()).toBe(true)
    const names = wrapper.findAll('[data-test="profile-ref-name"]')
    expect(names).toHaveLength(2)
    expect(names[0].text()).toBe('先祖')
    expect(names[1].text()).toBe('远祖')
  })

  it('无引用时不渲染区块', () => {
    const wrapper = mount(PendingProfileRefs, {
      props: { refs: [] },
      global: { plugins: [ElementPlus] },
    })

    expect(wrapper.find('[data-test="profile-ref-section"]').exists()).toBe(false)
  })
})
