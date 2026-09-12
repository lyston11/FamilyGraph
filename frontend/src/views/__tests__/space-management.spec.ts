import { mount } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import * as actionCardsApi from '@/api/actionCards'
import * as notificationsApi from '@/api/notifications'
import * as spacesApi from '@/api/spaces'
import { ApiError } from '@/api/errors'
import SpaceManagementView from '@/views/SpaceManagementView.vue'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import type { FamilySpace, NotificationsSnapshot, SpaceMemberInfo } from '@/types/api'

/**
 * SpaceManagementView（09-01 Phase 6，design §5.5；09-06 增模型设置分区）：
 * 六分区侧栏（概览/成员/邀请与申请/Bridge 通知/模型设置/空间设置）、非管理员
 * 安全拒绝态、Bridge 分区无任何操作控件、成员/邀请/申请走既有流程且操作后
 * reload、空间设置仅既有字段（空间名）、模型设置挂载双 Agent 面板。
 */

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
  updateSpace: vi.fn(),
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
  submitManagerApplication: vi.fn(),
  fetchMyManagerApplications: vi.fn().mockResolvedValue([]),
  fetchEligibleManagerTargets: vi.fn().mockResolvedValue([]),
  fetchMyTransferConsents: vi.fn().mockResolvedValue([]),
  respondTransferConsent: vi.fn(),
}))

vi.mock('@/api/notifications', () => ({
  fetchNotifications: vi.fn(),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
}))

vi.mock('@/api/spaceModelSettings', () => ({
  fetchSpaceModelSettings: vi.fn().mockResolvedValue({
    space_id: 7,
    settings: { assistant: null, steward: null },
    catalog: [],
    platform_default: { assistant: null, steward: null, updated_at: null },
  }),
  updateSpaceModelSetting: vi.fn(),
  resetSpaceModelSetting: vi.fn(),
}))

vi.mock('@/api/actionCards', () => ({
  ACTION_CARD_ERRORS: {
    CARD_STATE_CONFLICT: 'CARD_STATE_CONFLICT',
    CARD_EXPIRED: 'CARD_EXPIRED',
    CARD_EXECUTE_REJECTED: 'CARD_EXECUTE_REJECTED',
    SPACE_FORBIDDEN_ACTOR: 'SPACE_FORBIDDEN_ACTOR',
  },
  fetchActionCards: vi.fn().mockResolvedValue([]),
  viewActionCard: vi.fn(),
  dismissActionCard: vi.fn(),
  acceptActionCard: vi.fn(),
  executeActionCard: vi.fn(),
  friendlyActionCardError: vi.fn((code: string) => code),
}))

const mockedFetchSpaceMembers = vi.mocked(spacesApi.fetchSpaceMembers)
const mockedFetchSpaceProfileRefs = vi.mocked(spacesApi.fetchSpaceProfileRefs)
const mockedFetchOwnershipTransfers = vi.mocked(spacesApi.fetchOwnershipTransfers)
const mockedFetchSpaces = vi.mocked(spacesApi.fetchSpaces)
const mockedRemoveOrWithdraw = vi.mocked(spacesApi.removeOrWithdrawMembership)
const mockedUpdateSpace = vi.mocked(spacesApi.updateSpace)
const mockedFetchNotifications = vi.mocked(notificationsApi.fetchNotifications)

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
    role: 'space_admin',
    status: 'active',
    updated_at: '2026-08-29T00:00:00',
    ...overrides,
  }
}

