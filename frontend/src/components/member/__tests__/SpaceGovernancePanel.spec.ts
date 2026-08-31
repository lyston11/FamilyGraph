import { mount } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import * as membersApi from '@/api/members'
import SpaceGovernancePanel from '@/components/member/SpaceGovernancePanel.vue'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import type { FamilySpace, SpaceMemberInfo } from '@/types/api'

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
  fetchSpaces: vi.fn(),
  createSpace: vi.fn(),
  fetchSpaceMembers: vi.fn(),
  fetchSpaceProfileRefs: vi.fn().mockResolvedValue([]),
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

const mockedSearch = vi.mocked(membersApi.fetchMembersByPrefix)

function makeSpace(): FamilySpace {
  return {
    id: 1,
    name: '我们家',
    owner_id: 1,
    kind: 'household',
    created_at: '2026-08-29T00:00:00',
    pending_count: 1,
    member_count: 2,
  }
}

function makeMember(overrides: Partial<SpaceMemberInfo> = {}): SpaceMemberInfo {
  return {
    id: 1,
    space_id: 1,
    user_id: 1,
    user_name: '空间用户',
    added_by: 1,
    role: 'space_admin',
    status: 'active',
    updated_at: '2026-08-29T00:00:00',
    ...overrides,
  }
}

const ProvidedPanel = defineComponent({
  render() {
    return h(NMessageProvider, () => h(SpaceGovernancePanel))
  },
})

function seed(pinia: Pinia, role: SpaceMemberInfo['role']): void {
  const auth = useAuthStore(pinia)
  auth.user = {
    id: 1,
    name: '空间用户',
    is_admin: false,
    pin_must_change: false,
    claim_status: 'claimed',
    profile_status: 'identity_confirmed',
  }
  const spaces = useSpacesStore(pinia)
  spaces.spaces = [makeSpace()]
  spaces.currentSpaceId = 1
  spaces.members = [
    makeMember({ role }),
    makeMember({ id: 2, user_id: 2, user_name: '其他成员', role: 'member' }),
    makeMember({ id: 3, user_id: 3, user_name: '待确认', role: 'member', status: 'pending' }),
  ]
  spaces.profileRefs = [{ profile_id: 9, name: '待确档长辈', added_at: '2026-08-29T00:00:00' }]
}

describe('SpaceGovernancePanel', () => {
  let pinia: Pinia

  beforeEach(() => {
    pinia = createPinia()
    document.body.innerHTML = ''
    vi.clearAllMocks()
  })

  it('空间管理员可见成员数、邀请区与交接发起区', () => {
    seed(pinia, 'space_admin')
    const wrapper = mount(ProvidedPanel, { global: { plugins: [pinia] } })

    expect(wrapper.find('[data-test="space-governance-panel"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="my-role-tag"]').text()).toContain('空间管理员')
    expect(wrapper.find('[data-test="space-member-count"]').text()).toBe('2')
    expect(wrapper.find('[data-test="space-pending-count"]').text()).toBe('1')
    expect(wrapper.find('[data-test="governance-invite-search"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="transfer-target-select"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('交接文案说明单一管理员语义：对方接任、自己降级', () => {
    // 每空间只有一个管理员，交接不是「多加一个管理员」；文案必须让发起人
    // 在点下按钮前知道自己会降为普通成员。
    seed(pinia, 'space_admin')
    const wrapper = mount(ProvidedPanel, { global: { plugins: [pinia] } })

    const text = wrapper.text()
    expect(text).toContain('唯一管理员')
    expect(text).toContain('降为普通成员')
    expect(text).not.toContain('所有者')
    wrapper.unmount()
  })

  it('member 可见邀请区但无交接入口；guest 两者均不可见', () => {
    for (const role of ['member', 'guest'] as const) {
      pinia = createPinia()
      seed(pinia, role)
      const wrapper = mount(ProvidedPanel, { global: { plugins: [pinia] } })
      // 邀请区随 canInvite 对 active member 放开；guest 仍隐藏
      expect(wrapper.find('[data-test="governance-invite-search"]').exists()).toBe(role === 'member')
      expect(wrapper.find('[data-test="transfer-target-select"]').exists()).toBe(false)
      wrapper.unmount()
    }
  })

  it('管理员搜索只列出当前成员关系之外的候选人', async () => {
    seed(pinia, 'space_admin')
    mockedSearch.mockResolvedValue([
      { id: 4, name: '新成员' } as never,
      { id: 2, name: '其他成员' } as never,
    ])
    const wrapper = mount(ProvidedPanel, { global: { plugins: [pinia] } })
    const input = wrapper.find('[data-test="governance-invite-search"] input')
    await input.setValue('新')
    await wrapper.find('[data-test="governance-invite-search-btn"]').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('新成员'))
    expect(wrapper.text()).not.toContain('其他成员（#2）')
    wrapper.unmount()
  })
})
