import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'

import * as actionCardsApi from '@/api/actionCards'
import { ApiError } from '@/api/errors'
import * as notificationsApi from '@/api/notifications'
import * as pfvApi from '@/api/personalFamilyView'
import NotificationsView from '@/views/NotificationsView.vue'
import { useActionCardsStore } from '@/stores/actionCards'
import { useSpacesStore } from '@/stores/spaces'
import type { NotificationsSnapshot } from '@/types/api'
import type { ActionCard } from '@/types/actionCard'

vi.mock('@/api/notifications', () => ({
  fetchNotifications: vi.fn(),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
}))

vi.mock('@/api/personalFamilyView', () => ({
  fetchPersonalFamilyView: vi.fn(),
}))

vi.mock('@/api/actionCards', () => ({
  ACTION_CARD_ERRORS: {
    CARD_STATE_CONFLICT: 'CARD_STATE_CONFLICT',
    CARD_EXPIRED: 'CARD_EXPIRED',
    CARD_EXECUTE_REJECTED: 'CARD_EXECUTE_REJECTED',
    SPACE_FORBIDDEN_ACTOR: 'SPACE_FORBIDDEN_ACTOR',
  },
  fetchActionCards: vi.fn(),
  viewActionCard: vi.fn(),
  dismissActionCard: vi.fn(),
  acceptActionCard: vi.fn(),
  executeActionCard: vi.fn(),
  friendlyActionCardError: vi.fn((code: string) => code),
}))

vi.mock('@/api/spaces', () => ({
  fetchSpaces: vi.fn().mockResolvedValue([]),
  fetchSpaceMembers: vi.fn().mockResolvedValue([]),
  fetchSpaceProfileRefs: vi.fn().mockResolvedValue([]),
  fetchOwnershipTransfers: vi.fn().mockResolvedValue([]),
  createSpace: vi.fn(),
  inviteToSpace: vi.fn(),
  removeOrWithdrawMembership: vi.fn(),
  resolveMembership: vi.fn(),
  joinByUser: vi.fn(),
  getSpacePositions: vi.fn(),
  putSpacePositions: vi.fn(),
  createOwnershipTransfer: vi.fn(),
  respondOwnershipTransfer: vi.fn(),
}))

const mockedFetchNotifications = vi.mocked(notificationsApi.fetchNotifications)
const mockedMarkRead = vi.mocked(notificationsApi.markNotificationRead)
const mockedMarkAllRead = vi.mocked(notificationsApi.markAllNotificationsRead)
const mockedFetchPersonalFamilyView = vi.mocked(pfvApi.fetchPersonalFamilyView)
const mockedFetchActionCards = vi.mocked(actionCardsApi.fetchActionCards)

function makeItem(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    space_id: 7,
    kind: 'action_card' as const,
    payload: {
      title: '共建家庭空间建议',
      summary: '你们可以共建一个家庭空间',
      actor_name: '李四',
      space_name: null,
    },
    domain_status: 'pending' as const,
    suggestion: null,
    action_card: { card_id: 55, revision: 2 },
    created_at: '2026-09-01T08:00:00',
    read_at: null,
    ...overrides,
  }
}

function makeSnapshot(items: ReturnType<typeof makeItem>[], unreadCount: number): NotificationsSnapshot {
  return { data: { space_id: 7, items, unread_count: unreadCount }, etag: 'W/"n1"' }
}

function makeCard(overrides: Partial<ActionCard> = {}): ActionCard {
  return {
    id: 55,
    kind: 'household_link',
    space_id: 7,
    subject_user: { id: 1, name: '张三' },
    object_user: { id: 10, name: '李四' },
    reason_text: '你们是配偶',
    evidence: { fact_ids: [3], path_summary: null, evidence_version: 1 },
    proposed_action: { type: 'create_household', params: {} },
    privacy_effect: '对方将看到你的名字',
    state: 'pending',
    expires_at: null,
    created_at: '2026-08-26T00:00:00',
    revision: 2,
    ...overrides,
  }
}

let pinia: Pinia

async function mountNotifications(): Promise<VueWrapper> {
  const Harness = defineComponent({
    render() {
      return h('div', [h(NMessageProvider, () => h(NotificationsView))])
    },
  })
  // NotificationsView 使用 useRouter：注入内存路由消除 router injection 警告
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', name: 'home', component: { template: '<div />' } }],
  })
  await router.push('/')
  await router.isReady()
  const wrapper = mount(Harness, { global: { plugins: [pinia, router] }, attachTo: document.body })
  await new Promise((resolve) => setTimeout(resolve))
  return wrapper
}

