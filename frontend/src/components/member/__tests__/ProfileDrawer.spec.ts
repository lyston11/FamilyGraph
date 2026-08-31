import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import * as membersApi from '@/api/members'
import ProfileDrawer from '@/components/member/ProfileDrawer.vue'
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

// 抽屉内嵌 AttachmentsSection（挂载即拉取附件列表），mock 掉避免真实 XHR
vi.mock('@/api/attachments', () => ({
  fetchAttachments: vi.fn().mockResolvedValue([]),
  addLink: vi.fn(),
  deleteAttachment: vi.fn(),
  uploadImage: vi.fn(),
  attachmentRawUrl: vi.fn(() => ''),
}))

const mockedRemove = vi.mocked(membersApi.removeMember)
const mockedDisclosure = vi.mocked(membersApi.updateDisclosure)

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

// n-drawer/n-modal 默认 teleport 到 body，断言与点击走 document 查询；
// 内嵌 KinshipTermPanel 已迁 naive-ui（P3），无需全局注册
function click(selector: string): void {
  const target = document.querySelector(selector)
  expect(target, selector).not.toBeNull()
  target!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

const text = (selector: string): string | undefined =>
  document.querySelector(selector)?.textContent ?? undefined

// naive useMessage 需 NMessageProvider 祖先；div 根保证组件树查询稳定
const MessageProvidedDrawer = defineComponent({
  render() {
    return h('div', [h(NMessageProvider, () => h(ProfileDrawer, { memberId: 2 }))])
  },
})

async function mountWithMember(member: Member) {
  const pinia = createPinia()
  const store = useMembersStore(pinia)
  store.members.push(member)
  store.openDrawer(member.id)
  const wrapper = mount(MessageProvidedDrawer, {
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
  await new Promise((resolve) => setTimeout(resolve))
  return { wrapper, store }
}

describe('ProfileDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('无权编辑态：仅可查看，隐藏编辑/披露/删除入口', async () => {
    const { wrapper } = await mountWithMember(
      makeMember({ permissions: { edit: false, delete: false } }),
    )
    expect(document.querySelector('[data-test="profile-view"]')).not.toBeNull()
    expect(document.querySelector('[data-test="start-edit"]')).toBeNull()
    expect(document.querySelector('[data-test="disclosure-group"]')).toBeNull()
    expect(document.querySelector('[data-test="delete-btn"]')).toBeNull()
    wrapper.unmount()
  })

  it('有编辑权：披露开关组可切换并保存（五键整体替换）', async () => {
    const member = makeMember()
    const { wrapper } = await mountWithMember(member)
    mockedDisclosure.mockResolvedValue(member)

    expect(document.querySelector('[data-test="disclosure-group"]')).not.toBeNull()
    // n-switch 根节点自带 onClick（无原生 input，Phase 1 后交互走根元素 click）
    click('[data-test="disclosure-avatar"]')
    await new Promise((resolve) => setTimeout(resolve))
    click('[data-test="disclosure-save"]')

    await vi.waitFor(() =>
      expect(mockedDisclosure).toHaveBeenCalledWith(member.id, {
        avatar: true,
        photos: false,
        dates: false,
        bio: false,
        attachments: false,
      }),
    )
    wrapper.unmount()
  })

  it('删除确认流：名字不符按钮禁用，相符调用删除并关闭抽屉', async () => {
    const member = makeMember()
    const { wrapper, store } = await mountWithMember(member)
    mockedRemove.mockResolvedValue(undefined)

    click('[data-test="delete-btn"]')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="delete-confirm-dialog"]')).not.toBeNull(),
    )

    const submit = (): HTMLButtonElement =>
      document.querySelector<HTMLButtonElement>('[data-test="delete-submit"]')!
    expect(submit().disabled).toBe(true)

    // data-test 经 input-props 落在 n-input 的原生 input 上
    const input = document.querySelector<HTMLInputElement>('[data-test="delete-confirm-input"]')!
    input.value = '错误名字'
    input.dispatchEvent(new Event('input'))
    await new Promise((resolve) => setTimeout(resolve))
    expect(submit().disabled).toBe(true)

    input.value = '母亲'
    input.dispatchEvent(new Event('input'))
    await new Promise((resolve) => setTimeout(resolve))
    submit().click()

    await vi.waitFor(() => expect(mockedRemove).toHaveBeenCalledWith(member.id, '母亲'))
    await vi.waitFor(() => expect(store.drawerTargetId).toBeNull())
    await vi.waitFor(() => expect(wrapper.findComponent(ProfileDrawer).emitted('close')).toHaveLength(1))
    wrapper.unmount()
  })

  it('删除被后端拒绝（409 名字不符）：确认弹窗内展示错误且不关闭', async () => {
    const { ApiError } = await import('@/api/errors')
    const member = makeMember()
    const { wrapper } = await mountWithMember(member)
    mockedRemove.mockRejectedValue(
      new ApiError(409, 'CONFIRM_NAME_MISMATCH', '输入的名字与档案名字不一致'),
    )

    click('[data-test="delete-btn"]')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="delete-confirm-dialog"]')).not.toBeNull(),
    )
    const input = document.querySelector<HTMLInputElement>('[data-test="delete-confirm-input"]')!
    input.value = '母亲'
    input.dispatchEvent(new Event('input'))
    await new Promise((resolve) => setTimeout(resolve))
    document
      .querySelector<HTMLButtonElement>('[data-test="delete-submit"]')!
      .click()

    await vi.waitFor(() => expect(text('[data-test="delete-error"]')).toContain('名字不一致'))
    expect(wrapper.findComponent(ProfileDrawer).emitted('close')).toBeUndefined()
    wrapper.unmount()
  })

  it('owner 删除被义务预检拦截（OWNER_TRANSFER_REQUIRED）：展示移交引导而非裸报错', async () => {
    const { ApiError } = await import('@/api/errors')
    const member = makeMember()
    const { wrapper } = await mountWithMember(member)
    mockedRemove.mockRejectedValue(
      new ApiError(409, 'OWNER_TRANSFER_REQUIRED', '该档案是家庭空间所有者，请先完成 owner 移交后再删除', {
        spaces_requiring_handover: [5],
      }),
    )

    click('[data-test="delete-btn"]')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="delete-confirm-dialog"]')).not.toBeNull(),
    )
    const input = document.querySelector<HTMLInputElement>('[data-test="delete-confirm-input"]')!
    input.value = '母亲'
    input.dispatchEvent(new Event('input'))
    await new Promise((resolve) => setTimeout(resolve))
    document.querySelector<HTMLButtonElement>('[data-test="delete-submit"]')!.click()

    // AC-F5：引导到移交流程（提及移交），不展示原始错误码文案
    await vi.waitFor(() => expect(text('[data-test="delete-error"]')).toContain('移交'))
    expect(wrapper.findComponent(ProfileDrawer).emitted('close')).toBeUndefined()
    wrapper.unmount()
  })
})
