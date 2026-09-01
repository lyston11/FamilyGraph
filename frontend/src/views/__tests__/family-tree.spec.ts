import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as graphApi from '@/api/graph'
import * as personalFamilyViewApi from '@/api/personalFamilyView'
// 引用被 vi.mock 替换后的 VueFlow 定义：findComponent 以同一组件对象匹配
import { VueFlow } from '@vue-flow/core'
import FamilyTreeView from '@/views/FamilyTreeView.vue'
import familyTreeSource from '@/views/FamilyTreeView.vue?raw'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import type {
  FamilySpace,
  PersonalFamilyViewData,
  PersonalFamilyViewDisplay,
  PersonalFamilyViewEdge,
  PersonalFamilyViewNode,
  PersonalFamilyViewPathStep,
  PersonalFamilyViewSnapshot,
  SpaceMemberInfo,
} from '@/types/api'

/**
 * FamilyTreeView（design.md §5.2 / 任务 Phase 3-C）：
 * - 不发起 graph 请求（/api/graph/me 红线），数据只来自 PersonalFamilyView store；
 * - 树状布局默认 + 自由画布切换，无列表布局入口；
 * - 点击自己 → 家庭卡、他人 → 公示页、关系边 → 只读说明面板；
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
  useVueFlow: () => ({ fitView: vi.fn(), setCenter: vi.fn() }),
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

function makeData(overrides: Partial<PersonalFamilyViewData> = {}): PersonalFamilyViewData {
  return {
    space_id: 9,
    status: 'current',
    view_version: 3,
    computed_at: '2026-09-01T08:00:00',
    nodes: [],
    edges: [],
    truncated: false,
    next_cursor: null,
    stale_reason: null,
    ...overrides,
  }
}

function makeSnapshot(data: PersonalFamilyViewData): PersonalFamilyViewSnapshot {
  return { data, etag: 'W/"v3"' }
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
    is_admin: false,
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
        edges: [makeEdge(1, 2, [makeStep(1, 2, 'down')])],
      }),
    })

    expect(mockedFetchView).toHaveBeenCalledWith(9, null)
    // 红线：家族树页面禁止请求或读取旧 graph store 的 /api/graph/me
    expect(mockedFetchMyGraph).not.toHaveBeenCalled()
  })

  it('渲染已解码节点：自己强调、summary 节点不可展开', async () => {
    const { wrapper } = await mountTree({
      data: makeData({
        nodes: [makeNode(1, 'self_private'), makeNode(2), makeNode(3, 'lineage_summary')],
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

  it('关系边点击 → 只读关系说明面板（含称谓与版本时间）', async () => {
    const { wrapper } = await mountTree({
      data: makeData({
        nodes: [makeNode(1, 'self_private'), makeNode(2)],
        edges: [makeEdge(1, 2, [makeStep(1, 2, 'down')])],
      }),
    })

    const vm = wrapper.vm as unknown as { openRelationshipPanel: (key: string) => void }
    vm.openRelationshipPanel('e-1-2-0')
    await flushPromises()

    const panel = wrapper.find('[data-test="relation-panel"]')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('称谓1-2')
    expect(panel.text()).toContain('v3')
    await wrapper.find('[data-test="relation-panel-close"]').trigger('click')
    expect(wrapper.find('[data-test="relation-panel"]').exists()).toBe(false)
  })

  it('面板「查看待办」安全跳转 → /notifications（不产生任何写操作）', async () => {
    const { wrapper, router } = await mountTree({
      data: makeData({
        nodes: [makeNode(1, 'self_private'), makeNode(2)],
        edges: [makeEdge(1, 2, [makeStep(1, 2, 'down')])],
      }),
    })

    const vm = wrapper.vm as unknown as { openRelationshipPanel: (key: string) => void }
    vm.openRelationshipPanel('e-1-2-0')
    await flushPromises()

    // Bridge pending 只在通知/待办处理：面板/页面均无 Bridge 操作控件
    expect(wrapper.text()).not.toContain('同意')
    expect(wrapper.text()).not.toContain('拒绝')

    await wrapper.find('[data-test="relation-view-todos"]').trigger('click')
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
        edges: [],
      }),
    })
    const banner = wrapper.find('[data-test="truncated-banner"]')
    expect(banner.exists()).toBe(true)
    expect(banner.find('[data-test="truncated-count"]').text()).toBe('3')
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
