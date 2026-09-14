import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, disposePinia, getActivePinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { familyData, familyProgress, familySnapshot } from '@/__tests__/personalFamilyViewFixtures'
import RelationshipDetailPanel from '@/components/canvas/RelationshipDetailPanel.vue'

import * as actionCardsApi from '@/api/actionCards'
import * as personalFamilyViewApi from '@/api/personalFamilyView'
import { ApiError } from '@/api/errors'
import PersonProfileView from '@/views/PersonProfileView.vue'
import { useAuthStore } from '@/stores/auth'
import { usePersonalFamilyViewStore } from '@/stores/personalFamilyView'
import { useSpacesStore } from '@/stores/spaces'
import type {
  ActionCard,
} from '@/types/actionCard'
import type {
  FamilySpace,
  PersonalFamilyViewData,
  PersonalFamilyViewDisplay,
  PersonalFamilyViewEdge,
  PersonalFamilyViewNode,
  PersonalFamilyViewPathStep,
  PersonalFamilyViewSnapshot,
  StructuredDate,
} from '@/types/api'

enableAutoUnmount(afterEach)
afterEach(() => { const pinia = getActivePinia(); if (pinia) disposePinia(pinia) })

/**
 * PersonProfileView（design.md §5.3 / PRD §2.4，Phase 4）：
 * - 直达/刷新先建立 lineage 空间上下文并加载 PersonalFamilyView 快照，再在
 *   快照内验证目标；绝不调用 /users 或按 userId 的宽泛用户详情接口；
 * - 目标不在快照中与投影端点 403/404 → 统一「对方不可见或不存在」安全状态
 *   （不显示目标 ID/空间名/路径长度/数量）；网络错误 → 安全失败状态；
 * - 点击自己 → 重定向家庭卡；返回家族树保持同一 lineage 空间上下文；
 * - masked/可见性 icon+文字；页面只读；相关 ActionCard 只提供「查看待办」跳转；
 *   Bridge pending 不在本页渲染任何操作控件。
 */

vi.mock('@/api/personalFamilyView', () => ({
  fetchPersonalFamilyView: vi.fn(),
  demandPersonalFamilyView: vi.fn().mockResolvedValue({ status: 'queued', focus_user_id: null }),
}))

vi.mock('@/api/actionCards', () => ({
  fetchActionCards: vi.fn(),
}))

vi.mock('@/api/notifications', () => ({
  fetchNotifications: vi.fn().mockResolvedValue({
    data: { space_id: 9, items: [], unread_count: 0 },
    etag: null,
  }),
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
  getSpacePositions: vi.fn().mockResolvedValue([]),
  putSpacePositions: vi.fn(),
  createOwnershipTransfer: vi.fn(),
  respondOwnershipTransfer: vi.fn(),
  submitManagerApplication: vi.fn(),
  fetchMyManagerApplications: vi.fn().mockResolvedValue([]),
  fetchEligibleManagerTargets: vi.fn().mockResolvedValue([]),
  fetchMyTransferConsents: vi.fn().mockResolvedValue([]),
  respondTransferConsent: vi.fn(),
}))

const mockedFetchView = vi.mocked(personalFamilyViewApi.fetchPersonalFamilyView)
const mockedFetchCards = vi.mocked(actionCardsApi.fetchActionCards)

const SOLAR_DATE: StructuredDate = { cal_type: 'solar', date: '1948-03-12' }

function makeDisplay(id: number, name: string, overrides: Partial<PersonalFamilyViewDisplay> = {}): PersonalFamilyViewDisplay {
  return {
    id,
    name,
    gender: 'f',
    birth: SOLAR_DATE,
    death: null,
    bio: '家人简介',
    avatar_path: null,
    privacy_mode: 'perpetual',
    claim_status: 'claimed',
    ...overrides,
  }
}

function makeNode(
  id: number,
  visibilityLevel: PersonalFamilyViewNode['visibility_level'] = 'household_detail',
  displayOverrides: Partial<PersonalFamilyViewDisplay> = {},
): PersonalFamilyViewNode {
  return {
    user_id: id,
    display: makeDisplay(id, `成员${id}`, displayOverrides),
    visibility_level: visibilityLevel,
    inclusion_reason_code: id === 1 ? 'root' : 'confirmed_path',
  }
}

