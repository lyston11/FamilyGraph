import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import OneTimePinDialog from '@/components/member/OneTimePinDialog.vue'

// naive useMessage 需 NMessageProvider 祖先；div 根保证 test-utils 元素查询稳定
const MessageProvidedDialog = defineComponent({
  render() {
    return h('div', [
      h(NMessageProvider, () =>
        h(OneTimePinDialog, { pin: '654321', memberName: '母亲' }),
      ),
    ])
  },
})

// n-modal 内容 teleport 到 body，断言与点击走 document 查询
const pinInBody = (): string | null =>
  document.querySelector('[data-test="one-time-pin"]')?.textContent ?? null

describe('OneTimePinDialog', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  function mountDialog() {
    return mount(MessageProvidedDialog, {
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

    // 组件树内查找（n-modal DOM teleport 到 body，但组件实例仍在树内）
    const dialog = wrapper.findComponent(OneTimePinDialog)
    expect(dialog.emitted('close')).toHaveLength(1)
    // 模拟父组件响应 close：卸载组件后 PIN 不应再出现在任何界面
    wrapper.unmount()
    expect(pinInBody()).toBeNull()
  })
})