function makeNotification(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    space_id: 7,
    kind: 'bridge' as const,
    payload: {
      title: '跨家族连接请求',
      summary: '两位相关用户已双向同意后生效',
      actor_name: '李四',
      space_name: null,
    },
    domain_status: 'pending' as const,
    action_card: null,
  suggestion: null,
    created_at: '2026-09-01T08:00:00',
    read_at: null,
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
  userOverrides: Record<string, unknown> = {},
  notificationsResult: NotificationsSnapshot | Error = {
    data: { space_id: 7, items: [], unread_count: 0 },
    etag: null,
  },
  initialPath = '/spaces/7/manage',
): Promise<{ wrapper: ReturnType<typeof mount>; router: ReturnType<typeof createRouter> }> {
  const auth = useAuthStore(pinia)
  auth.user = {
    id: 1,
    name: '空间用户',
    pin_must_change: false,
    claim_status: 'claimed',
    profile_status: 'identity_confirmed',
    ...userOverrides,
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
  mockedFetchSpaces.mockResolvedValue([makeSpace()])
  if (notificationsResult instanceof Error) {
    mockedFetchNotifications.mockRejectedValue(notificationsResult)
  } else {
    mockedFetchNotifications.mockResolvedValue(notificationsResult)
  }

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'family-space', component: { template: '<div />' } },
      { path: '/spaces/:spaceId/manage', name: 'space-management', component: SpaceManagementView },
    ],
  })
  await router.push(initialPath)
  await router.isReady()
  const wrapper = mount(ProvidedManagementView, {
    global: { plugins: [pinia, router] },
    attachTo: document.body,
  })
  await vi.waitFor(() => expect(mockedFetchSpaceMembers).toHaveBeenCalledWith(7))
  return { wrapper, router }
}

async function openSection(
  wrapper: ReturnType<typeof mount>,
  key: string,
): Promise<void> {
  await wrapper.find(`[data-test="management-tab-${key}"]`).trigger('click')
  await vi.waitFor(() =>
    expect(wrapper.find(`[data-test="section-${key}"]`).exists()).toBe(true),
  )
}

