import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ElementPlus from 'element-plus'

import OneTimePinDialog from '@/components/member/OneTimePinDialog.vue'

vi.mock('@/api/members', () => ({
  fetchMembers: vi.fn(),
  fetchMember: vi.fn(),
  createMember: vi.fn(),
  updateMember: vi.fn(),
  updateDisclosure: vi.fn(),
  removeMember: vi.fn(),
}))

// 弹窗渲染在组件树内（无 teleport），配合 attachTo 用 document 查询亦可
const pinInBody = (): string | null =>
  document.querySelector('[data-test="one-time-pin"]')?.textContent ?? null

describe('OneTimePinDialog', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  function mountDialog() {
    return mount(OneTimePinDialog, {
      props: { pin: '654321', memberName: '母亲' },
      global: { plugins: [ElementPlus] },
      attachTo: document.body,
    })
  }

  it('大字号展示一次性 PIN 与保存警告', async () => {
    const wrapper = mountDialog()
    await new Promise((resolve) => setTimeout(resolve))
    expect(pinInBody()).toBe('654321')
    expect(document.querySelector('[data-test="pin-copy"]')).not.toBeNull()
    expect(document.body.textContent).toContain('仅显示这一次')
    wrapper.unmount()
  })

  it('点击「我已保存」仅 emit close，由父组件清空状态并卸载（不可回看）', async () => {
    const wrapper = mountDialog()
    await new Promise((resolve) => setTimeout(resolve))
    expect(pinInBody()).not.toBeNull()

    ;(document.querySelector('[data-test="pin-done"]') as HTMLButtonElement).click()
    await new Promise((resolve) => setTimeout(resolve))

    expect(wrapper.emitted('close')).toHaveLength(1)
    // 模拟父组件响应 close：卸载组件后 PIN 不应再出现在任何界面
    wrapper.unmount()
    expect(pinInBody()).toBeNull()
  })
})
