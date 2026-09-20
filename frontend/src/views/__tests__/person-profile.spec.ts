import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, disposePinia, getActivePinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { NMessageProvider } from 'naive-ui'

import { deferred, familyData, familyProgress, familySnapshot } from '@/__tests__/personalFamilyViewFixtures'
import RelationshipDetailPanel from '@/components/canvas/RelationshipDetailPanel.vue'

import * as actionCardsApi from '@/api/actionCards'
import * as kinshipApi from '@/api/kinship'
import * as suggestionsApi from '@/api/stewardSuggestions'
import * as personalFamilyViewApi from '@/api/personalFamilyView'
import * as spacesApi from '@/api/spaces'
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
  FamilySpaceOptions,
  PersonalFamilyViewData,
  PersonalFamilyViewDisplay,
  PersonalFamilyViewEdge,
  PersonalFamilyViewNode,
  PersonalFamilyViewPathStep,
  PersonalFamilyViewSnapshot,
  StructuredDate,
  SuggestionItem,
  SuggestionsPage,
} from '@/types/api'
import type { KinshipResolve } from '@/types/kinship'

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

vi.mock('@/api/kinship', () => ({
  KINSHIP_FLAG_DISABLED: 'KINSHIP_FLAG_DISABLED',
  resolveKinship: vi.fn().mockResolvedValue({ found: false }),
  fetchMyTerms: vi.fn(),
  updateMyTerm: vi.fn(),
  recordTermUsage: vi.fn(),
  parseRelationText: vi.fn(),
}))