function makeStep(from: number, to: number): PersonalFamilyViewPathStep {
  return { from, to, edge_type: 'parent', subtype: null, direction: 'up', fact_id: from * 100 + to }
}

function makeEdge(from: number, to: number, term: string): PersonalFamilyViewEdge {
  return {
    from_user_id: from,
    to_user_id: to,
    edge_kind: 'parent',
    path: [makeStep(from, to)],
    alternative_paths: [],
    path_class: 'direct_line',
    concept_code: 'FATHER',
    term,
    inclusion_reason_code: 'confirmed_path',
  }
}

function makeData(overrides: Partial<PersonalFamilyViewData> = {}): PersonalFamilyViewData {
  return {
    space_id: 9,
    status: 'current',
    view_version: 3,
    computed_at: '2026-09-01T08:00:00',
    nodes: [],
    inferred_edges: [],
    edges: [],
    topology_edges: [],
    truncated: false,
    next_cursor: null,
    stale_reason: null,
    ...overrides,
  }
}

function makeSnapshot(data: PersonalFamilyViewData): PersonalFamilyViewSnapshot {
  return data.progress ? familySnapshot(data) : { data, etag: 'W/"v3"' }
}

function makeLineageSpace(): FamilySpace {
  return {
    id: 9,
    name: '李家族谱',
    owner_id: 1,
    kind: 'lineage',
    created_at: '2026-08-25T00:00:00',
    pending_count: 0,
    member_count: 3,
  }
}

function makeHouseholdSpace(): FamilySpace {
  return {
    id: 5,
    name: '我的家庭',
    owner_id: 1,
    kind: 'household',
    created_at: '2026-08-25T00:00:00',
    pending_count: 0,
    member_count: 2,
  }
}

function makeActionCard(overrides: Partial<ActionCard> = {}): ActionCard {
  return {
    id: 77,
    kind: 'household_link',
    space_id: 9,
    subject_user: { id: 2, name: '成员2' },
    object_user: { id: 1, name: '张三' },
    reason_text: '建议建立共同家庭',
    evidence: { fact_ids: [1], path_summary: null, evidence_version: 1 },
    proposed_action: { type: 'create_household', params: {} },
    privacy_effect: '将创建一个新的家庭空间',
    state: 'pending',
    expires_at: null,
    created_at: '2026-09-01T00:00:00',
    revision: 1,
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
    ],
  })
}

interface MountOptions {
  userId: string
  currentSpaceId: number | null
  spaces?: FamilySpace[]
}

async function mountProfile(
  seed?: { data?: PersonalFamilyViewData; reject?: unknown; cards?: ActionCard[] },
  options: MountOptions = { userId: '2', currentSpaceId: 9 },
) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = makeRouter()
  await router.push(`/people/${options.userId}`)
  await router.isReady()

  const auth = useAuthStore(pinia)
  auth.user = {
    id: 1,
    name: '张三',
    pin_must_change: false,
    claim_status: 'claimed',
    profile_status: 'identity_confirmed',
  }
  const spaces = useSpacesStore(pinia)
  spaces.spaces = options.spaces ?? [makeLineageSpace()]
  spaces.currentSpaceId = options.currentSpaceId

  if (seed?.reject) {
    mockedFetchView.mockRejectedValue(seed.reject)
  } else {
    mockedFetchView.mockResolvedValue(makeSnapshot(seed?.data ?? makeData()))
  }
  mockedFetchCards.mockResolvedValue(seed?.cards ?? [])

  const wrapper = mount(PersonProfileView, {
    global: { plugins: [pinia, router] },
    attachTo: document.body,
  })
  await flushPromises()
  return { wrapper, router, pinia }
}

