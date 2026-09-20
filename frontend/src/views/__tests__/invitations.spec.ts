import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'

import { ApiError } from '@/api/errors'
import * as spacesApi from '@/api/spaces'
import InvitationsView from '@/views/InvitationsView.vue'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import type { PendingInvitation } from '@/types/api'

vi.mock('@/api/spaces', () => ({
  fetchSpaces: vi.fn().mockResolvedValue([]),
  fetchSpaceMembers: vi.fn().mockResolvedValue([]),
  fetchSpaceProfileRefs: vi.fn().mockResolvedValue([]),
  fetchOwnershipTransfers: vi.fn().mockResolvedValue([]),
  fetchMyInvitations: vi.fn(),
  createSpace: vi.fn(),
  updateSpace: vi.fn(),
  setSpaceLineageLink: vi.fn(),
  inviteToSpace: vi.fn(),
  inviteIntoFamilyHousehold: vi.fn(),
  joinByUser: vi.fn(),
  resolveMembership: vi.fn(),
  approveMembership: vi.fn(),
  setMemberRelationLabel: vi.fn(),
  removeOrWithdrawMembership: vi.fn(),
  requestLineageAccess: vi.fn(),
  fetchSpaceProfileRefs_: vi.fn(),
  createOwnershipTransfer: vi.fn(),
  fetchOwnershipTransfers_: vi.fn(),
  respondOwnershipTransfer: vi.fn(),
  submitManagerApplication: vi.fn(),
  fetchMyManagerApplications: vi.fn(),
  fetchEligibleManagerTargets: vi.fn(),
  fetchMyTransferConsents: vi.fn(),
  decideTransferConsent: vi.fn(),
  fetchSpaceManagementBootstrap: vi.fn(),
  fetchFamilySpaceOptions: vi.fn(),
}))

const mockedFetchMyInvitations = vi.mocked(spacesApi.fetchMyInvitations)
const mockedResolveMembership = vi.mocked(spacesApi.resolveMembership)
const mockedRemoveOrWithdraw = vi.mocked(spacesApi.removeOrWithdrawMembership)

function makeInvitation(overrides: Partial<PendingInvitation> = {}): PendingInvitation {
  return {
    id: 95,
    space_id: 3,
    space_name: '马府',
    space_kind: 'household',
    direction: 'incoming',
    stage: 'awaiting_me',
    counterpart_user_id: 1,
    counterpart_name: '朱元璋',
    relation_label: '女婿',
    owner_approved_at: '2026-09-20T14:09:19',
    updated_at: '2026-09-20T14:09:19',
    ...overrides,
  }
}

let pinia: Pinia

