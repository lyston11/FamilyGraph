import { mount } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ElementPlus from 'element-plus'

import * as membersApi from '@/api/members'
import * as spacesApi from '@/api/spaces'
import SpaceGovernanceDialog from '@/components/member/SpaceGovernanceDialog.vue'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import type { FamilySpace, Member, OwnershipTransfer, SpaceMemberInfo } from '@/types/api'

vi.mock('@/api/members', () => ({
  fetchMembers: vi.fn(),
  fetchMember: vi.fn(),
  createMember: vi.fn(),
  updateMember: vi.fn(),
  updateDisclosure: vi.fn(),
  removeMember: vi.fn(),
  fetchMembersByPrefix: vi.fn(),
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

const mockedInvite = vi.mocked(spacesApi.inviteToSpace)
const mockedSearch = vi.mocked(membersApi.fetchMembersByPrefix)
const mockedCreateTransfer = vi.mocked(spacesApi.createOwnershipTransfer)
const mockedRespondTransfer = vi.mocked(spacesApi.respondOwnershipTransfer)

function makeSpace(overrides: Partial<FamilySpace> = {}): FamilySpace {
  return {
    id: 1,
    name: '我家',
    owner_id: 1,
    kind: 'household',
    created_at: '2026-08-25T00:00:00',
    pending_count: 0,
    member_count: 3,
    ...overrides,
  }
}

function makeMembership(overrides: Partial<SpaceMemberInfo> = {}): SpaceMemberInfo {
  return {
    id: 11,
    space_id: 1,
    user_id: 1,
    user_name: null,
    added_by: null,
    role: 'member',
    status: 'active',
    updated_at: '2026-08-25T00:00:00',
    ...overrides,
  }
}

function makeTransfer(overrides: Partial<OwnershipTransfer> = {}): OwnershipTransfer {
  return {
    id: 50,
    space_id: 1,
    from_user: 1,
    to_user: 2,
    status: 'pending',
    created_at: '2026-08-26T00:00:00',
    decided_at: null,
    ...overrides,
  }
}

let pinia: Pinia

async function mountDialog(): Promise<ReturnType<typeof mount>> {
  const wrapper = mount(SpaceGovernanceDialog, {
    props: { visible: true },
    global: { plugins: [pinia, ElementPlus] },
    attachTo: document.body,
  })
  await new Promise((resolve) => setTimeout(resolve))
  return wrapper
}

describe('SpaceGovernanceDialog（v2 §0.2/§0.5 空间治理）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    pinia = createPinia()
  })

  function seedState(
    myUserId: number,
    memberships: SpaceMemberInfo[],
    transfers: OwnershipTransfer[] = [],
  ): void {
    pinia.state.value.auth = {
      user: { id: myUserId, name: `用户${myUserId}`, is_admin: false, pin_must_change: false },
      accessToken: 'a',
      refreshToken: null,
    }
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [makeSpace()]
    spaces.currentSpaceId = 1
    spaces.members = memberships
    spaces.transfers = transfers
    void useAuthStore(pinia)
  }

  const allMembers = (): SpaceMemberInfo[] => [
    makeMembership({ id: 11, user_id: 1, role: 'owner' }),
    makeMembership({ id: 12, user_id: 2, role: 'space_admin' }),
    makeMembership({ id: 13, user_id: 3, role: 'guest' }),
  ]

  it('owner 视角：kind/角色徽标正确，可见邀请区与移交发起区；访客提示不出现', async () => {
    seedState(1, allMembers())
    const wrapper = await mountDialog()

    expect(wrapper.find('[data-test="my-role-tag"]').text()).toContain('所有者')
    expect(wrapper.find('[data-test="guest-hint"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="governance-invite-search"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="transfer-target-select"]').exists()).toBe(true)
    // 可移交候选排除自己（下拉打开后才渲染，这里仅断言非 owner 不在候选逻辑内：由后续测试覆盖）
    wrapper.unmount()
  })

  it('owner 邀请成员：搜索过滤已有成员后调用 inviteToSpace', async () => {
    seedState(1, allMembers())
    mockedSearch.mockResolvedValue([
      { id: 9, name: '新家人' } as Member,
      { id: 2, name: '已是成员' } as unknown as Member,
    ])
    const wrapper = await mountDialog()

    await wrapper.find('[data-test="governance-invite-search"]').setValue('新')
    await wrapper.find('[data-test="governance-invite-search-btn"]').trigger('click')
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="governance-invite-9"]').exists()).toBe(true),
    )
    // 已是空间成员的搜索结果被过滤
    expect(wrapper.find('[data-test="governance-invite-2"]').exists()).toBe(false)

    mockedInvite.mockResolvedValue(makeMembership({ id: 20, user_id: 9, status: 'pending' }))
    await wrapper.find('[data-test="governance-invite-9"]').trigger('click')
    await vi.waitFor(() => expect(mockedInvite).toHaveBeenCalledWith(1, 9))
    wrapper.unmount()
  })

  it('owner 发起移交：未选目标禁用；从下拉选择后调用 createOwnershipTransfer', async () => {
    seedState(1, allMembers())
    const wrapper = await mountDialog()

    const initiate = () => wrapper.find('[data-test="transfer-initiate"]')
    expect((initiate().element as HTMLButtonElement).disabled).toBe(true)

    await wrapper.find('[data-test="transfer-target-select"]').trigger('click')
    await vi.waitFor(() =>
      expect(document.querySelector('.el-select-dropdown__item')).not.toBeNull(),
    )
    // 第一个选项即第一个非自己成员（用户2）
    document
      .querySelector('.el-select-dropdown__item')!
      .dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve))
    await new Promise((resolve) => setTimeout(resolve))
    expect((initiate().element as HTMLButtonElement).disabled).toBe(false)

    mockedCreateTransfer.mockResolvedValue(makeTransfer())
    await initiate().trigger('click')
    await vi.waitFor(() => expect(mockedCreateTransfer).toHaveBeenCalledWith(1, 2))
    wrapper.unmount()
  })

  it('受让人视角：pending 移交横幅出现并可接受；space_admin 可邀请但无移交发起区', async () => {
    seedState(2, allMembers(), [makeTransfer()])
    const wrapper = await mountDialog()

    expect(wrapper.find('[data-test="my-role-tag"]').text()).toContain('管理员')
    expect(wrapper.find('[data-test="transfer-pending"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="transfer-accept"]').exists()).toBe(true)
    // space_admin 可邀请新成员（§0.2），但不能发起移交（仅 owner）
    expect(wrapper.find('[data-test="governance-invite-search"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="transfer-target-select"]').exists()).toBe(false)

    mockedRespondTransfer.mockResolvedValue(makeTransfer({ status: 'accepted', decided_at: 'x' }))
    await wrapper.find('[data-test="transfer-accept"]').trigger('click')
    await vi.waitFor(() => expect(mockedRespondTransfer).toHaveBeenCalledWith(50, 'accept'))
    wrapper.unmount()
  })

  it('发起人视角：可取消自己发出的 pending 移交', async () => {
    seedState(1, allMembers(), [makeTransfer()])
    const wrapper = await mountDialog()

    expect(wrapper.find('[data-test="transfer-cancel"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="transfer-accept"]').exists()).toBe(false)

    mockedRespondTransfer.mockResolvedValue(makeTransfer({ status: 'cancelled', decided_at: 'x' }))
    await wrapper.find('[data-test="transfer-cancel"]').trigger('click')
    await vi.waitFor(() => expect(mockedRespondTransfer).toHaveBeenCalledWith(50, 'cancel'))
    wrapper.unmount()
  })

  it('访客视角：显示最小信息提示且无邀请区', async () => {
    seedState(3, allMembers())
    const wrapper = await mountDialog()

    expect(wrapper.find('[data-test="guest-hint"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="my-role-tag"]').text()).toContain('访客')
    expect(wrapper.find('[data-test="governance-invite-search"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="transfer-target-select"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
