import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import { ApiError } from '@/api/errors'
import * as membersApi from '@/api/members'
import * as householdApi from '@/api/household'
import HouseholdCardView from '@/views/HouseholdCardView.vue'
import householdCardSource from '@/views/HouseholdCardView.vue?raw'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import type { FamilySpace, HouseholdCardSnapshot, SpaceMemberInfo } from '@/types/api'

/**
 * HouseholdCardView（design.md §5.1 / 任务 Phase 3-A）：
 * - 右栏成员只消费 household store 投影；加载失败/合同未就绪显示状态面板，
 *   绝不回退 members store、/users 或本地拼接；
 * - 成员点击 → /people/:userId（本人不跳转）；退出按钮 → lineage 空间回退；
 * - 空 household 显示服务端 allowed_actions 允许的创建/邀请入口。
 */

// 空间上下文协调器打桩：页面只负责触发切换，事务语义由 useSpaceContext 自身测试覆盖
const switchSpaceMock = vi.fn<(spaceId: number) => Promise<boolean>>()
vi.mock('@/composables/useSpaceContext', () => ({
  useSpaceContext: () => ({
    switchSpace: (spaceId: number) => switchSpaceMock(spaceId),
    ensureDefaultSpace: vi.fn().mockResolvedValue('household'),
    defaultTarget: () => ({ name: 'home' }),
  }),
}))

vi.mock('@/api/household', () => ({
  fetchHouseholdCard: vi.fn(),
}))

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

const mockedFetchHouseholdCard = vi.mocked(householdApi.fetchHouseholdCard)

function makeDisplay(id: number, name: string) {
  return {
    id,
    name,
    gender: 'f' as const,
    birth: { cal_type: 'solar' as const, date: '1950-03-12' },
    death: null,
    bio: null,
    avatar_path: null,
    privacy_mode: 'perpetual' as const,
    claim_status: 'claimed' as const,
  }
}

function makeCardSnapshot(
  overrides: Partial<HouseholdCardSnapshot['data']> = {},
): HouseholdCardSnapshot {
  return {
    data: {
      space_id: 7,
      space_kind: 'household',
      space_name: '我的家庭',
      view_version: 3,
      computed_at: '2026-09-01T08:00:00',
      viewer: makeDisplay(1, '张三'),
      members: [],
      allowed_actions: { can_invite_members: false, can_create_household: false, empty_state_hint: null },
      ...overrides,
    },
    etag: 'W/"v3"',
  }
}

function makeSpace(overrides: Partial<FamilySpace> = {}): FamilySpace {
  return {
    id: 7,
    name: '我的家庭',
    owner_id: 1,
    kind: 'household',
    created_at: '2026-08-25T00:00:00',
    pending_count: 0,
    member_count: 2,
    ...overrides,
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

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/family-tree', name: 'family-space', component: { template: '<div />' } },
      { path: '/people/:userId', name: 'person-profile', component: { template: '<div />' } },
      { path: '/notifications', name: 'notifications', component: { template: '<div />' } },
      { path: '/settings', name: 'settings', component: { template: '<div />' } },
    ],
  })
}

const Host = defineComponent({
  render() {
    return h('div', [h(NMessageProvider, () => h(HouseholdCardView))])
  },
})

async function mountCard(seedSpaces: FamilySpace[] = [makeSpace()]) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = makeRouter()
  await router.push('/')
  await router.isReady()

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
  spaces.spaces = seedSpaces
  spaces.currentSpaceId = seedSpaces[0]?.id ?? null
  spaces.members = [makeMembership()]

  const wrapper = mount(Host, {
    global: { plugins: [pinia, router] },
    attachTo: document.body,
  })
  await flushPromises()
  return { wrapper, router, spaces, pinia }
}

