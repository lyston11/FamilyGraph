import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ElementPlus from 'element-plus'

import * as graphApi from '@/api/graph'
import * as membersApi from '@/api/members'
import * as spacesApi from '@/api/spaces'
import MemberCreateWizard from '@/components/member/MemberCreateWizard.vue'
import { useMembersStore } from '@/stores/members'
import type { FamilySpace, Member } from '@/types/api'

vi.mock('@/api/members', () => ({
  fetchMembers: vi.fn(),
  fetchMember: vi.fn(),
  createMember: vi.fn(),
  updateMember: vi.fn(),
  updateDisclosure: vi.fn(),
  removeMember: vi.fn(),
}))

vi.mock('@/api/graph', () => ({
  createConnectionRequest: vi.fn().mockResolvedValue({ id: 99 }),
}))

vi.mock('@/api/spaces', () => ({
  fetchSpaces: vi.fn().mockResolvedValue([]),
  createSpace: vi.fn(),
  fetchSpaceMembers: vi.fn().mockResolvedValue([]),
  inviteToSpace: vi.fn(),
  removeOrWithdrawMembership: vi.fn(),
  resolveMembership: vi.fn(),
  joinByUser: vi.fn(),
  getSpacePositions: vi.fn(),
  putSpacePositions: vi.fn(),
  createOwnershipTransfer: vi.fn(),
  fetchOwnershipTransfers: vi.fn().mockResolvedValue([]),
  respondOwnershipTransfer: vi.fn(),
}))

const mockedCreate = vi.mocked(membersApi.createMember)
const mockedConnect = vi.mocked(graphApi.createConnectionRequest)

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

/** 第一步通用操作：填名字 + 选关系（F-1 双必填） */
async function fillInfoStep(wrapper: ReturnType<typeof mount>, name = '母亲'): Promise<void> {
  await wrapper.find('[data-test="wizard-name"]').setValue(name)
  const relationRadios = wrapper
    .find('[data-test="wizard-relation-dir"]')
    .findAll('.el-radio')
  relationRadios[0].find('.el-radio__original').setValue(true)
  await new Promise((resolve) => setTimeout(resolve))
}