describe('PersonProfileView 直达/刷新与安全状态', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('scrollTo', vi.fn())
    document.body.innerHTML = ''
  })

  it('直达：先建立 lineage 上下文并加载当前快照，再在快照内验证目标', async () => {
    const { wrapper } = await mountProfile({
      data: makeData({
        nodes: [makeNode(1, 'self_private'), makeNode(2)],
        inferred_edges: [],
        edges: [makeEdge(1, 2, '母女')],
      }),
    })

    expect(mockedFetchView).toHaveBeenCalledWith(9, null, expect.objectContaining({ progressive: true }))
    expect(wrapper.find('[data-test="profile-unavailable"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="profile-name"]').text()).toBe('成员2')
    expect(wrapper.find('[data-test="profile-identity"]').text()).toContain('家庭详情可见')
  })

  it('目标不在快照中：统一「对方不可见或不存在」，不显示目标 ID/空间名/数量', async () => {
    const { wrapper } = await mountProfile(
      { data: makeData({ nodes: [makeNode(1, 'self_private')], edges: [] }) },
      { userId: '4242', currentSpaceId: 9 },
    )

    expect(wrapper.find('[data-test="profile-unavailable"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="profile-identity"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('4242')
    expect(wrapper.text()).not.toContain('李家族谱')
  })

  it('会话内到达（当前 lineage）：不重跑默认空间选择——列表中存在 own household 也不改写上下文', async () => {
    // 回归：ensureProfile 曾无条件调用 ensureDefaultSpace，把上下文改回
    // 「最近 household」，导致从家族树进入公示页时目标被误判为不可见
    // （09-01 走查实测）。当前空间已是 lineage 时必须直接沿用。
    const household = makeHouseholdSpace()
    const { wrapper } = await mountProfile(
      { data: makeData({ nodes: [makeNode(1, 'self_private'), makeNode(2)], edges: [] }) },
      { userId: '2', currentSpaceId: 9, spaces: [makeLineageSpace(), household] },
    )

    expect(mockedFetchView).toHaveBeenCalledWith(9, null, expect.objectContaining({ progressive: true }))
    expect(wrapper.find('[data-test="profile-name"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="profile-unavailable"]').exists()).toBe(false)
  })

  it('会话内当前空间是 household：走通用授权快照渲染资料（09-05 R1；家庭卡点成员卡可见）', async () => {
    const { wrapper } = await mountProfile(
      { data: makeData({ space_id: 5, nodes: [makeNode(1, 'self_private'), makeNode(2)], edges: [] }) },
      { userId: '2', currentSpaceId: 5, spaces: [makeLineageSpace(), makeHouseholdSpace()] },
    )

    expect(wrapper.find('[data-test="profile-unavailable"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="profile-name"]').exists()).toBe(true)
    // 返回按钮面向家庭卡（进入来源）
    expect(wrapper.text()).toContain('返回家庭卡')
  })

  it('快照加载失败（网络等）→ 安全失败状态：重试仍走 PFV 端点，不显示空间名', async () => {
    const { wrapper } = await mountProfile({ reject: new Error('network down') })

    expect(wrapper.find('[data-test="profile-load-error"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('李家族谱')

    mockedFetchView.mockResolvedValue(
      makeSnapshot(makeData({ nodes: [makeNode(1, 'self_private'), makeNode(2)] })),
    )
    await wrapper.find('[data-test="profile-retry"]').trigger('click')
    await flushPromises()
    expect(mockedFetchView).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-test="profile-name"]').exists()).toBe(true)
  })

  it('投影端点 403/404：与「不可见或不存在」同形状合并，不进入失败状态', async () => {
    const { wrapper } = await mountProfile({
      reject: new ApiError(404, 'VIEW_NOT_FOUND', '不存在'),
    })

    expect(wrapper.find('[data-test="profile-unavailable"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="profile-load-error"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('李家族谱')
  })

  it('当前空间是 household（无 lineage 空间）：同样经授权快照渲染（09-05 R1）', async () => {
    const { wrapper } = await mountProfile(
      { data: makeData({ space_id: 5, nodes: [makeNode(1, 'self_private'), makeNode(2)], edges: [] }) },
      { userId: '2', currentSpaceId: 5, spaces: [makeHouseholdSpace()] },
    )

    expect(wrapper.find('[data-test="profile-unavailable"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="profile-name"]').exists()).toBe(true)
  })

  it('硬刷新直达（无会话空间上下文）：经空间上下文协调建立 lineage 后正常渲染', async () => {
    const spacesApi = await import('@/api/spaces')
    vi.mocked(spacesApi.fetchSpaces).mockResolvedValue([makeLineageSpace()])

    const { wrapper, pinia } = await mountProfile(
      { data: makeData({ nodes: [makeNode(1, 'self_private'), makeNode(2)] }) },
      { userId: '2', currentSpaceId: null },
    )

    expect(mockedFetchView).toHaveBeenCalledWith(9, null, expect.objectContaining({ progressive: true }))
    expect(useSpacesStore(pinia).currentSpaceId).toBe(9)
    expect(wrapper.find('[data-test="profile-name"]').text()).toBe('成员2')
  })
})

describe('PersonProfileView 只读公示内容', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('scrollTo', vi.fn())
    document.body.innerHTML = ''
  })

  it('masked 字段用 MaskedField 锁形章，可见性层级用 icon+文字表达', async () => {
    const { wrapper } = await mountProfile({
      data: makeData({
        nodes: [
          makeNode(1, 'self_private'),
          makeNode(2, 'lineage_summary', {
            gender: { __masked__: true },
            birth: { __masked__: true },
            bio: { __masked__: true },
            privacy_mode: { __masked__: true },
            claim_status: { __masked__: true },
          }),
        ],
      }),
    })

    expect(wrapper.find('[data-test="profile-visibility"]').text()).toContain('族谱摘要 · 不可展开')
    for (const key of ['gender', 'birth', 'bio', 'privacy_mode', 'claim_status']) {
      expect(wrapper.find(`[data-test="profile-field-${key}"] [data-test="masked-field"]`).exists()).toBe(true)
    }
    // lineage_summary 不可展开：无任何展开控件
    expect(wrapper.find('[data-test="profile-expand"]').exists()).toBe(false)
  })

  it('明文字段按授权渲染：性别/出生/简介/档案状态', async () => {
    const { wrapper } = await mountProfile({
      data: makeData({ nodes: [makeNode(1, 'self_private'), makeNode(2)] }),
    })

    expect(wrapper.find('[data-test="profile-field-gender"]').text()).toContain('女')
    expect(wrapper.find('[data-test="profile-field-birth"]').text()).toContain('1948-03-12')
    expect(wrapper.find('[data-test="profile-field-bio"]').text()).toContain('家人简介')
    expect(wrapper.find('[data-test="profile-field-claim_status"]').text()).toContain('已确档')
    expect(wrapper.find('[data-test="profile-field-privacy_mode"]').text()).toContain('永久管理')
  })

  it('关系上下文：快照相邻边渲染称谓行，点击打开只读关系说明面板', async () => {
    const { wrapper } = await mountProfile({
      data: makeData({
        nodes: [makeNode(1, 'self_private'), makeNode(2), makeNode(3)],
        inferred_edges: [],
        edges: [makeEdge(1, 2, '母女'), makeEdge(2, 3, '母女')],
      }),
    })

    const rows = wrapper.findAll('[data-test^="profile-relation-"]')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('母女')
    expect(rows[0]!.text()).toContain('直系血亲')

    await rows[0]!.trigger('click')
    const panel = wrapper.find('[data-test="relation-panel"]')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('v3')
    expect(panel.text()).toContain('已确认的关系事实')

    await wrapper.find('[data-test="relation-panel-close"]').trigger('click')
    expect(wrapper.find('[data-test="relation-panel"]').exists()).toBe(false)
  })

  it('快照中无相邻边时显示安全空文案', async () => {
    const { wrapper } = await mountProfile({
      data: makeData({ nodes: [makeNode(1, 'self_private'), makeNode(2)], edges: [] }),
    })

    expect(wrapper.find('[data-test="profile-relations-empty"]').exists()).toBe(true)
  })
})