describe('HouseholdCardView 成员投影', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('只渲染 household store 投影成员；不回退 members store 或 /users 列表', async () => {
    mockedFetchHouseholdCard.mockResolvedValue(
      makeCardSnapshot({
        members: [
          { user_id: 2, display: makeDisplay(2, '母亲'), household_label: '成员', visibility_level: 'household_detail' },
          { user_id: 3, display: makeDisplay(3, '父亲'), household_label: '管理员', visibility_level: 'household_detail' },
        ],
      }),
    )
    const { wrapper } = await mountCard()

    expect(mockedFetchHouseholdCard).toHaveBeenCalledWith(7, null)
    const cards = wrapper.findAll('[data-test^="member-card-"]')
    expect(cards).toHaveLength(2)
    expect(wrapper.text()).toContain('母亲')
    expect(wrapper.text()).toContain('管理员')
    // 红线断言：家庭卡绝不从旧 members API 拼装成员
    expect(membersApi.fetchMembers).not.toHaveBeenCalled()
    expect(membersApi.fetchMembersByPrefix).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('masked 字段走 MaskedField 锁形章表达', async () => {
    mockedFetchHouseholdCard.mockResolvedValue(
      makeCardSnapshot({
        members: [
          {
            user_id: 2,
            display: { ...makeDisplay(2, '母亲'), birth: { __masked__: true } },
            household_label: '成员',
            visibility_level: 'household_detail',
          },
        ],
      }),
    )
    const { wrapper } = await mountCard()
    expect(wrapper.find('[data-test="masked-field"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('加载失败（BLOCKER 404）显示「家庭卡服务合同未就绪」面板，无成员数组兜底', async () => {
    mockedFetchHouseholdCard.mockRejectedValue(new ApiError(404, 'HTTP_ERROR', '请求失败（404）'))
    const { wrapper } = await mountCard()

    expect(wrapper.find('[data-test="contract-not-ready"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="contract-not-ready"]').text()).toContain('家庭卡服务合同未就绪')
    expect(wrapper.findAll('[data-test^="member-card-"]')).toHaveLength(0)
    expect(wrapper.find('[data-test="family-status"]').exists()).toBe(false)

    // 重试：重新请求同一投影端点（不切换数据源）
    mockedFetchHouseholdCard.mockResolvedValue(makeCardSnapshot())
    await wrapper.find('[data-test="contract-retry"]').trigger('click')
    await flushPromises()
    expect(mockedFetchHouseholdCard).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('网格 ↔ 列表切换渲染同一投影', async () => {
    mockedFetchHouseholdCard.mockResolvedValue(
      makeCardSnapshot({
        members: [
          { user_id: 2, display: makeDisplay(2, '母亲'), household_label: '成员', visibility_level: 'household_detail' },
        ],
      }),
    )
    const { wrapper } = await mountCard()
    expect(wrapper.find('[data-test="member-grid"]').exists()).toBe(true)

    const radios = wrapper.findAll('[data-test="member-view-toggle"] input[type="radio"]')
    await radios[1]!.setValue()
    await flushPromises()
    expect(wrapper.find('[data-test="member-list"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="member-row-2"]').text()).toContain('母亲')
    wrapper.unmount()
  })

  it('成员点击 → /people/:userId；本人卡片点击不跳转', async () => {
    mockedFetchHouseholdCard.mockResolvedValue(
      makeCardSnapshot({
        members: [
          { user_id: 1, display: makeDisplay(1, '张三'), household_label: '成员', visibility_level: 'self_private' },
          { user_id: 2, display: makeDisplay(2, '母亲'), household_label: '成员', visibility_level: 'household_detail' },
        ],
      }),
    )
    const { wrapper, router } = await mountCard()

    await wrapper.find('[data-test="member-card-1"]').trigger('click')
    expect(router.currentRoute.value.name).toBe('home')

    await wrapper.find('[data-test="member-card-2"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('person-profile')
    expect(router.currentRoute.value.params.userId).toBe('2')
    wrapper.unmount()
  })
})

describe('HouseholdCardView 退出与空状态', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('退出按钮切换到第一个可用 lineage 空间（useSpaceContext 事务）', async () => {
    mockedFetchHouseholdCard.mockResolvedValue(makeCardSnapshot())
    const { wrapper } = await mountCard([
      makeSpace({ id: 7, kind: 'household' }),
      makeSpace({ id: 9, name: '李家族谱', kind: 'lineage' }),
    ])

    await wrapper.find('[data-test="exit-to-family-tree"]').trigger('click')
    await flushPromises()
    expect(switchSpaceMock).toHaveBeenCalledWith(9)
    wrapper.unmount()
  })

  it('没有可用 lineage 时：显示安全提示并保留在家庭卡', async () => {
    mockedFetchHouseholdCard.mockResolvedValue(makeCardSnapshot())
    const { wrapper } = await mountCard([makeSpace({ id: 7, kind: 'household' })])

    await wrapper.find('[data-test="exit-to-family-tree"]').trigger('click')
    await flushPromises()
    expect(switchSpaceMock).not.toHaveBeenCalled()
    expect(wrapper.find('[data-test="no-lineage-hint"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="household-name"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('空 household：显示服务端允许的邀请/创建入口，入口来自既有流程组件', async () => {
    mockedFetchHouseholdCard.mockResolvedValue(
      makeCardSnapshot({
        members: [],
        allowed_actions: {
          can_invite_members: true,
          can_create_household: true,
          empty_state_hint: '这个家庭还没有成员，先邀请家人吧。',
        },
      }),
    )
    const { wrapper } = await mountCard()
    expect(wrapper.find('[data-test="household-empty"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="empty-hint"]').text()).toContain('先邀请家人吧')
    expect(wrapper.find('[data-test="invite-entry"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="create-household-entry"]').exists()).toBe(true)

    // 打开邀请弹窗（n-modal teleport 到 body）
    await wrapper.find('[data-test="invite-entry"]').trigger('click')
    await flushPromises()
    expect(document.querySelector('[data-test="invite-dialog"]')).not.toBeNull()
    wrapper.unmount()
  })

  it('服务端未授权创建/邀请时，空状态不渲染对应入口', async () => {
    mockedFetchHouseholdCard.mockResolvedValue(makeCardSnapshot({ members: [] }))
    const { wrapper } = await mountCard()
    expect(wrapper.find('[data-test="invite-entry"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="create-household-entry"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('家庭状态区：成员数、待办入口与进入家族树入口', async () => {
    mockedFetchHouseholdCard.mockResolvedValue(
      makeCardSnapshot({
        members: [
          { user_id: 2, display: makeDisplay(2, '母亲'), household_label: '成员', visibility_level: 'household_detail' },
        ],
      }),
    )
    const { wrapper, router } = await mountCard()
    expect(wrapper.find('[data-test="member-count"]').text()).toBe('1')

    await wrapper.find('[data-test="go-notifications"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('notifications')
    wrapper.unmount()
  })
})

describe('HouseholdCardView 375px 响应式（Phase 7）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('单列堆叠：本人资料 DOM 序在成员区之前（≤768px 纵向堆叠时在上）；成员区纵向滚动', async () => {
    mockedFetchHouseholdCard.mockResolvedValue(
      makeCardSnapshot({
        members: [
          { user_id: 2, display: makeDisplay(2, '母亲'), household_label: '成员', visibility_level: 'household_detail' },
        ],
      }),
    )
    const { wrapper } = await mountCard()

    // jsdom 无布局引擎：以 DOM 顺序断言堆叠次序（单列时本人资料在上、成员区在下）
    const selfCard = wrapper.find('[data-test="self-profile"]')
    const membersCard = wrapper.find('[data-test="household-members"]')
    expect(selfCard.exists()).toBe(true)
    expect(membersCard.exists()).toBe(true)
    expect(
      selfCard.element.compareDocumentPosition(membersCard.element) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()

    // 源级契约：≤768px 双栏收单列；成员网格/列表纵向滚动且无横向滚动；
    // 主要动作按钮与成员视图切换补足 44px 点按目标
    expect(householdCardSource).toMatch(
      /@media \(max-width: 768px\)[\s\S]*\.card-columns\s*\{\s*grid-template-columns: 1fr;/,
    )
    expect(householdCardSource).toMatch(/\.member-grid\s*\{[^}]*overflow-y: auto/)
    expect(householdCardSource).toMatch(/\.member-list\s*\{[^}]*overflow-y: auto/)
    expect(householdCardSource).toMatch(/\.exit-button\s*\{[^}]*min-height: 44px;/)
    expect(householdCardSource).toMatch(
      /@media \(max-width: 768px\)[\s\S]*\.n-button--small-type[^}]*min-height: 44px;/,
    )
    wrapper.unmount()
  })
})
