import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import * as membersApi from '@/api/members'
import * as spacesApi from '@/api/spaces'
import InviteMemberDialog from '@/components/member/InviteMemberDialog.vue'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import type { FamilySpace, Member, SpaceMemberInfo } from '@/types/api'

/**
 * 邀请成员弹窗（自旧 HomeView 保全的流程组件）：按名字前缀搜索已有账号 →
 * spaces store.invite（服务端创建 pending membership），受邀人本人接受后才 active。
 */

vi.mock('@/api/members', () => ({
  fetchMembers: vi.fn(),
  fetchMembersByPrefix: vi.fn(),
  fetchMember: vi.fn(),
  createMember: vi.fn(),
  updateMember: vi.fn(),
  updateDisclosure: vi.fn(),
  removeMember: vi.fn(),
}))

vi.mock('@/api/spaces', () => ({
  fetchSpaces: vi.fn().mockResolvedValue([]),
  createSpace: vi.fn(),
  fetchSpaceMembers: vi.fn().mockResolvedValue([]),
  fetchSpaceProfileRefs: vi.fn().mockResolvedValue([]),
  fetchOwnershipTransfers: vi.fn().mockResolvedValue([]),
  inviteToSpace: vi.fn(),
  removeOrWithdrawMembership: vi.fn(),
  resolveMembership: vi.fn(),
  joinByUser: vi.fn(),
  getSpacePositions: vi.fn(),
  putSpacePositions: vi.fn(),
  createOwnershipTransfer: vi.fn(),
  respondOwnershipTransfer: vi.fn(),
  submitManagerApplication: vi.fn(),
  fetchMyManagerApplications: vi.fn().mockResolvedValue([]),
  fetchEligibleManagerTargets: vi.fn().mockResolvedValue([]),
  fetchMyTransferConsents: vi.fn().mockResolvedValue([]),
  respondTransferConsent: vi.fn(),
}))

const mockedSearch = vi.mocked(membersApi.fetchMembersByPrefix)
const mockedInviteToSpace = vi.mocked(spacesApi.inviteToSpace)

function makeSpace(): FamilySpace {
  return {
    id: 7,
    name: '我的家庭',
    owner_id: 1,
    kind: 'household',
    created_at: '2026-08-25T00:00:00',
    pending_count: 0,
    member_count: 1,
  }
}

function makeMembership(overrides: Partial<SpaceMemberInfo> = {}): SpaceMemberInfo {
  return {
    id: 1,
    space_id: 7,
    user_id: 1,
    added_by: 1,
    role: 'member',
    status: 'active',
    updated_at: '2026-08-25T00:00:00',
    ...overrides,
  }
}

function makeMember(id: number, name: string): Member {
  return {
    id,
    name,
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
    clan_disclosure: { avatar: false, photos: false, dates: false, bio: false, attachments: false },
    permissions: { edit: true, delete: true },
  }
}

const Host = defineComponent({
  render() {
    return h('div', [h(NMessageProvider, () => h(InviteMemberDialog, { visible: true }))])
  },
})

async function mountDialog() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore(pinia)
  auth.user = {
    id: 1,
    name: '张三',
    is_admin: false,
    pin_must_change: false,
    claim_status: 'claimed',
    profile_status: 'identity_confirmed',
  }
  const spaces = useSpacesStore(pinia)
  spaces.spaces = [makeSpace()]
  spaces.currentSpaceId = 7
  spaces.members = [makeMembership()]
  const wrapper = mount(Host, { global: { plugins: [pinia] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

async function searchKeyword(value: string): Promise<void> {
  const input = document.querySelector<HTMLInputElement>('[data-test="invite-search"]')
  expect(input).not.toBeNull()
  input!.value = value
  input!.dispatchEvent(new Event('input'))
  await flushPromises()
  const submit = document.querySelector('[data-test="invite-search"]')
  submit!.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', bubbles: true }))
  await flushPromises()
}

describe('InviteMemberDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('搜索过滤已有成员后邀请：调用 spaces.invite（POST 当前空间成员），成功后 emit invited', async () => {
    mockedSearch.mockResolvedValue([makeMember(2, '母亲'), makeMember(1, '张三')])
    mockedInviteToSpace.mockResolvedValue(makeMembership())
    const wrapper = await mountDialog()

    await searchKeyword('母')
    expect(mockedSearch).toHaveBeenCalledWith('母')
    // 已在本空间的账号（user_id=1）不出现
    expect(document.body.textContent).toContain('母亲')
    expect(document.body.textContent).not.toContain('张三')

    const inviteButton = document.querySelector<HTMLButtonElement>('[data-test="invite-user-2"]')
    expect(inviteButton).not.toBeNull()
    inviteButton!.click()
    await vi.waitFor(() => expect(mockedInviteToSpace).toHaveBeenCalledWith(7, 2))
    await flushPromises()
    const dialog = wrapper.findComponent(InviteMemberDialog)
    expect(dialog.emitted('invited')).toEqual([[expect.objectContaining({ id: 2 })]])
    wrapper.unmount()
  })

  it('空间 store 判定无 active membership 时不搜索也不邀请', async () => {
    const wrapper = await mountDialog()
    const spaces = useSpacesStore()
    spaces.members = [makeMembership({ status: 'pending' })]
    await flushPromises()

    await searchKeyword('母')
    expect(mockedSearch).not.toHaveBeenCalled()
    expect(mockedInviteToSpace).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
