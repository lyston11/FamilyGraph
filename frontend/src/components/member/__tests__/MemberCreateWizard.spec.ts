import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ElementPlus from 'element-plus'

import * as membersApi from '@/api/members'
import MemberCreateWizard from '@/components/member/MemberCreateWizard.vue'
import { useMembersStore } from '@/stores/members'
import type { Member } from '@/types/api'

vi.mock('@/api/members', () => ({
  fetchMembers: vi.fn(),
  fetchMember: vi.fn(),
  createMember: vi.fn(),
  updateMember: vi.fn(),
  updateDisclosure: vi.fn(),
  removeMember: vi.fn(),
}))

const mockedCreate = vi.mocked(membersApi.createMember)

function makeMember(overrides: Partial<Member> = {}): Member {
  return {
    id: 2,
    name: '母亲',
    is_admin: false,
    gender: 'f',
    birth: null,
    death: null,
    bio: null,
    avatar_path: null,
    privacy_mode: 'handover',
    claim_status: 'managed',
    created_by: 1,
    created_at: '2026-08-25T00:00:00',
    clan_disclosure: {
      avatar: false,
      photos: false,
      dates: false,
      bio: false,
      attachments: false,
    },
    permissions: { edit: true, delete: true },
    ...overrides,
  }
}

async function mountWizard() {
  const pinia = createPinia()
  const wrapper = mount(MemberCreateWizard, {
    global: { plugins: [pinia, ElementPlus] },
  })
  await wrapper.vm.$nextTick()
  return wrapper
}

describe('MemberCreateWizard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('第一步名字为空时不能进入下一步，填写后放行', async () => {
    const wrapper = await mountWizard()

    const next = wrapper.find('[data-test="wizard-next"]')
    expect((next.element as HTMLButtonElement).disabled).toBe(true)

    await wrapper.find('[data-test="wizard-name"]').setValue('母亲')
    expect((next.element as HTMLButtonElement).disabled).toBe(false)
    await next.trigger('click')
    expect(wrapper.find('[data-test="wizard-step-mode"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('完整流程：资料 → 归属模式 → 确认提交 → 携一次性 PIN emit created', async () => {
    const pinia = createPinia()
    const wrapper = mount(MemberCreateWizard, {
      global: { plugins: [pinia, ElementPlus] },
    })
    const store = useMembersStore(pinia)
    mockedCreate.mockResolvedValue({ user: makeMember(), pin: '654321' })
    await wrapper.vm.$nextTick()

    await wrapper.find('[data-test="wizard-name"]').setValue('母亲')
    await wrapper.find('[data-test="wizard-next"]').trigger('click')

    // 归属模式：默认 handover，选择「永久管理」（与 login.spec 相同的点击方式）
    const radios = wrapper.find('[data-test="wizard-mode"]').findAll('.el-radio')
    expect(radios.length).toBe(2)
    radios[1].find('.el-radio__original').setValue(true)
    await new Promise((resolve) => setTimeout(resolve))
    expect(store.members).toHaveLength(0)
    await wrapper.find('[data-test="wizard-to-confirm"]').trigger('click')
    await wrapper.find('[data-test="wizard-submit"]').trigger('click')

    await vi.waitFor(() =>
      expect(mockedCreate).toHaveBeenCalledWith(
        expect.objectContaining({ name: '母亲', privacy_mode: 'perpetual' }),
      ),
    )
    // store 已落新档案（服务端数据唯一来源）
    await vi.waitFor(() => expect(store.members).toHaveLength(1))
    expect(wrapper.emitted('created')).toEqual([[{ name: '母亲', pin: '654321' }]])
    wrapper.unmount()
  })

  it('提交失败：展示错误文案且不 emit created', async () => {
    const { ApiError } = await import('@/api/errors')
    mockedCreate.mockRejectedValue(new ApiError(422, 'VALIDATION_ERROR', '请求参数不合法'))
    const wrapper = await mountWizard()

    await wrapper.find('[data-test="wizard-name"]').setValue('母亲')
    await wrapper.find('[data-test="wizard-next"]').trigger('click')
    await wrapper.find('[data-test="wizard-to-confirm"]').trigger('click')
    await wrapper.find('[data-test="wizard-submit"]').trigger('click')

    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="wizard-error"]').text()).toBe('请求参数不合法'),
    )
    expect(wrapper.emitted('created')).toBeUndefined()
    wrapper.unmount()
  })
})
