import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ElementPlus from 'element-plus'

import AgentComposer from '@/components/agent/AgentComposer.vue'

/** AgentComposer：Enter 发送 / Shift+Enter 换行 / 发送中禁用 / 取消按钮仅作用于 active run */
function mountComposer(props: Partial<InstanceType<typeof AgentComposer>['$props']> = {}) {
  return mount(AgentComposer, {
    props: {
      modelValue: '',
      sending: false,
      canCancel: false,
      maxLength: 100,
      ...props,
    },
    global: { plugins: [ElementPlus] },
  })
}

describe('Composer', () => {
  it('空内容不可发送；输入后 Enter 发送并清理由父级控制', async () => {
    const wrapper = mountComposer({ modelValue: '  ' })
    expect(wrapper.find('[data-test="send-btn"]').attributes('disabled')).toBeDefined()

    await wrapper.setProps({ modelValue: '谁是我的长辈？' })
    const sendBtn = wrapper.find('[data-test="send-btn"]')
    expect(sendBtn.attributes('disabled')).toBeUndefined()

    await wrapper.find('[data-test="composer-input"]').setValue('谁是我的长辈？')
    await wrapper.find('[data-test="composer-input"]').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('send')).toHaveLength(1)
  })

  it('Shift+Enter 不发送（换行）', async () => {
    const wrapper = mountComposer({ modelValue: 'x' })
    await wrapper.find('[data-test="composer-input"]').trigger('keydown', {
      key: 'Enter',
      shiftKey: true,
    })
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('发送中禁用输入与发送按钮（Idempotency 重试由 store 层保证）', async () => {
    const wrapper = mountComposer({ modelValue: 'hi', sending: true })
    expect((wrapper.find('[data-test="composer-input"]').element as HTMLTextAreaElement).disabled).toBe(true)
    expect(wrapper.find('[data-test="send-btn"]').attributes('disabled')).toBeDefined()
  })

  it('canCancel=true 时显示取消按钮并上抛 cancel', async () => {
    const wrapper = mountComposer({ modelValue: '', canCancel: true })
    expect(wrapper.find('[data-test="cancel-run-btn"]').exists()).toBe(true)
    await wrapper.find('[data-test="cancel-run-btn"]').trigger('click')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })

  it('无活动 Run 时不显示取消按钮', () => {
    const wrapper = mountComposer({ modelValue: '', canCancel: false })
    expect(wrapper.find('[data-test="cancel-run-btn"]').exists()).toBe(false)
  })

  it('输入经 update:modelValue 上抛（草稿归 store 分区管理）', async () => {
    const wrapper = mountComposer()
    await wrapper.find('[data-test="composer-input"]').setValue('草稿内容')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['草稿内容'])
  })
})