describe('PersonProfileView 跳转与重定向', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('scrollTo', vi.fn())
    document.body.innerHTML = ''
  })

  it('点击自己（userId === 当前用户）不进入本页：重定向 /（家庭卡）', async () => {
    const { router } = await mountProfile(undefined, { userId: '1', currentSpaceId: 9 })

    await flushPromises()
    expect(router.currentRoute.value.name).toBe('home')
    // 自己的资料不需要任何投影加载
    expect(mockedFetchView).not.toHaveBeenCalled()
  })

  it('返回按钮默认回家庭卡（无 state 兜底；PRD R1）', async () => {
    const { wrapper, router, pinia } = await mountProfile({
      data: makeData({ nodes: [makeNode(1, 'self_private'), makeNode(2)] }),
    })

    await wrapper.find('[data-test="back-to-family-tree"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('home')
    // 空间上下文由 spaces store 会话态决定：未发生空间切换
    expect(useSpacesStore(pinia).currentSpaceId).toBe(9)
  })

  it('返回按钮感知来源 state：家族树进入（fgBackTo）时回家族树', async () => {
    const { wrapper, router } = await mountProfile({
      data: makeData({ nodes: [makeNode(1, 'self_private'), makeNode(2)] }),
    })
    // 模拟从家族树进入：直接向当前 history 条目注入 fgBackTo
    // （对同路由重复 push 会被 vue-router 去重，state 不落盘）
    history.replaceState({ ...history.state, fgBackTo: 'family-space' }, '')

    await wrapper.find('[data-test="back-to-family-tree"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('family-space')
  })

  it('已有相关 ActionCard：只提供「查看待办」跳转 → /notifications', async () => {
    const { wrapper, router } = await mountProfile(
      {
        data: makeData({ nodes: [makeNode(1, 'self_private'), makeNode(2), makeNode(3)] }),
        cards: [
          makeActionCard({ subject_user: { id: 2, name: '成员2' }, state: 'pending' }),
          makeActionCard({
            id: 78,
            subject_user: { id: 3, name: '成员3' },
            object_user: { id: 2, name: '成员2' },
            state: 'viewed',
          }),
          // 终态卡不算待办
          makeActionCard({ id: 79, subject_user: { id: 2, name: '成员2' }, state: 'dismissed' }),
        ],
      },
    )

    const todos = wrapper.find('[data-test="profile-view-todos"]')
    expect(todos.exists()).toBe(true)
    await todos.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('notifications')
  })

  it('无相关 ActionCard：不渲染「查看待办」按钮', async () => {
    const { wrapper } = await mountProfile({
      data: makeData({ nodes: [makeNode(1, 'self_private'), makeNode(2), makeNode(3)] }),
      cards: [makeActionCard({ subject_user: { id: 3, name: '成员3' } })],
    })

    expect(wrapper.find('[data-test="profile-view-todos"]').exists()).toBe(false)
  })
})

