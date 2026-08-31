import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import * as graphApi from '@/api/graph'
import * as membersApi from '@/api/members'
import * as spacesApi from '@/api/spaces'
import FamilySpaceView from '@/views/FamilySpaceView.vue'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import type { FamilySpace } from '@/types/api'

vi.mock('@/api/graph', () => ({
  fetchMyGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [], scope: 'family' }),
  createConnectionRequest: vi.fn(),
  fetchIncomingConnections: vi.fn().mockResolvedValue([]),
  resolveConnection: vi.fn(),
  revokeRelation: vi.fn(),
}))

vi.mock('@/api/members', () => ({
  fetchMembers: vi.fn().mockResolvedValue([]),
  fetchMembersByPrefix: vi.fn().mockResolvedValue([]),
  fetchMember: vi.fn(),
  createMember: vi.fn(),
  updateMember: vi.fn(),
  updateDisclosure: vi.fn(),
  removeMember: vi.fn(),
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
  getSpacePositions: vi.fn().mockResolvedValue([]),
  putSpacePositions: vi.fn(),
  createOwnershipTransfer: vi.fn(),
  fetchOwnershipTransfers: vi.fn().mockResolvedValue([]),
  respondOwnershipTransfer: vi.fn(),
}))

vi.mock('@vue-flow/core', () => ({
  VueFlow: defineComponent({ template: '<div data-test="mock-flow"><slot /></div>' }),
  useVueFlow: () => ({ fitView: vi.fn() }),
}))
vi.mock('@vue-flow/controls', () => ({
  Controls: defineComponent({ template: '<div />' }),
}))

vi.mock('@/components/common/GlobalSearch.vue', () => ({ default: defineComponent({ template: '<div />' }) }))
vi.mock('@/components/actioncard/ActionCardInbox.vue', () => ({ default: defineComponent({ template: '<div />' }) }))
vi.mock('@/components/kinship/RelationLookup.vue', () => ({ default: defineComponent({ template: '<div />' }) }))
vi.mock('@/components/member/PendingProfileRefs.vue', () => ({ default: defineComponent({ template: '<div />' }) }))
vi.mock('@/components/member/ProfileDrawer.vue', () => ({ default: defineComponent({ template: '<div />' }) }))
vi.mock('@/components/member/SpaceGovernanceDialog.vue', () => ({ default: defineComponent({ template: '<div />' }) }))
vi.mock('@/components/canvas/MemberNode.vue', () => ({ default: defineComponent({ template: '<div />' }) }))

const mockedGraph = vi.mocked(graphApi.fetchMyGraph)
const mockedFetchSpaces = vi.mocked(spacesApi.fetchSpaces)
const mockedFetchSpaceMembers = vi.mocked(spacesApi.fetchSpaceMembers)
const mockedFetchMembers = vi.mocked(membersApi.fetchMembers)

function makeSpace(id: number): FamilySpace {
  return {
    id,
    name: `空间${id}`,
    owner_id: 1,
    kind: 'household',
    created_at: '2026-08-29T00:00:00',
    pending_count: 0,
    member_count: 1,
  }
}

async function mountFamily(spaces: FamilySpace[]) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore(pinia)
  auth.user = {
    id: 1,
    name: '我',
    is_admin: false,
    pin_must_change: false,
    claim_status: 'claimed',
    profile_status: 'identity_confirmed',
  }
  mockedFetchSpaces.mockResolvedValue(spaces)
  mockedFetchSpaceMembers.mockImplementation(async (spaceId) => [{
    id: spaceId,
    space_id: spaceId,
    user_id: 1,
    added_by: 1,
    role: 'space_admin',
    status: 'active',
    updated_at: '2026-08-29T00:00:00',
  }])
  mockedFetchMembers.mockResolvedValue([])

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', name: 'family-space', component: { template: '<div />' } }],
  })
  await router.push('/')
  await router.isReady()
  const Host = defineComponent({
    render: () => h(NMessageProvider, null, { default: () => h(FamilySpaceView) }),
  })
  const wrapper = mount(Host, {
    global: { plugins: [pinia, router] },
    attachTo: document.body,
  })
  await new Promise((resolve) => setTimeout(resolve))
  return { wrapper, pinia }
}

describe('FamilySpaceView graph space boundary', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    document.body.innerHTML = ''
    mockedGraph.mockResolvedValue({ nodes: [], edges: [], scope: 'family' })
    mockedFetchSpaceMembers.mockResolvedValue([])
  })

  it('initially loads the active space graph with space_id', async () => {
    const { wrapper } = await mountFamily([makeSpace(7)])

    await vi.waitFor(() => expect(mockedGraph).toHaveBeenCalledWith('family', 5, 7))
    expect(mockedGraph.mock.calls.every((call) => call[2] === 7)).toBe(true)
    wrapper.unmount()
  })

  it('space switching reloads the new graph without retaining the old scope', async () => {
    const { wrapper, pinia } = await mountFamily([makeSpace(7), makeSpace(8)])
    const spaces = useSpacesStore(pinia)
    await vi.waitFor(() => expect(mockedGraph).toHaveBeenCalledWith('family', 5, 7))

    spaces.currentSpaceId = 8
    await vi.waitFor(() => expect(mockedGraph).toHaveBeenCalledWith('family', 5, 8))
    expect(spaces.currentSpaceId).toBe(8)
    wrapper.unmount()
  })

  it('scope switching keeps the active space_id', async () => {
    const { wrapper, pinia } = await mountFamily([makeSpace(9)])
    await vi.waitFor(() => expect(mockedGraph).toHaveBeenCalledWith('family', 5, 9))

    const scopeButtons = wrapper.findAll('.scope-switch input[type="radio"]')
    expect(scopeButtons).toHaveLength(2)
    ;(scopeButtons[1].element as HTMLInputElement).click()
    await vi.waitFor(() => expect(mockedGraph).toHaveBeenCalledWith('clan', 5, 9))
    wrapper.unmount()
    void pinia
  })

  it('does not request a global graph when there is no active space', async () => {
    const { wrapper } = await mountFamily([])

    await new Promise((resolve) => setTimeout(resolve))
    expect(mockedGraph).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