describe('MemberCreateWizard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // clearAllMocks 保留 mockImplementation，但清空 mockResolvedValue 的结果记录；
    // 为稳妥起见重设默认实现
    mockedConnect.mockResolvedValue({ id: 99 } as never)
  })

  it('第一步：名字为空禁用下一步；仅填名字仍禁用（关系必填）；选关系后放行', async () => {
    const wrapper = await mountWizard()

    const next = wrapper.find('[data-test="wizard-next"]')
    expect((next.element as HTMLButtonElement).disabled).toBe(true)

    await wrapper.find('[data-test="wizard-name"]').setValue('母亲')
    expect((next.element as HTMLButtonElement).disabled).toBe(true)

    const relationRadios = wrapper
      .find('[data-test="wizard-relation-dir"]')
      .findAll('.el-radio')
    expect(relationRadios.length).toBe(4)
    relationRadios[0].find('.el-radio__original').setValue(true)
    await new Promise((resolve) => setTimeout(resolve))

    expect((next.element as HTMLButtonElement).disabled).toBe(false)
    await next.trigger('click')
    expect(wrapper.find('[data-test="wizard-step-mode"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('完整流程（无空间）：资料 → 归属模式 → 确认提交 → 建档 + 关系请求发出 + PIN 弹窗', async () => {
    const pinia = createPinia()
    const wrapper = mount(MemberCreateWizard, {
      global: { plugins: [pinia, ElementPlus] },
    })
    const store = useMembersStore(pinia)
    mockedCreate.mockResolvedValue({ user: makeMember(), pin: '654321' })
    await wrapper.vm.$nextTick()

    await fillInfoStep(wrapper)
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
        expect.objectContaining({ name: '母亲', privacy_mode: 'perpetual', space_membership: null }),
      ),
    )
    // v2 F-1：关系以合并请求发出（对方确档后自行确认）
    await vi.waitFor(() =>
      expect(mockedConnect).toHaveBeenCalledWith({
        target_id: 2,
        dir_class: 'elder',
        label: null,
      }),
    )
    // store 已落新档案（服务端数据唯一来源）
    await vi.waitFor(() => expect(store.members).toHaveLength(1))
    expect(wrapper.emitted('created')).toEqual([[{ name: '母亲', pin: '654321' }]])
    wrapper.unmount()
  })

  it('提交失败：展示错误文案、不 emit created、不发关系请求', async () => {
    const { ApiError } = await import('@/api/errors')
    mockedCreate.mockRejectedValue(new ApiError(422, 'VALIDATION_ERROR', '请求参数不合法'))
    const wrapper = await mountWizard()

    await fillInfoStep(wrapper)
    await wrapper.find('[data-test="wizard-next"]').trigger('click')
    await wrapper.find('[data-test="wizard-to-confirm"]').trigger('click')
    await wrapper.find('[data-test="wizard-submit"]').trigger('click')

    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="wizard-error"]').text()).toBe('请求参数不合法'),
    )
    expect(wrapper.emitted('created')).toBeUndefined()
    expect(mockedConnect).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('空间选择（F-3）：未选具体空间时拦截；新建族谱空间后自动选中并随建档提交引用', async () => {
    vi.mocked(spacesApi.fetchSpaces).mockResolvedValue([
      {
        id: 3,
        name: '我家',
        owner_id: 1,
        kind: 'household',
        created_at: '2026-08-25T00:00:00',
        pending_count: 0,
        member_count: 2,
      },
    ])
    vi.mocked(spacesApi.createSpace).mockResolvedValue({
      id: 7,
      name: '王家族谱',
      owner_id: 1,
      kind: 'lineage',
      created_at: '2026-08-26T00:00:00',
      pending_count: 0,
      member_count: 1,
    } satisfies FamilySpace)
    mockedCreate.mockResolvedValue({ user: makeMember(), pin: '111111' })
    const wrapper = await mountWizard()

    // 等待 onMounted 的 spaces.load() 完成
    await new Promise((resolve) => setTimeout(resolve))

    await fillInfoStep(wrapper)
    await wrapper.find('[data-test="wizard-next"]').trigger('click')
    await wrapper.find('[data-test="wizard-to-confirm"]').trigger('click') // 进入空间步骤

    const stepSpace = wrapper.find('[data-test="wizard-step-space"]')
    expect(stepSpace.exists()).toBe(true)

    // 选「家庭空间」但未选具体空间 → 下一步被拦截
    const choiceRadios = wrapper.find('[data-test="wizard-space-choice"]').findAll('.el-radio')
    expect(choiceRadios.length).toBe(3)
    choiceRadios[1].find('.el-radio__original').setValue(true)
    await new Promise((resolve) => setTimeout(resolve))
    await wrapper.find('[data-test="wizard-to-confirm"]').trigger('click')
    expect(wrapper.find('[data-test="wizard-step-space"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="wizard-error"]').text()).toContain('请先选择一个具体空间')

    // 切到族谱空间并内联新建 → 自动选中 → 可进入确认
    choiceRadios[2].find('.el-radio__original').setValue(true)
    await new Promise((resolve) => setTimeout(resolve))
    await wrapper.find('[data-test="wizard-lineage-name"]').setValue('王家族谱')
    await wrapper.find('[data-test="wizard-lineage-create"]').trigger('click')
    await vi.waitFor(() => expect(spacesApi.createSpace).toHaveBeenCalledWith('王家族谱', 'lineage'))

    await wrapper.find('[data-test="wizard-to-confirm"]').trigger('click')
    expect(wrapper.find('[data-test="wizard-step-confirm"]').exists()).toBe(true)
    await wrapper.find('[data-test="wizard-submit"]').trigger('click')

    await vi.waitFor(() =>
      expect(mockedCreate).toHaveBeenCalledWith(
        expect.objectContaining({ space_membership: { space_id: 7 } }),
      ),
    )
    wrapper.unmount()
  })
})