describe('PersonProfileView 只读与 Bridge 边界', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('scrollTo', vi.fn())
    document.body.innerHTML = ''
  })

  it('页面只读：不提供修改对方资料/建立关系/加入空间/扩大权限的任何按钮', async () => {
    const { wrapper } = await mountProfile({
      data: makeData({
        nodes: [makeNode(1, 'self_private'), makeNode(2)],
        inferred_edges: [],
        edges: [makeEdge(1, 2, '母女')],
      }),
    })

    for (const phrase of ['建立关系', '邀请', '加入空间', '修改资料', '扩大权限', '查看对方家庭']) {
      expect(wrapper.text()).not.toContain(phrase)
    }
    // 无任何按 userId 的宽泛用户详情请求：只有 PFV 端点被调用
    expect(mockedFetchView).toHaveBeenCalledTimes(1)
  })

  it('Bridge pending 只在通知/待办处理：本页不渲染 approve/reject/consent/revoke 控件', async () => {
    const { wrapper } = await mountProfile({
      data: makeData({
        nodes: [makeNode(1, 'self_private'), makeNode(2)],
        inferred_edges: [],
        edges: [makeEdge(1, 2, '母女')],
      }),
    })

    // 空间管理员对跨 LineageSpace bridge 只有通知查看权——本页无任何 Bridge 操作按钮
    for (const phrase of ['同意桥接', '拒绝桥接', '撤销桥接', '批准']) {
      expect(wrapper.text()).not.toContain(phrase)
    }
    const buttonTests = wrapper.findAll('button').map((button) => button.attributes('data-test') ?? '')
    for (const test of buttonTests) {
      expect(test).not.toMatch(/approve|reject|consent|revoke/i)
    }
  })
})

