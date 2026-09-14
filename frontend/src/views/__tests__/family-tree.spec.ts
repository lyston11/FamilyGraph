import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, disposePinia, getActivePinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { familyData, familyNode, familyProgress, familySnapshot } from '@/__tests__/personalFamilyViewFixtures'
import { ApiError } from '@/api/errors'
import InferredEdgePanel from '@/components/canvas/InferredEdgePanel.vue'

import * as graphApi from '@/api/graph'
import * as personalFamilyViewApi from '@/api/personalFamilyView'
// 引用被 vi.mock 替换后的 VueFlow 定义：findComponent 以同一组件对象匹配
import { VueFlow } from '@vue-flow/core'
import FamilyTreeView from '@/views/FamilyTreeView.vue'
import familyTreeSource from '@/views/FamilyTreeView.vue?raw'
import { useAuthStore } from '@/stores/auth'
import { usePersonalFamilyViewStore } from '@/stores/personalFamilyView'
import { useSpacesStore } from '@/stores/spaces'
import type {
  FamilySpace,
  PersonalFamilyViewData,
  PersonalFamilyViewDisplay,
  PersonalFamilyViewEdge,
  PersonalFamilyViewInferredEdge,
  PersonalFamilyViewNode,
  PersonalFamilyViewPathStep,
  PersonalFamilyViewSnapshot,
  PersonalFamilyViewTopologyEdge,
  SpaceMemberInfo,
} from '@/types/api'

enableAutoUnmount(afterEach)
afterEach(() => { const pinia = getActivePinia(); if (pinia) disposePinia(pinia) })
const { fitViewMock } = vi.hoisted(() => ({ fitViewMock: vi.fn() }))

/**
 * FamilyTreeView（09-01 design.md §5.2 / 09-13 design.md §5-6）：
 * - 不发起 graph 请求（/api/graph/me 红线），数据只来自 PersonalFamilyView store；
 * - 画布连线只来自 confirmed topology_edges：摘要边绝不画成星形连线；
 * - 树状布局默认 + 自由画布切换，无列表布局入口；
 * - 点击自己 → 家庭卡、他人 → 公示页、结构边 → 结构说明面板；
 * - topology 缺失 → 连线未就绪提示；世代冲突 → 回退自由画布并提示；
 * - lineage_summary 节点不可展开；stale/failed/never_computed 与 truncated 状态 UI。
 */

const switchSpaceMock = vi.fn<(spaceId: number) => Promise<boolean>>()
vi.mock('@/composables/useSpaceContext', () => ({
  useSpaceContext: () => ({
    switchSpace: (spaceId: number) => switchSpaceMock(spaceId),
    ensureDefaultSpace: vi.fn().mockResolvedValue('lineage'),
    defaultTarget: () => ({ name: 'family-space' }),
  }),
}))