describe('NotificationsView（PRD §2.6：三分区 + 已读与 ActionCard 严格分离）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    pinia = createPinia()
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [
      { id: 7, name: '我家', owner_id: 1, kind: 'household', created_at: '', pending_count: 0, member_count: 2 },
    ]
    spaces.currentSpaceId = 7
    mockedFetchPersonalFamilyView.mockResolvedValue(null)
    mockedFetchActionCards.mockResolvedValue([])
    mockedMarkRead.mockResolvedValue({ id: 1, read_at: '2026-09-01T09:00:00' })
    mockedMarkAllRead.mockResolvedValue({ space_id: 7, marked_count: 3 })
  })

  it('三分区归类：待我处理（ActionCard pending）/ 通知 / 已完成·历史', async () => {
    mockedFetchNotifications.mockResolvedValue(
      makeSnapshot(
        [
          makeItem(), // 待我处理：action_card + pending
          makeItem({ id: 2, kind: 'relation', domain_status: 'pending', action_card: null }), // 通知
          makeItem({
            id: 3,
            kind: 'space_membership',
            domain_status: 'accepted',
            action_card: null,
  suggestion: null,
            read_at: '2026-09-01T09:00:00',
          }), // 历史：已读 + 领域终态
          makeItem({
            id: 4,
            kind: 'action_card',
            domain_status: 'done',
            read_at: null,
          }), // 通知：领域终态但未读（先告知后归档）
        ],
        2,
      ),
    )
    const wrapper = await mountNotifications()

    expect(wrapper.find('[data-test="section-pending"]').findAll('[data-test="notice-item"]')).toHaveLength(1)
    expect(wrapper.find('[data-test="section-notices"]').findAll('[data-test="notice-item"]')).toHaveLength(2)
    expect(wrapper.find('[data-test="section-history"]').findAll('[data-test="notice-item"]')).toHaveLength(1)
    // 领域状态以状态标签展示
    const historyItem = wrapper.find('[data-test="section-history"] [data-test="notice-item"]')
    expect(historyItem.find('[data-test="domain-status"]').text()).toContain('已接受')
    wrapper.unmount()
  })

  it('打开通知只标记已读并重读列表；绝不调用任何 ActionCard 接口', async () => {
    mockedFetchNotifications.mockResolvedValue(makeSnapshot([makeItem()], 1))
    const wrapper = await mountNotifications()

    // 打开（点击）通知行 → 只调已读端点
    mockedFetchNotifications.mockClear()
    mockedFetchNotifications.mockResolvedValue(
      makeSnapshot([makeItem({ read_at: '2026-09-01T09:00:00' })], 0),
    )
    await wrapper.find('[data-test="section-pending"] [data-test="notice-item"]').trigger('click')
    await vi.waitFor(() => expect(mockedMarkRead).toHaveBeenCalledWith(1))
    await vi.waitFor(() => expect(mockedFetchNotifications).toHaveBeenCalledWith(7, 'W/"n1"'))

    // 红线：通知已读不触发 ActionCard 的任何状态转换接口
    expect(actionCardsApi.viewActionCard).not.toHaveBeenCalled()
    expect(actionCardsApi.acceptActionCard).not.toHaveBeenCalled()
    expect(actionCardsApi.dismissActionCard).not.toHaveBeenCalled()
    expect(actionCardsApi.executeActionCard).not.toHaveBeenCalled()

    // 重读后已读标签消失；domain_status 与 ActionCard 引用保持服务端原状
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="section-pending"] [data-test="unread-badge"]').exists()).toBe(false)
    })
    expect(
      wrapper
        .find('[data-test="section-pending"] [data-test="notice-item"]')
        .find('[data-test="domain-status"]')
        .text(),
    ).toContain('待处理')
    wrapper.unmount()
  })

  it('全部标记已读：只调全部已读端点 + 重读列表，不改 ActionCard 状态', async () => {
    mockedFetchNotifications.mockResolvedValue(makeSnapshot([makeItem()], 1))
    const wrapper = await mountNotifications()

    mockedFetchNotifications.mockClear()
    mockedFetchNotifications.mockResolvedValue(
      makeSnapshot([makeItem({ read_at: '2026-09-01T09:30:00' })], 0),
    )
    await wrapper.find('[data-test="mark-all-read"]').trigger('click')

    await vi.waitFor(() => expect(mockedMarkAllRead).toHaveBeenCalledWith(7))
    expect(actionCardsApi.acceptActionCard).not.toHaveBeenCalled()
    expect(actionCardsApi.executeActionCard).not.toHaveBeenCalled()
    expect(actionCardsApi.dismissActionCard).not.toHaveBeenCalled()
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="unread-badge"]').exists()).toBe(false)
    })
    wrapper.unmount()
  })

  it('「去处理」打开既有 ActionCard 流程（Inbox + 卡片），通知页自身不执行卡片动作', async () => {
    mockedFetchNotifications.mockResolvedValue(makeSnapshot([makeItem()], 1))
    mockedFetchActionCards.mockResolvedValue([makeCard()])
    const wrapper = await mountNotifications()

    await wrapper.find('[data-test="go-process"]').trigger('click')
    await vi.waitFor(() => expect(mockedFetchActionCards).toHaveBeenCalledWith(7))
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="steward-inbox-panel"]').exists()).toBe(true)
    })
    expect(wrapper.find('[data-test="action-card-item"]').exists()).toBe(true)
    // 通知页自身没有 accept/reject/execute 按钮语义（动作都在卡片流程内）
    const actionCards = useActionCardsStore(pinia)
    expect(actionCards.cardsOf(7)).toHaveLength(1)
    wrapper.unmount()
  })

  it('bridge active 通知展示后触发当前空间投影只读重载（reloadAfterBridgeChange）', async () => {
    mockedFetchNotifications.mockResolvedValue(
      makeSnapshot(
        [
          makeItem({
            id: 5,
            kind: 'bridge',
            domain_status: 'active',
            action_card: null,
  suggestion: null,
            payload: { title: '家族连接已生效', summary: null, actor_name: null, space_name: null },
          }),
        ],
        1,
      ),
    )
    const wrapper = await mountNotifications()

    await vi.waitFor(() => expect(mockedFetchPersonalFamilyView).toHaveBeenCalledWith(7, null, { progressive: true }))
    // 只读重载：只有 GET 投影请求，无任何 Bridge 写操作
    expect(mockedFetchPersonalFamilyView).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('通知端点 404：「通知服务未部署」可行动安全态，无假数据', async () => {
    mockedFetchNotifications.mockRejectedValue(new ApiError(404, 'HTTP_ERROR', 'not found'))
    const wrapper = await mountNotifications()

    expect(wrapper.find('[data-test="notifications-error"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('通知服务未部署')
    expect(wrapper.find('[data-test="section-pending"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('共建家庭空间建议')
    wrapper.unmount()
  })

  it('非 404 错误：可解释失败态 + 可重试', async () => {
    mockedFetchNotifications.mockRejectedValue(new ApiError(0, 'NETWORK_ERROR', '网络异常'))
    const wrapper = await mountNotifications()

    expect(wrapper.find('[data-test="notifications-error"]').exists()).toBe(true)
    await wrapper.find('[data-test="notifications-retry"]').trigger('click')
    wrapper.unmount()
  })

  it('空间管理员对 bridge 通知只有查看权：无任何 approve/reject/consent/revoke 控件', async () => {
    const spaces = useSpacesStore(pinia)
    spaces.members = [
      {
        id: 1,
        space_id: 7,
        user_id: 1,
        added_by: 1,
        role: 'space_admin',
        status: 'active',
        updated_at: '2026-08-25T00:00:00',
      },
    ]
    mockedFetchNotifications.mockResolvedValue(
      makeSnapshot(
        [
          makeItem({
            id: 6,
            kind: 'bridge',
            domain_status: 'pending',
            action_card: null,
  suggestion: null,
            payload: {
              title: '跨族谱连接请求',
              summary: '另一侧空间请求建立连接',
              actor_name: '王五',
              space_name: '李氏族谱',
            },
          }),
        ],
        1,
      ),
    )
    const wrapper = await mountNotifications()

    const bridgeItem = wrapper.find('[data-test="section-notices"] [data-test="notice-item"]')
    expect(bridgeItem.exists()).toBe(true)
    expect(bridgeItem.text()).toContain('跨族谱连接请求')
    // 查看权边界：不渲染批准/否决/同意/撤销控件（文案与 data-test 双重断言）
    expect(bridgeItem.find('[data-test="approve-bridge"]').exists()).toBe(false)
    expect(bridgeItem.find('[data-test="reject-bridge"]').exists()).toBe(false)
    expect(bridgeItem.find('[data-test="consent-bridge"]').exists()).toBe(false)
    expect(bridgeItem.find('[data-test="revoke-bridge"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="approve-bridge"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="reject-bridge"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="consent-bridge"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="revoke-bridge"]').exists()).toBe(false)
    const bridgeText = bridgeItem.text()
    for (const forbidden of ['批准', '否决', '同意连接', '撤销连接']) {
      expect(bridgeText).not.toContain(forbidden)
    }
    wrapper.unmount()
  })
})
