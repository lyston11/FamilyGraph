import { mount } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import * as spacesApi from '@/api/spaces'
import SpaceManagementView from '@/views/SpaceManagementView.vue'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import type { FamilySpace, SpaceMemberInfo } from '@/types/api'

vi.mock('@/api/members', () => ({
  fetchMembersByPrefix: vi.fn(),
}))

vi.mock('@/api/spaces', () => ({
  fetchSpaces: vi.fn(),
  createSpace: vi.fn(),
  fetchSpaceMembers: vi.fn(),
  fetchSpaceProfileRefs: vi.fn(),
  inviteToSpace: vi.fn(),
  removeOrWithdrawMembership: vi.fn(),
  resolveMembership: vi.fn(),
  joinByUser: vi.fn(),
  getSpacePositions: vi.fn(),
  putSpacePositions: vi.fn(),
  createOwnershipTransfer: vi.fn(),
  fetchOwnershipTransfers: vi.fn(),
  respondOwnershipTransfer: vi.fn(),
}))

const mockedFetchSpaceMembers = vi.mocked(spacesApi.fetchSpaceMembers)
const mockedFetchSpaceProfileRefs = vi.mocked(spacesApi.fetchSpaceProfileRefs)
const mockedFetchOwnershipTransfers = vi.mocked(spacesApi.fetchOwnershipTransfers)

function makeSpace(overrides: Partial<FamilySpace> = {}): FamilySpace {
  return {
    id: 7,
    name: '王家空间',
    owner_id: 1,
    kind: 'household',
    created_at: '2026-08-29T00:00:00',
    pending_count: 1,
    member_count: 2,
    ...overrides,
  }
}

function makeMember(overrides: Partial<SpaceMemberInfo> = {}): SpaceMemberInfo {
  return {
    id: 1,
    space_id: 7,
    user_id: 1,
    user_name: '空间用户',
    added_by: 1,
    role: 'owner',
    status: 'active',
    updated_at: '2026-08-29T00:00:00',
    ...overrides,
  }
}

const ProvidedManagementView = defineComponent({
  render() {
    return h(NMessageProvider, () => h(SpaceManagementView))
  },
})

async function mountManagement(
  pinia: Pinia,
  role: SpaceMemberInfo['role'],
): Promise<{ wrapper: ReturnType<typeof mount>; router: ReturnType<typeof createRouter> }> {
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
  spaces.currentSpaceId = 7
  mockedFetchSpaceMembers.mockResolvedValue([
    makeMember({ role }),
    makeMember({ id: 2, user_id: 2, user_name: '另一位成员', role: 'member' }),
    makeMember({ id: 3, user_id: 3, user_name: '待确认成员', role: 'member', status: 'pending' }),
  ])
  mockedFetchOwnershipTransfers.mockResolvedValue([])
  mockedFetchSpaceProfileRefs.mockResolvedValue([
    { profile_id: 9, name: '待确档长辈', added_at: '2026-08-29T00:00:00' },
  ])

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'family-space', component: { template: '<div />' } },
      { path: '/spaces/:spaceId/manage', name: 'space-management', component: SpaceManagementView },
    ],
  })
  await router.push('/spaces/7/manage')
  await router.isReady()
  const wrapper = mount(ProvidedManagementView, {
    global: { plugins: [pinia, router] },
  })
  await vi.waitFor(() => expect(mockedFetchSpaceMembers).toHaveBeenCalledWith(7))
  return { wrapper, router }
}

describe('SpaceManagementView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('owner sees the management overview, governance panel, and pending profile references', async () => {
    const pinia = createPinia()
    const { wrapper } = await mountManagement(pinia, 'owner')
    await vi.waitFor(() => expect(wrapper.find('[data-test="space-overview"]').exists()).toBe(true))
    await vi.waitFor(() => expect(wrapper.find('[data-test="profile-ref-section"]').exists()).toBe(true))

    expect(wrapper.find('[data-test="space-management-view"]').exists()).toBe(true)
    expect(wrapper.find('h1').text()).toBe('家庭空间管理')
    expect(wrapper.find('[data-test="management-space-name"]').text()).toBe('王家空间')
    expect(wrapper.find('[data-test="management-space-kind"]').text()).toContain('家庭空间')
    expect(wrapper.find('[data-test="management-current-role"]').text()).toContain('空间所有者')
    expect(wrapper.find('[data-test="management-counts"]').text()).toBe('2 / 1')
    expect(wrapper.find('[data-test="space-governance-panel"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="management-profile-refs"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="profile-ref-section"]').text()).toContain('待确档长辈')
    expect(wrapper.find('[data-test="management-denied"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('space_admin can view the management page and invite but cannot transfer ownership', async () => {
    const pinia = createPinia()
    const { wrapper } = await mountManagement(pinia, 'space_admin')
    await vi.waitFor(() => expect(wrapper.find('[data-test="space-overview"]').exists()).toBe(true))

    expect(wrapper.find('[data-test="management-current-role"]').text()).toContain('空间管理员')
    expect(wrapper.find('[data-test="governance-invite-search"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="transfer-target-select"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('member and guest receive a fail-closed denied state without governance actions', async () => {
    for (const role of ['member', 'guest'] as const) {
      const pinia = createPinia()
      const { wrapper } = await mountManagement(pinia, role)

      expect(wrapper.find('[data-test="management-denied"]').exists()).toBe(true)
      expect(wrapper.find('[data-test="space-governance-panel"]').exists()).toBe(false)
      wrapper.unmount()
    }
  })
})