vi.mock('@/api/personalFamilyView', () => ({
  fetchPersonalFamilyView: vi.fn(),
  demandPersonalFamilyView: vi.fn().mockResolvedValue({ status: 'queued', focus_user_id: null }),
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

// graph API 是旧流程合同：家族树页面必须零调用（红线断言）
vi.mock('@/api/graph', () => ({
  fetchMyGraph: vi.fn(),
  createConnectionRequest: vi.fn(),
  fetchIncomingConnections: vi.fn(),
  resolveConnection: vi.fn(),
  revokeRelation: vi.fn(),
}))

// Vue Flow 打桩：把 member 节点经 slot 模板渲染出来，保证节点交互可测；
// Controls 等子组件与画布几何在组件测试中不承载断言。
vi.mock('@vue-flow/core', () => ({
  VueFlow: defineComponent({
    props: {
      nodes: { type: Array, default: () => [] },
      edges: { type: Array, default: () => [] },
      // Phase 7 移动端触控契约：画布组件声明同名 prop 以便断言
      zoomOnPinch: { type: Boolean, default: undefined },
      panOnDrag: { type: Boolean, default: undefined },
      zoomOnScroll: { type: Boolean, default: undefined },
      defaultViewport: { type: Object, default: undefined },
    },
    setup(props, { slots }) {
      return () =>
        h(
          'div',
          { 'data-test': 'mock-flow' },
          (props.nodes as Array<{ id: string; data: unknown }>).map((node) =>
            slots['node-member']?.({ id: node.id, data: node.data }),
          ),
        )
    },
  }),
  // MemberNode 依赖连接点组件与位置枚举：测试只关心名牌自身的渲染/交互合同
  Handle: defineComponent({ template: '<div class="mock-handle" />' }),
  Position: { Top: 'top', Bottom: 'bottom', Left: 'left', Right: 'right' },
  useVueFlow: () => ({ fitView: fitViewMock, setCenter: vi.fn() }),
}))

vi.mock('@vue-flow/controls', () => ({
  Controls: defineComponent({ template: '<div />' }),
}))

const mockedFetchView = vi.mocked(personalFamilyViewApi.fetchPersonalFamilyView)
const mockedFetchMyGraph = vi.mocked(graphApi.fetchMyGraph)

function makeDisplay(id: number, name: string): PersonalFamilyViewDisplay {
  return {
    id,
    name,
    gender: 'm',
    birth: null,
    death: null,
    bio: null,
    avatar_path: null,
    privacy_mode: 'perpetual',
    claim_status: 'claimed',
  }
}

function makeNode(
  id: number,
  visibilityLevel: PersonalFamilyViewNode['visibility_level'] = 'household_detail',
): PersonalFamilyViewNode {
  return {
    user_id: id,
    display: makeDisplay(id, `成员${id}`),
    visibility_level: visibilityLevel,
    inclusion_reason_code: id === 1 ? 'root' : 'confirmed_path',
  }
}

function makeStep(from: number, to: number, direction: PersonalFamilyViewPathStep['direction']): PersonalFamilyViewPathStep {
  return { from, to, edge_type: 'parent', subtype: null, direction, fact_id: from * 100 + to }
}

function makeEdge(from: number, to: number, path: PersonalFamilyViewPathStep[]): PersonalFamilyViewEdge {
  return {
    from_user_id: from,
    to_user_id: to,
    edge_kind: 'parent',
    path,
    alternative_paths: [],
    path_class: 'direct_line',
    concept_code: 'FATHER',
    term: `称谓${from}-${to}`,
    inclusion_reason_code: 'confirmed_path',
  }
}

function makeTopologyEdge(
  edgeKind: PersonalFamilyViewTopologyEdge['edge_kind'],
  from: number,
  to: number,
  subtype: PersonalFamilyViewTopologyEdge['subtype'] = null,
): PersonalFamilyViewTopologyEdge {
  return {
    id: `${edgeKind}:${subtype ?? '-'}:${from}:${to}`,
    from_user_id: from,
    to_user_id: to,
    edge_kind: edgeKind,
    subtype,
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

function makeMembership(): SpaceMemberInfo {
  return {
    id: 1,
    space_id: 9,
    user_id: 1,
    added_by: 1,
    role: 'member',
    status: 'active',
    updated_at: '2026-08-25T00:00:00',
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

async function mountTree(seed?: { data?: PersonalFamilyViewData; reject?: unknown }) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = makeRouter()
  await router.push('/family-tree')
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
  spaces.spaces = [makeLineageSpace()]
  spaces.currentSpaceId = 9
  spaces.members = [makeMembership()]

  if (seed?.reject) {
    mockedFetchView.mockRejectedValue(seed.reject)
  } else {
    mockedFetchView.mockResolvedValue(makeSnapshot(seed?.data ?? makeData()))
  }

  const wrapper = mount(FamilyTreeView, {
    global: { plugins: [pinia, router] },
    attachTo: document.body,
  })
  await flushPromises()
  return { wrapper, router, pinia }
}

describe('FamilyTreeView 数据边界', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('只按当前 lineage space_id 请求 PersonalFamilyView，绝不调用旧 graph API', async () => {
    await mountTree({
      data: makeData({
        nodes: [makeNode(1, 'self_private'), makeNode(2)],
        inferred_edges: [],
        edges: [makeEdge(1, 2, [makeStep(1, 2, 'down')])],
      }),
    })

    expect(mockedFetchView).toHaveBeenCalledWith(9, null, expect.objectContaining({ progressive: true }))
    // 红线：家族树页面禁止请求或读取旧 graph store 的 /api/graph/me
    expect(mockedFetchMyGraph).not.toHaveBeenCalled()
  })

  it('渲染已解码节点：自己强调、summary 节点不可展开', async () => {
    const { wrapper } = await mountTree({
      data: makeData({
        nodes: [makeNode(1, 'self_private'), makeNode(2), makeNode(3, 'lineage_summary')],
        inferred_edges: [],
        edges: [],
      }),
    })

    const cards = wrapper.findAll('[data-test="canvas-member-card"]')
    expect(cards).toHaveLength(3)
    expect(wrapper.find('[data-test="self-chip"]').exists()).toBe(true)
    // summary：只读标记，无展开按钮、无家庭卡入口
    expect(wrapper.text()).toContain('不可展开')
    expect(wrapper.find('[data-test="join-request-btn"]').exists()).toBe(false)
    expect(wrapper.find('button[data-test="canvas-member-card"]').exists()).toBe(false)
  })

  it('点击自己 → 家庭卡；点击他人 → /people/:userId', async () => {
    const { wrapper, router } = await mountTree({
      data: makeData({
        nodes: [makeNode(1, 'self_private'), makeNode(2)],
        inferred_edges: [],
        edges: [],
      }),
    })

    const selfCard = wrapper.findAll('[data-test="canvas-member-card"]')[0]!
    await selfCard.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('home')

    const otherCard = wrapper.findAll('[data-test="canvas-member-card"]')[1]!
    await otherCard.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('person-profile')
    expect(router.currentRoute.value.params.userId).toBe('2')
  })

  it('结构边点击 → 结构关系说明面板（两端、类型、已确认，无 viewer 称谓）', async () => {
    const { wrapper } = await mountTree({
      data: makeData({
        nodes: [makeNode(1, 'self_private'), makeNode(2)],
        inferred_edges: [],
        edges: [makeEdge(1, 2, [makeStep(1, 2, 'down')])],
        topology_edges: [makeTopologyEdge('spouse', 1, 2)],
      }),
    })

    const vm = wrapper.vm as unknown as { openRelationshipPanel: (key: string) => void }
    vm.openRelationshipPanel('spouse:-:1:2')
    await flushPromises()

    const panel = wrapper.find('[data-test="structural-panel"]')
    expect(panel.exists()).toBe(true)
    // 两个实际端点 + 直接事实类型；不使用「称谓1-2」等 viewer 视角称谓
    expect(panel.text()).toContain('成员1')
    expect(panel.text()).toContain('成员2')
    expect(panel.text()).toContain('配偶')
    expect(panel.text()).toContain('已确认')
    expect(panel.text()).not.toContain('称谓1-2')
    expect(panel.text()).toContain('v3')
    await wrapper.find('[data-test="structural-panel-close"]').trigger('click')
    expect(wrapper.find('[data-test="structural-panel"]').exists()).toBe(false)
  })

  it('个人摘要边绝不画成画布连线；confirmed 结构边正常传入画布', async () => {
    const { wrapper } = await mountTree({
      data: makeData({
        nodes: [makeNode(1, 'self_private'), makeNode(2), makeNode(3)],
        edges: [makeEdge(1, 2, [makeStep(1, 2, 'down')]), makeEdge(1, 3, [makeStep(1, 3, 'down')])],
        // 结构边只有 1—2 配偶：摘要边 1→2、1→3 都不产生星形连线
        topology_edges: [makeTopologyEdge('spouse', 1, 2)],
      }),
    })

    const flow = wrapper.findComponent(VueFlow)
    const edges = flow.props('edges') as Array<{ id: string }>
    expect(edges).toHaveLength(1)
    expect(edges[0]!.id).toBe('spouse:-:1:2')
  })

  it('结构边随刷新消失时清空面板，避免残留旧端点信息', async () => {
    const { wrapper } = await mountTree({
      data: makeData({
        nodes: [makeNode(1, 'self_private'), makeNode(2)],
        topology_edges: [makeTopologyEdge('spouse', 1, 2)],
      }),
    })
    const vm = wrapper.vm as unknown as { openRelationshipPanel: (key: string) => void }
    vm.openRelationshipPanel('spouse:-:1:2')
    await flushPromises()
    expect(wrapper.find('[data-test="structural-panel"]').exists()).toBe(true)

    // 刷新后该结构边不再存在 → 面板自动清空
    mockedFetchView.mockResolvedValue(
      makeSnapshot(makeData({ nodes: [makeNode(1, 'self_private'), makeNode(2)] })),
    )
    await wrapper.find('[data-test="reload-view"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-test="structural-panel"]').exists()).toBe(false)
  })

  it('面板「查看待办」安全跳转 → /notifications（不产生任何写操作）', async () => {
    const { wrapper, router } = await mountTree({
      data: makeData({
        nodes: [makeNode(1, 'self_private'), makeNode(2)],
        topology_edges: [makeTopologyEdge('spouse', 1, 2)],
      }),
    })

    const vm = wrapper.vm as unknown as { openRelationshipPanel: (key: string) => void }
    vm.openRelationshipPanel('spouse:-:1:2')
    await flushPromises()

    // Bridge pending 只在通知/待办处理：面板/页面均无 Bridge 操作控件
    expect(wrapper.text()).not.toContain('同意')
    expect(wrapper.text()).not.toContain('拒绝')

    await wrapper.find('[data-test="structural-view-todos"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('notifications')
  })
})

describe('FamilyTreeView 布局与状态机 UI', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('默认树状布局，可切换自由画布；无列表布局入口', async () => {
    const { wrapper } = await mountTree({
      data: makeData({ nodes: [makeNode(1, 'self_private'), makeNode(2)], edges: [] }),
    })

    const layoutSwitch = wrapper.find('[data-test="layout-switch"]')
    expect(layoutSwitch.text()).toContain('树状')
    expect(layoutSwitch.text()).toContain('自由画布')
    expect(layoutSwitch.text()).not.toContain('列表')

    const radios = layoutSwitch.findAll('input[type="radio"]')
    expect(radios).toHaveLength(2)
    await radios[1]!.setValue()
    await flushPromises()
    // 切换后画布仍在（自由模式不重挂页面）
    expect(wrapper.findAll('[data-test="canvas-member-card"]').length).toBe(2)
  })

  it('stale：用最近安全投影渲染画布并清晰标注版本/时间/原因', async () => {
    const { wrapper } = await mountTree({
      data: makeData({
        status: 'stale',
        stale_reason: '成员资格发生变化',
        nodes: [makeNode(1, 'self_private'), makeNode(2)],
        inferred_edges: [],
        edges: [],
      }),
    })

    const banner = wrapper.find('[data-test="status-banner"]')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('已过期')
    expect(banner.text()).toContain('v3')
    expect(banner.text()).toContain('2026-09-01T08:00:00')
    expect(banner.text()).toContain('成员资格发生变化')
    expect(wrapper.findAll('[data-test="canvas-member-card"]').length).toBe(2)
  })

  it('failed 有快照：画布 + 失败标注；failed 无快照：状态面板', async () => {
    const { wrapper } = await mountTree({
      data: makeData({
        status: 'failed',
        nodes: [makeNode(1, 'self_private'), makeNode(2)],
        inferred_edges: [],
        edges: [],
      }),
    })
    expect(wrapper.find('[data-test="status-banner"]').text()).toContain('计算失败')
    expect(wrapper.findAll('[data-test="canvas-member-card"]').length).toBe(2)
    wrapper.unmount()

    const failedEmpty = await mountTree({ data: makeData({ status: 'failed' }) })
    expect(failedEmpty.wrapper.find('[data-test="family-tree-failed"]').exists()).toBe(true)
    expect(failedEmpty.wrapper.find('[data-test="mock-flow"]').exists()).toBe(false)
    failedEmpty.wrapper.unmount()
  })

  it('never_computed/queued/running 且无数据：状态面板不画布', async () => {
    for (const status of ['never_computed', 'queued', 'running'] as const) {
      const { wrapper } = await mountTree({ data: makeData({ status }) })
      expect(wrapper.find('[data-test="family-tree-pending"]').exists()).toBe(true)
      expect(wrapper.find('[data-test="mock-flow"]').exists()).toBe(false)
      wrapper.unmount()
    }
  })

  it('truncated：显示截断提示与已加载数量', async () => {
    const { wrapper } = await mountTree({
      data: makeData({
        truncated: true,
        nodes: [makeNode(1, 'self_private'), makeNode(2), makeNode(3)],
        inferred_edges: [],
        edges: [],
      }),
    })
    const banner = wrapper.find('[data-test="truncated-banner"]')
    expect(banner.exists()).toBe(true)
    expect(banner.find('[data-test="truncated-count"]').text()).toBe('3')
  })

  it('旧载荷缺 topology_edges：显示连线未就绪提示，不绘制替代星形连线', async () => {
    const { wrapper } = await mountTree({
      data: makeData({
        nodes: [makeNode(1, 'self_private'), makeNode(2)],
        edges: [makeEdge(1, 2, [makeStep(1, 2, 'down')])],
        topology_edges: null,
      }),
    })

    expect(wrapper.find('[data-test="topology-missing-hint"]').exists()).toBe(true)
    const flow = wrapper.findComponent(VueFlow)
    expect((flow.props('edges') as unknown[]).length).toBe(0)
    // 节点卡片仍然按安全投影显示
    expect(wrapper.findAll('[data-test="canvas-member-card"]').length).toBe(2)
  })

  it('世代约束冲突：自动回退自由画布并提示，节点不丢失', async () => {
    const { wrapper } = await mountTree({
      data: makeData({
        nodes: [makeNode(1, 'self_private'), makeNode(2), makeNode(3)],
        topology_edges: [
          makeTopologyEdge('parent', 1, 2, 'biological'),
          makeTopologyEdge('spouse', 2, 3),
          makeTopologyEdge('parent', 2, 3, 'biological'),
        ],
      }),
    })

    expect(wrapper.find('[data-test="layout-fallback-alert"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="layout-fallback-alert"]').text()).toContain('自由画布')
    // 全部真实节点保留（不删除任何关系或人物）
    expect(wrapper.findAll('[data-test="canvas-member-card"]').length).toBe(3)
  })

  it('403/404/网络错误：安全失败状态，不显示空间名/ID 细节', async () => {
    const { wrapper } = await mountTree({ reject: new Error('network down') })
    const panel = wrapper.find('[data-test="family-tree-error"]')
    expect(panel.exists()).toBe(true)
    expect(wrapper.text()).not.toContain('李家族谱')
    expect(wrapper.find('[data-test="lineage-space-name"]').exists()).toBe(false)
    // 重试仍走 PFV 端点
    await wrapper.find('[data-test="family-tree-retry"]').trigger('click')
    await flushPromises()
    expect(mockedFetchView).toHaveBeenCalledTimes(2)
    expect(mockedFetchMyGraph).not.toHaveBeenCalled()
  })
})

describe('FamilyTreeView 移动端画布（Phase 7 375px 契约）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('触控契约：双指缩放/拖拽平移显式开启；定位自己入口保留；工具栏收敛紧凑单行', async () => {
    const { wrapper } = await mountTree({
      data: makeData({ nodes: [makeNode(1, 'self_private'), makeNode(2)], edges: [] }),
    })

    // 移动端画布缩放/平移经 VueFlow 触控（zoom-on-pinch / pan-on-drag）；
    // 「定位自己」由工具栏按钮提供，不依赖列表布局
    const flow = wrapper.findComponent(VueFlow)
    expect(flow.exists()).toBe(true)
    expect(flow.props('zoomOnPinch')).toBe(true)
    expect(flow.props('panOnDrag')).toBe(true)
    expect(flow.props('zoomOnScroll')).toBe(true)
    expect(wrapper.find('[data-test="focus-self"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="fit-canvas"]').exists()).toBe(true)

    // 模板中不出现列表布局入口（红线：移动端也不恢复列表；只切 <template> 段，
    // 排除 script/style 注释中的「列表布局已删除」记录文字）
    expect(familyTreeSource).toMatch(
      /@media \(max-width: 768px\)[\s\S]*\.toolbar\s*\{[\s\S]*?flex-wrap: nowrap;/,
    )
    expect(familyTreeSource).toMatch(
      /@media \(max-width: 768px\)[\s\S]*\.toolbar\s*\{[\s\S]*?overflow-x: auto;/,
    )
    const templateStart = familyTreeSource.indexOf('<template>')
    const templateSection = familyTreeSource.slice(templateStart, familyTreeSource.indexOf('<style'))
    expect(templateSection).not.toContain('列表')
    wrapper.unmount()
  })
})

describe('FamilyTreeView 渐进展示与稳定交互', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    vi.mocked(personalFamilyViewApi.demandPersonalFamilyView).mockResolvedValue({ status: 'already_active', focus_user_id: null })
    document.body.innerHTML = ''
  })
  afterEach(() => { vi.useRealTimers() })

  it.each(['ready', 'failed'] as const)('空的 %s 预览结束等待，不依赖整作业 current 状态', async (phase) => {
    const { wrapper } = await mountTree({ data: familyData({ nodes: [], topology_edges: [],
      progress: familyProgress({ phase, next_poll_ms: 0, targets: [] }) }) })
    expect(wrapper.find('[data-test="family-tree-pending"]').exists()).toBe(false)
    expect(wrapper.find(`[data-test="family-tree-${phase === 'ready' ? 'empty' : 'failed'}"]`).exists()).toBe(true)
  })

  it('先显示确认骨架，自动补齐称谓和真实完成计数，ready 预览停止高频加载', async () => {
    const skeleton = familyData({ nodes: [familyNode(1), familyNode(2), familyNode(3)],
      topology_edges: [makeTopologyEdge('parent', 2, 1), makeTopologyEdge('spouse', 2, 3)],
      progress: familyProgress({ targets: [{ user_id: 2, status: 'pending', reason_code: null }, { user_id: 3, status: 'pending', reason_code: null }] }) })
    const { wrapper } = await mountTree({ data: skeleton })
    expect(wrapper.findAll('[data-test="canvas-member-card"]')).toHaveLength(3)
    expect(wrapper.findAll('[data-test="term-pending-chip"]')).toHaveLength(2)
    expect(wrapper.findComponent(VueFlow).props('edges')).toHaveLength(2)
    expect(wrapper.find('[data-test="family-tree-progress"]').text()).toContain('0/2')
    await vi.advanceTimersByTimeAsync(50)
    expect(fitViewMock).toHaveBeenCalledTimes(1)

    mockedFetchView.mockResolvedValue(familySnapshot({ ...skeleton,
      edges: [{ ...makeEdge(1, 2, [makeStep(1, 2, 'up')]), term: '父亲' }],
      progress: familyProgress({ phase: 'ready', revision: 2, next_poll_ms: 0,
        targets: [{ user_id: 2, status: 'ready', reason_code: null }, { user_id: 3, status: 'unavailable', reason_code: null }] }) }))
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(wrapper.find('[data-test="view-label"]').text()).toBe('父亲')
    expect(wrapper.find('[data-test="term-unavailable-chip"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="term-pending-chip"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="family-tree-progress"]').text()).toContain('已整理完成（2/2）')
    expect(wrapper.find('[data-test="status-banner"]').exists()).toBe(false)
    expect(fitViewMock).toHaveBeenCalledTimes(1)
    const calls = mockedFetchView.mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)
    expect(mockedFetchView).toHaveBeenCalledTimes(calls)
  })

  it('称谓更新保留拖动位置和视口，离开后返回仍保留自由画布', async () => {
    const { wrapper, pinia, router } = await mountTree({ data: familyData() })
    await vi.advanceTimersByTimeAsync(50)
    const radio = wrapper.find('[data-test="layout-switch"]').findAll('input[type="radio"]')[1]!
    await radio.setValue()
    const flow = wrapper.findComponent(VueFlow)
    const position = { x: 721, y: 385 }
    const viewport = { x: -42, y: 72, zoom: 1.4 }
    flow.vm.$emit('nodeDragStop', { node: { id: 'n-2', position }, nodes: [{ id: 'n-2', position }] })
    flow.vm.$emit('viewportChangeEnd', viewport)
    await flushPromises()
    const instance = flow.vm
    const pfv = usePersonalFamilyViewStore(pinia)
    mockedFetchView.mockResolvedValue(familySnapshot({
      edges: [{ ...makeEdge(1, 2, [makeStep(1, 2, 'up')]), term: '父亲' }],
      progress: familyProgress({ revision: 2, phase: 'ready', next_poll_ms: 0, targets: [{ user_id: 2, status: 'ready', reason_code: null }] }),
    }))
    await pfv.refresh(9)
    await flushPromises()
    expect(wrapper.findComponent(VueFlow).vm).toBe(instance)
    expect(flow.props('nodes')).toContainEqual(expect.objectContaining({ id: 'n-2', position }))
    expect(pfv.viewports.get(9)).toEqual(viewport)
    expect(fitViewMock).toHaveBeenCalledTimes(1)
    wrapper.unmount()

    const returned = mount(FamilyTreeView, { global: { plugins: [pinia, router] }, attachTo: document.body })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(50)
    const restored = returned.findComponent(VueFlow)
    expect(restored.props('defaultViewport')).toEqual(viewport)
    expect(restored.props('nodes')).toContainEqual(expect.objectContaining({ id: 'n-2', position, draggable: true }))
    expect(fitViewMock).toHaveBeenCalledTimes(1)
  })

  it.each([
    { status: 'queued', phase: 'preparing', reason_code: null },
    { status: 'stale', phase: 'retrying', reason_code: 'input_changed' },
  ] as const)('$phase 空窗隐藏图内容，新授权骨架恢复拖动位置和视口', async ({ status, phase, reason_code }) => {
    const { wrapper, pinia } = await mountTree({ data: familyData() })
    await vi.advanceTimersByTimeAsync(50)
    await wrapper.find('[data-test="layout-switch"]').findAll('input[type="radio"]')[1]!.setValue()
    const flow = wrapper.findComponent(VueFlow)
    const position = { x: 721, y: 385 }
    const viewport = { x: -42, y: 72, zoom: 1.4 }
    flow.vm.$emit('nodeDragStop', { node: { id: 'n-2', position }, nodes: [{ id: 'n-2', position }] })
    flow.vm.$emit('viewportChangeEnd', viewport)
    await flushPromises()
    const pfv = usePersonalFamilyViewStore(pinia)
    mockedFetchView.mockResolvedValue(familySnapshot({ status, nodes: [], topology_edges: [], edges: [], inferred_edges: [],
      progress: familyProgress({ revision: 2, phase, reason_code, targets: [] }) }))
    await pfv.refresh(9)
    await flushPromises()
    expect(wrapper.findComponent(VueFlow).exists()).toBe(false)
    expect(wrapper.text()).not.toContain('家人2')
    mockedFetchView.mockResolvedValue(familySnapshot({ progress: familyProgress({ generation: 8 }) }))
    await pfv.refresh(9)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(50)
    const restored = wrapper.findComponent(VueFlow)
    expect(restored.props('nodes')).toContainEqual(expect.objectContaining({ id: 'n-2', position }))
    expect(restored.props('defaultViewport')).toEqual(viewport)
    expect(fitViewMock).toHaveBeenCalledTimes(1)
  })

  it.each(['recompute', 'denied', 'subject'] as const)('树布局跨空窗按授权上下文处理锚点：%s', async (transition) => {
    const { wrapper, pinia } = await mountTree({ data: familyData({ nodes: [familyNode(1), familyNode(3)],
      topology_edges: [makeTopologyEdge('parent', 3, 1)],
      progress: familyProgress({ topology_revision: 'one-parent', targets: [{ user_id: 3, status: 'pending', reason_code: null }] }) }) })
    const pfv = usePersonalFamilyViewStore(pinia)
    const initialNodes = wrapper.findComponent(VueFlow).props('nodes') as { id: string; position: { x: number; y: number } }[]
    const viewport = { x: -42, y: 72, zoom: 1.4 }
    wrapper.findComponent(VueFlow).vm.$emit('viewportChangeEnd', viewport)
    const skeleton = familyData({ nodes: [familyNode(1), familyNode(2), familyNode(3)],
      topology_edges: [makeTopologyEdge('parent', 3, 1), makeTopologyEdge('spouse', 2, 3)],
      progress: familyProgress({ generation: 8, topology_revision: 'parents-and-spouse',
        targets: [{ user_id: 2, status: 'pending', reason_code: null }, { user_id: 3, status: 'pending', reason_code: null }] }) })
    mockedFetchView.mockResolvedValue(familySnapshot(skeleton))
    await pfv.refresh(9)
    await flushPromises()
    const expandedNodes = wrapper.findComponent(VueFlow).props('nodes') as typeof initialNodes
    expect(expandedNodes.find((node) => node.id === 'n-3')?.position.x).toBe(initialNodes.find((node) => node.id === 'n-3')?.position.x)
    const positions = expandedNodes.map(({ id, position }) => ({ id, position }))
    mockedFetchView.mockResolvedValue(familySnapshot({ status: 'stale', nodes: [], topology_edges: [],
      progress: familyProgress({ generation: 8, revision: 2, phase: 'retrying', topology_revision: '', reason_code: 'input_changed', targets: [] }) }))
    await pfv.refresh(9)
    await flushPromises()
    expect(wrapper.findComponent(VueFlow).exists()).toBe(false)
    if (transition === 'denied') {
      mockedFetchView.mockRejectedValueOnce(new ApiError(403, 'FORBIDDEN', '无法读取'))
      await expect(pfv.refresh(9)).rejects.toMatchObject({ status: 403 })
      await flushPromises()
    } else if (transition === 'subject') {
      useAuthStore(pinia).user = { ...useAuthStore(pinia).user!, id: 2 }
      await flushPromises()
    }
    mockedFetchView.mockResolvedValue(familySnapshot({ ...skeleton, progress: { ...skeleton.progress!, generation: 9 } }))
    await pfv.refresh(9)
    await flushPromises()
    const flow = wrapper.findComponent(VueFlow)
    const restored = flow.props('nodes') as typeof initialNodes
    if (transition === 'recompute') {
      expect(restored.map(({ id, position }) => ({ id, position }))).toEqual(positions)
      expect(flow.props('defaultViewport')).toEqual(viewport)
    } else {
      expect(restored.find((node) => node.id === 'n-3')?.position.x).toBeGreaterThan(restored.find((node) => node.id === 'n-2')!.position.x)
      expect(flow.props('defaultViewport')).toBeUndefined()
    }
  })

  it('选中面板按稳定 ID 使用最新对象，撤销可见性时及时关闭', async () => {
    const inferred: PersonalFamilyViewInferredEdge = { id: 15, subject_user_id: 1, object_user_id: 2,
      relation_kind: 'sibling', term: '兄弟', path: [], viewer_term: null, viewer_path: [], new_user_id: null,
      evidence_fact_ids: [], revision: 1, created_at: '2026-09-14T00:00:00Z' }
    const { wrapper, pinia } = await mountTree({ data: familyData({ inferred_edges: [inferred] }) })
    const flow = wrapper.findComponent(VueFlow)
    const edge = (flow.props('edges') as { id: string }[]).find((row) => row.id.startsWith('i-'))!
    flow.vm.$emit('edgeClick', { edge })
    await flushPromises()
    expect(wrapper.findComponent(InferredEdgePanel).props('edge').revision).toBe(1)
    mockedFetchView.mockResolvedValue(familySnapshot({ inferred_edges: [{ ...inferred, revision: 2, term: '姐妹' }],
      progress: familyProgress({ revision: 2 }) }))
    const pfv = usePersonalFamilyViewStore(pinia)
    await pfv.refresh(9)
    await flushPromises()
    expect(wrapper.findComponent(InferredEdgePanel).props('edge')).toMatchObject({ revision: 2, term: '姐妹' })
    mockedFetchView.mockResolvedValue(familySnapshot({ nodes: [familyNode(1)], topology_edges: [], inferred_edges: [],
      progress: familyProgress({ generation: 8, revision: 0, topology_revision: 'self', targets: [] }) }))
    await pfv.refresh(9)
    await flushPromises()
    expect(wrapper.findComponent(InferredEdgePanel).exists()).toBe(false)
    expect(wrapper.findAll('[data-test="canvas-member-card"]')).toHaveLength(1)
  })

  it('点击待计算家人发送一次 focus，并照常打开授权资料', async () => {
    const { wrapper, router } = await mountTree({ data: familyData() })
    const target = wrapper.findAll('[data-test="canvas-member-card"]')[1]!
    await target.trigger('click')
    await flushPromises()
    expect(personalFamilyViewApi.demandPersonalFamilyView).toHaveBeenCalledWith(9, expect.objectContaining({ focusUserId: 2 }))
    expect(router.currentRoute.value.params.userId).toBe('2')
    await target.trigger('click')
    await flushPromises()
    expect(personalFamilyViewApi.demandPersonalFamilyView).toHaveBeenCalledTimes(1)
  })

  it('暂时断线保留同一画布，到展示期限隐藏内容，重新授权后恢复视口', async () => {
    const { wrapper, pinia } = await mountTree({ data: familyData() })
    const flow = wrapper.findComponent(VueFlow)
    const viewport = { x: -170, y: 88, zoom: 1.2 }
    flow.vm.$emit('viewportChangeEnd', viewport)
    flow.vm.$emit('edgeClick', { edge: (flow.props('edges') as { id: string }[])[0] })
    await flushPromises()
    const instance = flow.vm
    mockedFetchView.mockRejectedValue(new ApiError(0, 'NETWORK_ERROR', '连接中断'))
    await vi.advanceTimersByTimeAsync(1000)
    expect(wrapper.findComponent(VueFlow).vm).toBe(instance)
    expect(wrapper.find('[data-test="update-notice"]').text()).toContain('重试')
    await vi.advanceTimersByTimeAsync(60_000)
    await flushPromises()
    expect(usePersonalFamilyViewStore(pinia).forSpace(9)).toBeNull()
    expect(wrapper.findComponent(VueFlow).exists()).toBe(false)
    expect(wrapper.find('[data-test="structural-panel"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('家人2')
    expect(wrapper.find('[data-test="family-tree-error"]').exists()).toBe(true)
    mockedFetchView.mockResolvedValue(familySnapshot({ progress: familyProgress({ generation: 8 }) }))
    await wrapper.find('[data-test="family-tree-retry"]').trigger('click')
    await flushPromises()
    expect(wrapper.findComponent(VueFlow).props('defaultViewport')).toEqual(viewport)
    expect(wrapper.findAll('[data-test="canvas-member-card"]')).toHaveLength(2)
  })
})