async function mountView(): Promise<VueWrapper> {
  const Harness = defineComponent({
    render() {
      return h('div', [h(NMessageProvider, () => h(InvitationsView))])
    },
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', name: 'home', component: { template: '<div />' } }],
  })
  await router.push('/')
  await router.isReady()
  const wrapper = mount(Harness, { global: { plugins: [pinia, router] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

describe('InvitationsView（跨空间邀请入口：pending 受邀人此前完全不可达）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    pinia = createPinia()
    useAuthStore(pinia).user = {
      id: 31,
      name: '马公',
      pin_must_change: false,
      claim_status: 'claimed',
      profile_status: 'identity_confirmed',
    }
    // 当前空间是「马氏家族」，邀请所属空间是「马府」——两者不同正是缺陷场景
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [
      {
        id: 4,
        name: '马氏家族',
        owner_id: 2,
        kind: 'lineage',
        created_at: '',
        pending_count: 0,
        member_count: 4,
      },
    ]
    spaces.currentSpaceId = 4
    mockedFetchMyInvitations.mockResolvedValue([makeInvitation()])
    mockedResolveMembership.mockResolvedValue({
      id: 95,
      space_id: 3,
      user_id: 31,
      added_by: 1,
      role: 'member',
      status: 'active',
      updated_at: '',
    })
    mockedRemoveOrWithdraw.mockResolvedValue(undefined)
  })

  it('当前空间不是邀请空间时，邀请依然可见可操作（缺陷主场景）', async () => {
    const wrapper = await mountView()

    const incoming = wrapper.findAll('[data-test="incoming-item"]')
    expect(incoming).toHaveLength(1)
    // 自带空间名：不依赖当前空间（当前是「马氏家族」，邀请属于「马府」）
    expect(incoming[0].text()).toContain('马府')
    expect(incoming[0].text()).toContain('来自：朱元璋')
    expect(incoming[0].text()).toContain('关系：女婿')
    expect(incoming[0].find('[data-test="incoming-stage"]').text()).toContain('等待你接受')
    // 当前空间与邀请空间解耦：马氏家族没有出现在邀请行里
    expect(incoming[0].text()).not.toContain('马氏家族')

    const accept = wrapper.find('[data-test="invitation-accept-95"]')
    expect(accept.attributes('disabled')).toBeUndefined()
    await accept.trigger('click')
    await flushPromises()

    expect(mockedResolveMembership).toHaveBeenCalledWith(95, 'accept')
    expect(wrapper.find('[data-test="invitations-feedback"]').text()).toContain('已加入「马府」')
    wrapper.unmount()
  })

  it('等待房主批准时按钮禁用且文案说明原因（前端不作为授权边界）', async () => {
    mockedFetchMyInvitations.mockResolvedValue([
      makeInvitation({ stage: 'awaiting_owner', owner_approved_at: null }),
    ])
    const wrapper = await mountView()

    const item = wrapper.find('[data-test="incoming-item"]')
    expect(item.find('[data-test="incoming-stage"]').text()).toContain('等待房主批准')
    expect(wrapper.find('[data-test="invitation-accept-95"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="invitation-reject-95"]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('我发起的申请只读展示进度，可撤回', async () => {
    mockedFetchMyInvitations.mockResolvedValue([
      makeInvitation({
        id: 96,
        direction: 'outgoing',
        stage: 'awaiting_owner',
        counterpart_name: '马皇后',
        relation_label: '朋友',
        owner_approved_at: null,
      }),
    ])
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="section-incoming"] [data-test="incoming-empty"]').exists()).toBe(true)
    const outgoing = wrapper.findAll('[data-test="outgoing-item"]')
    expect(outgoing).toHaveLength(1)
    expect(outgoing[0].text()).toContain('等待房主批准')

    await wrapper.find('[data-test="invitation-withdraw-96"]').trigger('click')
    await flushPromises()
    expect(mockedRemoveOrWithdraw).toHaveBeenCalledWith(96)
    expect(wrapper.find('[data-test="invitations-feedback"]').text()).toContain('已撤回')
    wrapper.unmount()
  })

  it('对方不可见时用中性占位，不泄露姓名', async () => {
    mockedFetchMyInvitations.mockResolvedValue([
      makeInvitation({ counterpart_name: null, relation_label: null }),
    ])
    const wrapper = await mountView()

    const text = wrapper.find('[data-test="incoming-meta"]').text()
    expect(text).toContain('一位家人')
    expect(text).not.toContain('关系：')
    wrapper.unmount()
  })

  it('空态与加载失败分类：两种分区都空 → 空态；服务端失败 → 可重试错误面板', async () => {
    mockedFetchMyInvitations.mockResolvedValue([])
    const empty = await mountView()
    expect(empty.find('[data-test="incoming-empty"]').exists()).toBe(true)
    expect(empty.find('[data-test="outgoing-empty"]').exists()).toBe(true)
    empty.unmount()

    mockedFetchMyInvitations.mockRejectedValue(new ApiError(503, 'SERVICE_UNAVAILABLE', '服务暂不可用'))
    const failed = await mountView()
    const panel = failed.find('[data-test="invitations-error"]')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('维护中')
    // 失败面板不泄露后端细节
    expect(panel.text()).not.toContain('boom')
    expect(panel.text()).not.toContain('SERVICE_UNAVAILABLE')
    failed.unmount()
  })

  it('接受失败时展示服务端可读文案（未获房主批准 → 403）', async () => {
    mockedResolveMembership.mockRejectedValue(
      new ApiError(403, 'SPACE_FORBIDDEN_ACTOR', '等待该空间管理员批准后再接受'),
    )
    const wrapper = await mountView()

    await wrapper.find('[data-test="invitation-accept-95"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="invitations-feedback"]').text()).toContain('等待该空间管理员批准')
    wrapper.unmount()
  })
})