describe('SpaceManagementView 六分区侧栏（design §5.5；09-06 增模型设置）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('侧栏含六个分区，默认概览；逐分区切换渲染对应内容', async () => {
    const pinia = createPinia()
    const { wrapper } = await mountManagement(pinia, 'space_admin')

    const tabs = wrapper.findAll('[data-test^="management-tab-"]')
    expect(tabs.map((tab) => tab.text())).toEqual([
      '概览',
      '成员',
      '邀请与申请',
      'Bridge 通知',
      '模型设置',
      '空间设置',
    ])
    expect(wrapper.find('[data-test="section-overview"]').exists()).toBe(true)

    await openSection(wrapper, 'members')
    expect(wrapper.find('[data-test="section-overview"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="space-governance-panel"]').exists()).toBe(true)

    await openSection(wrapper, 'invites')
    expect(wrapper.find('[data-test="manager-application-panel"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="open-invite-dialog"]').exists()).toBe(true)

    await openSection(wrapper, 'bridge')
    expect(wrapper.find('[data-test="bridge-notices-empty"]').exists()).toBe(true)

    await openSection(wrapper, 'models')
    expect(wrapper.find('[data-test="model-settings-panel"]').exists()).toBe(true)
    // 双 Agent 维度区块（assistant / steward 各一）
    expect(wrapper.find('[data-test="model-settings-assistant"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="model-settings-steward"]').exists()).toBe(true)

    await openSection(wrapper, 'settings')
    expect(wrapper.find('[data-test="space-name-input"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('概览：空间名称、类型、管理员标记与成员概要来自 spaces/members store', async () => {
    const pinia = createPinia()
    const { wrapper } = await mountManagement(pinia, 'space_admin')

    expect(wrapper.find('[data-test="management-space-name"]').text()).toBe('王家空间')
    expect(wrapper.find('[data-test="management-space-kind"]').text()).toContain('家庭空间')
    expect(wrapper.find('[data-test="management-current-role"]').text()).toContain('空间管理员')
    expect(wrapper.find('[data-test="management-counts"]').text()).toBe('2 / 1')
    wrapper.unmount()
  })

  it('成员：移除成员走既有 removeOrWithdrawMembership 命令并触发服务端 reload', async () => {
    const pinia = createPinia()
    mockedRemoveOrWithdraw.mockResolvedValue(undefined)
    const { wrapper } = await mountManagement(pinia, 'space_admin')

    await openSection(wrapper, 'members')
    // 管理员操作列：active 非本人成员可移除，pending 成员可撤回；无本人操作
    expect(wrapper.find('[data-test="member-remove-2"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="member-withdraw-3"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="member-remove-1"]').exists()).toBe(false)

    mockedFetchSpaceMembers.mockClear()
    await wrapper.find('[data-test="member-remove-2"]').trigger('click')
    await vi.waitFor(() => {
      const confirmButton = document.body.querySelector<HTMLButtonElement>(
        '.n-popconfirm .n-button--primary-type',
      )
      if (confirmButton === null) throw new Error('popconfirm positive button not rendered')
      confirmButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await vi.waitFor(() => expect(mockedRemoveOrWithdraw).toHaveBeenCalledWith(2))
    // 操作后 store 重新读取服务端（load() → fetchSpaces + fetchSpaceMembers）
    await vi.waitFor(() => expect(mockedFetchSpaces).toHaveBeenCalled())
    await vi.waitFor(() => expect(mockedFetchSpaceMembers).toHaveBeenCalledWith(7))
    wrapper.unmount()
  })

  it('邀请与申请：挂载既有 InviteMemberDialog / 申请面板 / ActionCard 待办入口', async () => {
    const pinia = createPinia()
    const { wrapper } = await mountManagement(pinia, 'space_admin')

    await openSection(wrapper, 'invites')
    expect(wrapper.find('[data-test="manager-application-panel"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="steward-inbox-entry"]').exists()).toBe(true)

    await wrapper.find('[data-test="open-invite-dialog"]').trigger('click')
    await vi.waitFor(() => {
      expect(document.body.querySelector('[data-test="invite-dialog"]')).not.toBeNull()
    })
    // 邀请搜索框来自既有 InviteMemberDialog 流程组件
    expect(document.body.querySelector('[data-test="invite-search"]')).not.toBeNull()
    wrapper.unmount()
  })

  it('Bridge 分区只读：仅展示 bridge 类通知，无任何操作控件（按钮/链接/操作 data-test）', async () => {
    const pinia = createPinia()
    const { wrapper } = await mountManagement(pinia, 'space_admin', {}, {
      data: {
        space_id: 7,
        unread_count: 2,
        items: [
          makeNotification({ id: 11, domain_status: 'pending' }),
          makeNotification({ id: 12, domain_status: 'active', read_at: '2026-09-01T09:00:00' }),
          makeNotification({ id: 13, kind: 'action_card', suggestion: null,
    action_card: { card_id: 5, revision: 1 } }),
          makeNotification({ id: 14, kind: 'space_membership' }),
        ],
      },
      etag: null,
    } satisfies NotificationsSnapshot)

    await openSection(wrapper, 'bridge')
    // 只过滤出 bridge 类通知（安全状态 + 通知时间）
    await vi.waitFor(() =>
      expect(wrapper.findAll('[data-test="bridge-notice-item"]')).toHaveLength(2),
    )
    const items = wrapper.findAll('[data-test="bridge-notice-item"]')
    expect(items[0].text()).toContain('待处理')
    expect(items[1].text()).toContain('已生效')
    // 通知时间（created_at）可见
    expect(items[0].text()).toContain('2026-09-01 08:00')

    // 红线：管理员对 bridge 无批准/否决/修改/撤销控件——分区零按钮、零链接，
    // 且不存在任何操作 data-test 锚点
    const section = wrapper.find('[data-test="section-bridge"]')
    expect(section.findAll('button')).toHaveLength(0)
    expect(section.findAll('a')).toHaveLength(0)
    for (const action of ['approve', 'reject', 'consent', 'revoke', 'mark-read']) {
      expect(section.find(`[data-test*="${action}"]`).exists()).toBe(false)
    }
    // 未接入任何通知/卡片变更命令
    expect(notificationsApi.markNotificationRead).not.toHaveBeenCalled()
    expect(notificationsApi.markAllNotificationsRead).not.toHaveBeenCalled()
    expect(actionCardsApi.viewActionCard).not.toHaveBeenCalled()
    expect(actionCardsApi.acceptActionCard).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('Bridge 通知端点 404：按真实原因分类的安全态', async () => {
    const pinia = createPinia()
    const { wrapper } = await mountManagement(
      pinia,
      'space_admin',
      {},
      new ApiError(404, 'HTTP_ERROR', 'not found'),
    )

    await openSection(wrapper, 'bridge')
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="bridge-notice-error"]').exists()).toBe(true),
    )
    expect(wrapper.find('[data-test="bridge-notice-error"]').text()).toContain('通知服务未部署')
    expect(wrapper.find('[data-test="bridge-notice-item"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('空间设置：仅空间名一个既有字段；保存走既有 PATCH 并回写服务端响应', async () => {
    const pinia = createPinia()
    mockedUpdateSpace.mockResolvedValue(makeSpace({ name: '王家新居' }))
    const { wrapper } = await mountManagement(pinia, 'space_admin')

    await openSection(wrapper, 'settings')
    // 仅一个输入（空间名），无任何授权/角色配置控件
    const section = wrapper.find('[data-test="section-settings"]')
    expect(section.findAll('input')).toHaveLength(1)
    expect(wrapper.find('[data-test="space-name-save"]').attributes('disabled')).toBeDefined()

    const input = wrapper.find('input[data-test="space-name-input"]')
    await input.setValue('王家新居')
    expect(wrapper.find('[data-test="space-name-save"]').attributes('disabled')).toBeUndefined()
    await wrapper.find('[data-test="space-name-save"]').trigger('click')

    await vi.waitFor(() => expect(mockedUpdateSpace).toHaveBeenCalledWith(7, '王家新居'))
    const spaces = useSpacesStore(pinia)
    await vi.waitFor(() => expect(spaces.spaces[0].name).toBe('王家新居'))
    expect((input.element as HTMLInputElement).value).toBe('王家新居')
    wrapper.unmount()
  })
})

describe('SpaceManagementView 非管理员安全拒绝态（双保险）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('member / 平台运营标记（家庭主体）均渲染拒绝态且无分区与管理面板', async () => {
    const cases: Array<{ role: SpaceMemberInfo['role']; userOverrides?: Record<string, unknown> }> = [
      { role: 'member' },
      { role: 'member', userOverrides: { is_admin: true } },
    ]
    for (const testCase of cases) {
      const pinia = createPinia()
      const { wrapper } = await mountManagement(pinia, testCase.role, testCase.userOverrides)

      expect(wrapper.find('[data-test="management-denied"]').exists()).toBe(true)
      expect(wrapper.find('[data-test="management-nav"]').exists()).toBe(false)
      expect(wrapper.find('[data-test="space-governance-panel"]').exists()).toBe(false)
      expect(wrapper.find('[data-test="section-overview"]').exists()).toBe(false)
      wrapper.unmount()
      document.body.innerHTML = ''
    }
  })
})