vi.mock('@/api/stewardSuggestions', () => ({
  fetchSuggestions: vi.fn().mockResolvedValue({ space_id: 9, items: [], next_cursor: null }),
  fetchSuggestionDetail: vi.fn(),
  submitSuggestion: vi.fn(),
  dismissSuggestion: vi.fn(),
  restoreSuggestionTerm: vi.fn(),
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
  fetchFamilySpaceOptions: vi.fn(),
  inviteIntoFamilyHousehold: vi.fn(),
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
const mockedFamilyOptions = vi.mocked(spacesApi.fetchFamilySpaceOptions)
const mockedFamilyInvite = vi.mocked(spacesApi.inviteIntoFamilyHousehold)
const mockedJoinByUser = vi.mocked(spacesApi.joinByUser)

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

function makeKinship(targetId: number): KinshipResolve {
  return {
    found: true, space_id: 9, viewer_user_id: 1, target_user_id: targetId,
    term: '外婆', term_source_level: 'steward', term_entry_id: null, concept_code: 'M_MOTHER',
    path_class: 'direct_line', explanation_structural: null, main_path: [], alt_paths: [],
    fact_state: { confirmed: 2, proposed: 0, disputed: 0, revoked: 0, evidence_fact_ids: [] },
    cache_hit: false, algorithm_version: 'v1',
  }
}

function makePreference(targetId: number, term: string): SuggestionItem {
  return {
    id: targetId, space_id: 9, kind: 'term_preference', origin: 'model', state: 'proposed',
    revision: 1, evidence_hash: 'evidence', subject_user_id: 1, object_user_id: targetId,
    subject_name: '我', object_name: '家人', presentation: null,
    value: { term, can_restore: true, semantic_hash: 'hash', projection_revision: 1 },
    evidence_summary: { fact_count: 2, facts: [] }, allowed_actions: ['open_details', 'submit'],
    expires_at: null, created_at: '',
  }
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

function makeHouseholdSpace(overrides: Partial<FamilySpace> = {}): FamilySpace {
  return {
    id: 5,
    name: '我的家庭',
    owner_id: 1,
    kind: 'household',
    created_at: '2026-08-25T00:00:00',
    pending_count: 0,
    member_count: 2,
    ...overrides,
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

  const Harness = defineComponent({ render: () => h(NMessageProvider, () => h(PersonProfileView)) })
  const wrapper = mount(Harness, {
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
    expect(wrapper.find('[data-test="kinship-section"]').exists()).toBe(true)
    expect(kinshipApi.resolveKinship).toHaveBeenCalledWith(9, 1, 2)
    expect(suggestionsApi.fetchSuggestions).toHaveBeenCalledWith(9, null, 50, 'term_preference', 2)
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
    expect(wrapper.find('[data-test="kinship-section"]').exists()).toBe(false)
    expect(kinshipApi.resolveKinship).not.toHaveBeenCalled()
    expect(suggestionsApi.fetchSuggestions).not.toHaveBeenCalled()
  })

  it('实际个人档案页展示可选的保留与恢复称谓入口', async () => {
    vi.mocked(kinshipApi.resolveKinship).mockResolvedValueOnce(makeKinship(2))
    vi.mocked(suggestionsApi.fetchSuggestions).mockResolvedValueOnce({
      space_id: 9, items: [makePreference(2, '姥姥')], next_cursor: null,
    })
    const { wrapper } = await mountProfile({
      data: makeData({ nodes: [makeNode(1, 'self_private'), makeNode(2)] }),
    })
    expect(wrapper.find('[data-test="kinship-suggestion"]').text()).toContain('姥姥')
    expect(wrapper.find('[data-test="kinship-keep-btn"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="kinship-restore-btn"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('路由切换人物后旧档案的迟到建议不会出现在新档案', async () => {
    let resolveOld!: (page: SuggestionsPage) => void
    const oldPage = new Promise<SuggestionsPage>((resolve) => { resolveOld = resolve })
    vi.mocked(kinshipApi.resolveKinship).mockResolvedValueOnce(makeKinship(2)).mockResolvedValueOnce(makeKinship(3))
    vi.mocked(suggestionsApi.fetchSuggestions).mockReturnValueOnce(oldPage).mockResolvedValueOnce({
      space_id: 9, items: [makePreference(3, '新人物叫法')], next_cursor: null,
    })
    const { wrapper, router } = await mountProfile({
      data: makeData({ nodes: [makeNode(1, 'self_private'), makeNode(2), makeNode(3)] }),
    })
    await router.push('/people/3')
    await flushPromises()
    resolveOld({ space_id: 9, items: [makePreference(2, '旧人物叫法')], next_cursor: null })
    await flushPromises()
    expect(wrapper.find('[data-test="profile-name"]').text()).toBe('成员3')
    expect(wrapper.find('[data-test="kinship-suggestion"]').text()).toContain('新人物叫法')
    expect(wrapper.text()).not.toContain('旧人物叫法')
    expect(suggestionsApi.fetchSuggestions).toHaveBeenLastCalledWith(9, null, 50, 'term_preference', 3)
    wrapper.unmount()
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

  it('页面不提供修改对方资料/建立关系/查看对方家庭/扩大权限的按钮（09-20 仅新增邀请入口）', async () => {
    const { wrapper } = await mountProfile({
      data: makeData({
        nodes: [makeNode(1, 'self_private'), makeNode(2)],
        inferred_edges: [],
        edges: [makeEdge(1, 2, '母女')],
      }),
    })

    // 09-20 需求修订：允许「加入家庭空间」（本用例未配对家族空间，故入口不可见）；
    // 其余扩权入口仍不得出现。
    for (const phrase of ['建立关系', '加入空间', '修改资料', '扩大权限', '查看对方家庭']) {
      expect(wrapper.text()).not.toContain(phrase)
    }
    // 09-20 需求允许「加入家庭空间」入口（当前是家族空间上下文，故可见）
    expect(wrapper.find('[data-test="profile-family-space-join"]').exists()).toBe(true)
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
    vi.mocked(kinshipApi.resolveKinship).mockReset().mockResolvedValue({ ...makeKinship(2), found: false, term: null })
    vi.mocked(suggestionsApi.fetchSuggestions).mockReset().mockResolvedValue({ space_id: 9, items: [], next_cursor: null })
    document.body.innerHTML = ''
  })
  afterEach(() => { vi.useRealTimers() })

  it.each(['progressive', 'legacy'] as const)('%s 换代先隐藏旧称谓/证据/建议，普通进度更新不重复解析', async (protocol) => {
    vi.mocked(kinshipApi.resolveKinship).mockResolvedValueOnce({
      ...makeKinship(2), term: '旧称谓', explanation_structural: '旧关系证据',
    })
    vi.mocked(suggestionsApi.fetchSuggestions).mockResolvedValueOnce({
      space_id: 9, items: [makePreference(2, '旧建议')], next_cursor: null,
    })
    const data = protocol === 'progressive' ? familyData() : makeData({ nodes: [makeNode(1, 'self_private'), makeNode(2)] })
    const version = data.progress?.generation ?? data.view_version
    const { wrapper, pinia } = await mountProfile({ data })
    const pfv = usePersonalFamilyViewStore(pinia)
    expect(wrapper.find('[data-test="kinship-term"]').text()).toBe('旧称谓')
    expect(wrapper.find('[data-test="kinship-suggestion"]').text()).toContain('旧建议')

    async function updateView(nextVersion: number, revision: number): Promise<void> {
      const nextData = data.progress
        ? { ...data, progress: familyProgress({ ...data.progress, generation: nextVersion, revision }) }
        : { ...data, view_version: nextVersion }
      mockedFetchView.mockResolvedValue(makeSnapshot(nextData))
      await pfv.refresh(9)
      await flushPromises()
    }

    await updateView(version, 2)
    expect(kinshipApi.resolveKinship).toHaveBeenCalledTimes(1)
    expect(suggestionsApi.fetchSuggestions).toHaveBeenCalledTimes(1)
    const nextResolve = deferred<KinshipResolve>()
    const nextSuggestions = deferred<SuggestionsPage>()
    vi.mocked(kinshipApi.resolveKinship).mockReturnValueOnce(nextResolve.promise)
    vi.mocked(suggestionsApi.fetchSuggestions).mockReturnValueOnce(nextSuggestions.promise)
    await updateView(version + 1, 1)
    expect(wrapper.find('[data-test="profile-name"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="kinship-loading"]').exists()).toBe(true)
    for (const stale of ['旧称谓', '旧关系证据', '旧建议']) expect(wrapper.text()).not.toContain(stale)
    expect(kinshipApi.resolveKinship).toHaveBeenCalledTimes(2)
    expect(suggestionsApi.fetchSuggestions).toHaveBeenCalledTimes(2)

    nextResolve.resolve({ ...makeKinship(2), term: '新称谓', explanation_structural: '新关系证据' })
    nextSuggestions.resolve({ space_id: 9, items: [makePreference(2, '新建议')], next_cursor: null })
    await flushPromises()
    expect(wrapper.find('[data-test="kinship-term"]').text()).toBe('新称谓')
    expect(wrapper.find('[data-test="kinship-suggestion"]').text()).toContain('新建议')
    await updateView(version + 1, 2)
    expect(kinshipApi.resolveKinship).toHaveBeenCalledTimes(2)
    expect(suggestionsApi.fetchSuggestions).toHaveBeenCalledTimes(2)

    mockedFetchView.mockRejectedValue(new ApiError(403, 'FORBIDDEN', '无法读取'))
    await expect(pfv.refresh(9)).rejects.toMatchObject({ status: 403 })
    await flushPromises()
    expect(wrapper.find('[data-test="kinship-section"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('新称谓')
  })

  it('同目标的新代完成后，旧代在途称谓和建议均不能回写', async () => {
    const oldResolve = deferred<KinshipResolve>()
    const oldSuggestions = deferred<SuggestionsPage>()
    vi.mocked(kinshipApi.resolveKinship).mockReturnValueOnce(oldResolve.promise)
    vi.mocked(suggestionsApi.fetchSuggestions).mockReturnValueOnce(oldSuggestions.promise)
    const { wrapper, pinia } = await mountProfile({ data: familyData() })
    vi.mocked(kinshipApi.resolveKinship).mockResolvedValueOnce({ ...makeKinship(2), term: '新代称谓' })
    vi.mocked(suggestionsApi.fetchSuggestions).mockResolvedValueOnce({
      space_id: 9, items: [makePreference(2, '新代建议')], next_cursor: null,
    })
    mockedFetchView.mockResolvedValue(familySnapshot({ progress: familyProgress({ generation: 8 }) }))
    await usePersonalFamilyViewStore(pinia).refresh(9)
    await flushPromises()
    expect(wrapper.find('[data-test="kinship-term"]').text()).toBe('新代称谓')
    oldResolve.resolve({ ...makeKinship(2), term: '旧代称谓' })
    oldSuggestions.resolve({ space_id: 9, items: [makePreference(2, '旧代建议')], next_cursor: null })
    await flushPromises()
    expect(wrapper.find('[data-test="kinship-term"]').text()).toBe('新代称谓')
    expect(wrapper.find('[data-test="kinship-suggestion"]').text()).toContain('新代建议')
    expect(wrapper.text()).not.toContain('旧代')
  })

  it('断网至 PFV 授权到期后，独立称谓缓存也不能继续显示', async () => {
    vi.mocked(kinshipApi.resolveKinship).mockResolvedValueOnce({ ...makeKinship(2), term: '期限内的称谓' })
    const { wrapper } = await mountProfile({ data: familyData({ progress: familyProgress({ phase: 'ready', next_poll_ms: 0 }) }) })
    expect(wrapper.find('[data-test="kinship-term"]').text()).toBe('期限内的称谓')
    mockedFetchView.mockRejectedValue(new ApiError(0, 'NETWORK_ERROR', '连接中断'))
    await vi.advanceTimersByTimeAsync(60_001)
    await flushPromises()
    expect(wrapper.find('[data-test="profile-identity"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="kinship-section"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('期限内的称谓')
  })

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

describe('PersonProfileView 家族空间内双向加入（09-20）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('scrollTo', vi.fn())
    document.body.innerHTML = ''
  })

  /** NModal 内容 teleport 到 document.body：按既有约定用 document 查询。 */
  function dialog(selector: string): Element | null {
    return document.querySelector(`[data-test="family-space-join-dialog"] ${selector}`)
  }

  function options(overrides: Partial<FamilySpaceOptions> = {}): FamilySpaceOptions {
    return {
      lineage_space_id: 9,
      lineage_space_name: '李家族谱',
      shares_lineage: true,
      invite: [{ space_id: 5, space_name: '我的家庭', status: 'none' }],
      join: [{ space_id: 6, space_name: '对方家庭', status: 'none' }],
      ...overrides,
    }
  }

  /** 家族树上下文（currentSpaceId=9 即 lineage）。传 'reject' 时用拒绝态。 */
  function mountInLineage(seed: FamilySpaceOptions | 'reject' = options()) {
    if (seed === 'reject') {
      mockedFamilyOptions.mockRejectedValue(
        new ApiError(404, 'USER_NOT_FOUND', '对方不存在或不可见'),
      )
    } else {
      mockedFamilyOptions.mockResolvedValue(seed)
    }
    return mountProfile(
      {
        data: makeData({
          nodes: [makeNode(1, 'self_private'), makeNode(2)],
          inferred_edges: [],
          edges: [makeEdge(1, 2, '母女')],
        }),
      },
      { userId: '2', currentSpaceId: 9, spaces: [makeLineageSpace(), makeHouseholdSpace()] },
    )
  }

  async function openDialog(wrapper: Awaited<ReturnType<typeof mountProfile>>['wrapper']) {
    await wrapper.find('[data-test="profile-family-space-join"]').trigger('click')
    await flushPromises()
  }

  it('家族空间上下文且目标可见时显示入口，请求带当前家族空间 id', async () => {
    const { wrapper } = await mountInLineage()
    expect(wrapper.find('[data-test="profile-family-space-join"]').exists()).toBe(true)
    await openDialog(wrapper)
    expect(mockedFamilyOptions).toHaveBeenCalledWith(9, 2)
  })

  it('家庭卡上下文用配对家族空间解析（lineageForSpace），无配对则不显示入口', async () => {
    // household 5 配对到 lineage 9 → 入口可用
    mockedFamilyOptions.mockResolvedValue(options())
    const paired = await mountProfile(
      { data: makeData({ space_id: 5, nodes: [makeNode(1, 'self_private'), makeNode(2)], edges: [] }) },
      {
        userId: '2',
        currentSpaceId: 5,
        spaces: [
          makeLineageSpace(),
          makeHouseholdSpace({ lineage_space_id: 9 }),
        ],
      },
    )
    expect(paired.wrapper.find('[data-test="profile-family-space-join"]').exists()).toBe(true)
    paired.wrapper.unmount()

    // 未配对的 household（且 owner 无唯一 lineage）→ 解析不出家族空间，不显示入口
    const unpaired = await mountProfile(
      { data: makeData({ space_id: 5, nodes: [makeNode(1, 'self_private'), makeNode(2)], edges: [] }) },
      { userId: '2', currentSpaceId: 5, spaces: [makeHouseholdSpace()] },
    )
    expect(unpaired.wrapper.find('[data-test="profile-family-space-join"]').exists()).toBe(false)
    unpaired.wrapper.unmount()
  })

  it('邀请方向：三态文案与禁用，确认后按所选空间发邀请', async () => {
    const { wrapper } = await mountInLineage(
      options({
        invite: [
          { space_id: 5, space_name: '我的家庭', status: 'active' },
          { space_id: 7, space_name: '第二个家庭', status: 'pending' },
          { space_id: 8, space_name: '可邀请家庭', status: 'none' },
        ],
      }),
    )
    await openDialog(wrapper)

    expect(dialog('[data-test="join-space-status-5"]')?.textContent).toContain('已在该家庭空间中')
    expect(dialog('[data-test="join-space-status-7"]')?.textContent).toContain('已有待处理')
    expect(dialog('[data-test="join-space-status-8"]')?.textContent).toContain('可以')
    expect(dialog('[data-test="join-space-5"]')?.className).toContain('disabled')
    expect(dialog('[data-test="join-space-7"]')?.className).toContain('disabled')
    expect(dialog('[data-test="join-space-8"]')?.className ?? '').not.toContain('disabled')

    ;(dialog('[data-test="join-dialog-submit"]') as HTMLButtonElement).click()
    await flushPromises()
    // 邀请走 family-invitations：家族空间 id + 所选空间 id + 目标用户
    expect(mockedFamilyInvite).toHaveBeenCalledWith(9, 8, 2)
  })

  it('申请方向：切到申请后按对方的家庭空间发加入申请', async () => {
    const { wrapper } = await mountInLineage(
      options({
        invite: [{ space_id: 5, space_name: '我的家庭', status: 'active' }],
        join: [
          { space_id: 6, space_name: '对方家庭', status: 'none' },
          { space_id: 10, space_name: '对方另一个家庭', status: 'pending' },
        ],
      }),
    )
    await openDialog(wrapper)

    // 邀请方向无可选项 → 默认落到申请方向
    ;(dialog('[data-test="join-direction-join"]') as HTMLElement).click()
    await flushPromises()
    ;(dialog('[data-test="join-dialog-submit"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(mockedJoinByUser).toHaveBeenCalledWith(9, 2, 6)
  })

  it('不同族：两个方向都不显示，只提示走邀请码途径', async () => {
    const { wrapper } = await mountInLineage(
      options({ shares_lineage: false, invite: [], join: [] }),
    )
    await openDialog(wrapper)

    expect(dialog('[data-test="join-dialog-not-same-lineage"]')?.textContent).toContain('邀请码')
    expect(dialog('[data-test="join-direction-group"]')).toBeNull()
    expect((dialog('[data-test="join-dialog-submit"]') as HTMLButtonElement).disabled).toBe(true)
    expect(mockedFamilyInvite).not.toHaveBeenCalled()
    expect(mockedJoinByUser).not.toHaveBeenCalled()
  })

  it('两个方向都没有可用空间：显示对应说明且不可提交', async () => {
    const { wrapper } = await mountInLineage(
      options({
        invite: [{ space_id: 5, space_name: '我的家庭', status: 'active' }],
        join: [{ space_id: 6, space_name: '对方家庭', status: 'active' }],
      }),
    )
    await openDialog(wrapper)

    expect(dialog('[data-test="join-dialog-all-taken"]')).not.toBeNull()
    expect(dialog('[data-test="join-space-group"]')).toBeNull()
    expect((dialog('[data-test="join-dialog-submit"]') as HTMLButtonElement).disabled).toBe(true)
  })

  it('选项加载失败：显示可读错误且不发送任何请求', async () => {
    const { wrapper } = await mountInLineage('reject')
    await openDialog(wrapper)

    expect(dialog('[data-test="join-dialog-error"]')?.textContent).toContain('对方不存在或不可见')
    expect(mockedFamilyInvite).not.toHaveBeenCalled()
    expect(mockedJoinByUser).not.toHaveBeenCalled()
  })

  it('操作失败：显示服务端文案且弹窗保持打开', async () => {
    mockedFamilyInvite.mockRejectedValue(
      new ApiError(403, 'SPACE_JOIN_NO_RELATION', '你与该账号不在同一个家族空间'),
    )
    const { wrapper } = await mountInLineage()
    await openDialog(wrapper)

    ;(dialog('[data-test="join-dialog-submit"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(document.querySelector('[data-test="family-space-join-dialog"]')).not.toBeNull()
  })
})