describe('PersonProfileView 渐进关系说明', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    vi.stubGlobal('scrollTo', vi.fn())
    vi.mocked(personalFamilyViewApi.demandPersonalFamilyView).mockResolvedValue({ status: 'already_active', focus_user_id: null })
    document.body.innerHTML = ''
  })
  afterEach(() => { vi.useRealTimers() })

  it('先显示授权资料和单人待计算提示，轮询补齐完整称谓与说明', async () => {
    const { wrapper } = await mountProfile({ data: familyData() })
    expect(wrapper.find('[data-test="profile-name"]').text()).toBe('家人2')
    expect(wrapper.find('[data-test="profile-target-pending"]').text()).toContain('自动显示')
    expect(wrapper.find('[data-test="profile-relations-empty"]').exists()).toBe(false)
    expect(personalFamilyViewApi.demandPersonalFamilyView).toHaveBeenCalledWith(9, expect.objectContaining({ focusUserId: 2 }))
    mockedFetchView.mockResolvedValue(familySnapshot({ edges: [makeEdge(1, 2, '父亲')],
      progress: familyProgress({ revision: 2, phase: 'ready', next_poll_ms: 0, targets: [{ user_id: 2, status: 'ready', reason_code: null }] }) }))
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(wrapper.find('[data-test="profile-target-pending"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="profile-relation-0"]').text()).toContain('父亲')
    expect(wrapper.find('[data-test="profile-progress"]').text()).toContain('已整理完成（1/1）')
    expect(personalFamilyViewApi.demandPersonalFamilyView).toHaveBeenCalledTimes(1)
    const calls = mockedFetchView.mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)
    expect(mockedFetchView).toHaveBeenCalledTimes(calls)
  })

  it('无可显示称谓和计算失败分别提示，失败可明确重试', async () => {
    const { wrapper, pinia } = await mountProfile({ data: familyData({
      progress: familyProgress({ phase: 'ready', next_poll_ms: 0, targets: [{ user_id: 2, status: 'unavailable', reason_code: null }] }),
    }) })
    expect(wrapper.find('[data-test="profile-target-unavailable"]').text()).toContain('已完成整理')
    expect(wrapper.find('[data-test="profile-target-pending"]').exists()).toBe(false)
    mockedFetchView.mockResolvedValue(familySnapshot({ progress: familyProgress({ generation: 8, phase: 'failed', next_poll_ms: 0,
      targets: [{ user_id: 2, status: 'failed', reason_code: null }] }) }))
    await usePersonalFamilyViewStore(pinia).refresh(9)
    await flushPromises()
    expect(wrapper.find('[data-test="profile-target-failed"]').text()).toContain('暂未整理成功')
    expect(wrapper.find('[data-test="profile-progress"]').text()).toContain('0/1')
    expect(wrapper.find('[data-test="profile-identity"]').exists()).toBe(true)
    await wrapper.find('[data-test="profile-target-retry"]').trigger('click')
    await flushPromises()
    expect(personalFamilyViewApi.demandPersonalFamilyView).toHaveBeenCalledWith(9, expect.objectContaining({ retry: true }))
  })

  it('骨架尚未就绪时继续等待并暴露等待过长提示，避免误报成员不存在', async () => {
    const { wrapper } = await mountProfile({ data: familyData({ nodes: [], topology_edges: [],
      progress: familyProgress({ phase: 'preparing', targets: [] }) }) })
    expect(wrapper.find('[data-test="profile-preparing"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="profile-unavailable"]').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(31_000)
    await flushPromises()
    expect(wrapper.find('[data-test="profile-preparing-notice"]').text()).toContain('等待时间较长')
  })

  it('同一关系的说明随批次更新，成员失权后资料与面板同时回收', async () => {
    const { wrapper, pinia } = await mountProfile({ data: familyData({ edges: [makeEdge(1, 2, '父亲')],
      progress: familyProgress({ phase: 'ready', next_poll_ms: 0, targets: [{ user_id: 2, status: 'ready', reason_code: null }] }) }) })
    await wrapper.find('[data-test="profile-relation-0"]').trigger('click')
    expect(wrapper.findComponent(RelationshipDetailPanel).props('edge').term).toBe('父亲')
    const pfv = usePersonalFamilyViewStore(pinia)
    mockedFetchView.mockResolvedValue(familySnapshot({ edges: [makeEdge(1, 2, '爸爸')],
      progress: familyProgress({ generation: 8, phase: 'ready', next_poll_ms: 0, targets: [{ user_id: 2, status: 'ready', reason_code: null }] }) }))
    await pfv.refresh(9)
    await flushPromises()
    expect(wrapper.findComponent(RelationshipDetailPanel).props('edge').term).toBe('爸爸')
    mockedFetchView.mockRejectedValue(new ApiError(403, 'FORBIDDEN', '无法读取'))
    await expect(pfv.refresh(9)).rejects.toMatchObject({ status: 403 })
    await flushPromises()
    expect(wrapper.findComponent(RelationshipDetailPanel).exists()).toBe(false)
    expect(wrapper.find('[data-test="profile-identity"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="profile-unavailable"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('家人2')
  })
})