describe('SpaceManagementView 分区深链（09-06 R5：?section= 直达与写回）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('挂载时 ?section=models 直达模型设置分区，URL 保留该参数', async () => {
    const pinia = createPinia()
    const { wrapper, router } = await mountManagement(
      pinia,
      'space_admin',
      {},
      undefined,
      '/spaces/7/manage?section=models',
    )
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="section-models"]').exists()).toBe(true)
    })
    expect(wrapper.find('[data-test="section-overview"]').exists()).toBe(false)
    expect(router.currentRoute.value.query.section).toBe('models')
    wrapper.unmount()
  })

  it('非法 section 值回退概览', async () => {
    const pinia = createPinia()
    const { wrapper } = await mountManagement(
      pinia,
      'space_admin',
      {},
      undefined,
      '/spaces/7/manage?section=../hack',
    )
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="section-overview"]').exists()).toBe(true)
    })
    expect(wrapper.find('[data-test="section-models"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('切分区时把 section 写回 query（URL 可分享）', async () => {
    const pinia = createPinia()
    const { wrapper, router } = await mountManagement(pinia, 'space_admin')
    await openSection(wrapper, 'models')
    await vi.waitFor(() => {
      expect(router.currentRoute.value.query.section).toBe('models')
    })
    await openSection(wrapper, 'overview')
    await vi.waitFor(() => {
      expect(router.currentRoute.value.query.section).toBe('overview')
    })
    wrapper.unmount()
  })
})
