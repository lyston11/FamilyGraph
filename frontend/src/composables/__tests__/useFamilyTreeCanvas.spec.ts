import { describe, expect, it } from 'vitest'

import {
  applyFreeCanvasLayout,
  applyTreeViewLayout,
  buildFamilyCanvas,
  COL_SPACING,
  ROW_SPACING,
} from '@/composables/useFamilyTreeCanvas'
import type {
  PersonalFamilyViewData,
  PersonalFamilyViewDisplay,
  PersonalFamilyViewEdge,
  PersonalFamilyViewNode,
  PersonalFamilyViewPathStep,
  PersonalFamilyViewTopologyEdge,
} from '@/types/api'

/**
 * 家族树画布 view-model（09-01 §5.2 / 09-13 design.md §5.1）：
 * - nodes/edges 只从已解码 PersonalFamilyViewData 构造，无任何旧 graph 输入；
 * - **个人摘要与结构边严格分离**：摘要 edges 只注入节点称谓与几何估计，
 *   绝不成为画布连线；连线只来自 confirmed topology_edges；
 * - topology_edges === null（旧载荷）→ topologyAvailable=false，无结构连线；
 * - 结构布局走确定性世代布局，冲突回退自由画布摆位。
 */

const VIEWER_ID = 1

function makeDisplay(id: number, name?: string): PersonalFamilyViewDisplay {
  return {
    id,
    name: name ?? `成员${id}`,
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
    display: makeDisplay(id),
    visibility_level: visibilityLevel,
    inclusion_reason_code: id === VIEWER_ID ? 'root' : 'confirmed_path',
  }
}

function makeStep(
  from: number,
  to: number,
  direction: PersonalFamilyViewPathStep['direction'],
  edgeType = 'parent',
): PersonalFamilyViewPathStep {
  return { from, to, edge_type: edgeType, subtype: null, direction, fact_id: from * 100 + to }
}

function makeSummaryEdge(
  from: number,
  to: number,
  path: PersonalFamilyViewPathStep[],
  overrides: Partial<PersonalFamilyViewEdge> = {},
): PersonalFamilyViewEdge {
  return {
    from_user_id: from,
    to_user_id: to,
    edge_kind: path[path.length - 1]?.edge_type ?? 'self',
    path,
    alternative_paths: [],
    path_class: 'direct_line',
    concept_code: null,
    term: `称谓${from}-${to}`,
    inclusion_reason_code: 'confirmed_path',
    ...overrides,
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
    view_version: 2,
    computed_at: '2026-09-01T08:00:00',
    nodes: [],
    edges: [],
    topology_edges: [],
    truncated: false,
    next_cursor: null,
    stale_reason: null,
    ...overrides,
  }
}

describe('buildFamilyCanvas', () => {
  it('nodes 只来自已解码快照：viewer 标记 isSelf，其余成员携带可见性层级', () => {
    const data = makeData({
      nodes: [
        makeNode(VIEWER_ID, 'self_private'),
        makeNode(2),
        makeNode(3, 'lineage_summary'),
      ],
    })

    const model = buildFamilyCanvas(data, VIEWER_ID)
    expect(model.nodes).toHaveLength(3)
    const self = model.nodes.find((node) => node.userId === VIEWER_ID)
    expect(self?.isSelf).toBe(true)
    expect(self?.visibilityLevel).toBe('self_private')
    const summary = model.nodes.find((node) => node.userId === 3)
    expect(summary?.visibilityLevel).toBe('lineage_summary')
    expect(summary?.isSelf).toBe(false)
  })

  it('画布连线只来自 confirmed 结构边：个人摘要边不产生连线（09-13 R1）', () => {
    const data = makeData({
      nodes: [makeNode(VIEWER_ID), makeNode(2)],
      edges: [makeSummaryEdge(VIEWER_ID, 2, [makeStep(VIEWER_ID, 2, 'up')], { term: '父亲' })],
      topology_edges: [makeTopologyEdge('parent', 2, VIEWER_ID, 'biological')],
    })

    const model = buildFamilyCanvas(data, VIEWER_ID)
    expect(model.edges).toHaveLength(1)
    expect(model.edges[0]?.key).toBe(`parent:biological:2:${VIEWER_ID}`)
    expect(model.edges[0]?.label).toBe('亲子·亲生')
    expect(model.edges[0]?.sourceUserId).toBe(2)
    expect(model.edges[0]?.targetUserId).toBe(VIEWER_ID)
    expect(model.edges[0]?.orientation).toBe('vertical')
    // viewer 视角称谓仍注入节点（名牌用）
    expect(model.nodes.find((node) => node.userId === 2)?.term).toBe('父亲')
    expect(model.nodes.find((node) => node.userId === VIEWER_ID)?.term).toBeNull()
  })

  it('对称结构边 orientation=horizontal，标签不含 viewer 称谓', () => {
    const data = makeData({
      nodes: [makeNode(VIEWER_ID), makeNode(2)],
      edges: [makeSummaryEdge(VIEWER_ID, 2, [makeStep(VIEWER_ID, 2, 'sym', 'spouse')], { term: '妻子' })],
      topology_edges: [makeTopologyEdge('spouse', VIEWER_ID, 2)],
    })
    const model = buildFamilyCanvas(data, VIEWER_ID)
    expect(model.edges[0]?.orientation).toBe('horizontal')
    expect(model.edges[0]?.label).toBe('配偶')
  })

  it('topology_edges 缺失（旧载荷）→ topologyAvailable=false 且无结构连线；空数组 → true', () => {
    const base = {
      nodes: [makeNode(VIEWER_ID), makeNode(2)],
      edges: [makeSummaryEdge(VIEWER_ID, 2, [makeStep(VIEWER_ID, 2, 'up')])],
    }
    const legacy = buildFamilyCanvas(makeData({ ...base, topology_edges: null }), VIEWER_ID)
    expect(legacy.topologyAvailable).toBe(false)
    expect(legacy.edges).toHaveLength(0)

    const empty = buildFamilyCanvas(makeData({ ...base, topology_edges: [] }), VIEWER_ID)
    expect(empty.topologyAvailable).toBe(true)
    expect(empty.edges).toHaveLength(0)
  })

  it('同一输入两次构造结果逐字段一致（无隐藏随机源/旧位置数据）', () => {
    const data = makeData({
      nodes: [makeNode(VIEWER_ID), makeNode(2)],
      edges: [makeSummaryEdge(VIEWER_ID, 2, [makeStep(VIEWER_ID, 2, 'up')])],
      topology_edges: [makeTopologyEdge('parent', 2, VIEWER_ID, 'biological')],
    })
    expect(buildFamilyCanvas(data, VIEWER_ID)).toEqual(buildFamilyCanvas(data, VIEWER_ID))
  })
})

describe('applyTreeViewLayout（confirmed 结构边 → 确定性世代布局）', () => {
  it('共同父母下兄妹同代、分别连接父母；配偶同代', () => {
    // 2=父亲 3=母亲 4=viewer 5=妹妹 6=viewer 配偶
    const data = makeData({
      nodes: [makeNode(2), makeNode(3), makeNode(4), makeNode(5), makeNode(6)],
      topology_edges: [
        makeTopologyEdge('parent', 2, 4, 'biological'),
        makeTopologyEdge('parent', 2, 5, 'biological'),
        makeTopologyEdge('parent', 3, 4, 'biological'),
        makeTopologyEdge('parent', 3, 5, 'biological'),
        makeTopologyEdge('spouse', 4, 6),
      ],
    })
    const outcome = applyTreeViewLayout(buildFamilyCanvas(data, 4), 4)
    expect(outcome.failed).toBe(false)
    const byUser = new Map(outcome.nodes.map((node) => [node.userId, node]))
    // 父母同在上一代，兄妹与配偶都在本人行
    expect(byUser.get(2)?.y).toBe(-ROW_SPACING)
    expect(byUser.get(3)?.y).toBe(-ROW_SPACING)
    expect(byUser.get(4)?.y).toBe(0)
    expect(byUser.get(5)?.y).toBe(0)
    expect(byUser.get(6)?.y).toBe(0)
    // 不存在仅由摘要产生的本人→妹妹连线（model.edges 只含结构边）
    const model = buildFamilyCanvas(data, 4)
    expect(model.edges.every((edge) => edge.key !== 'sibling:-:4:5')).toBe(true)
  })

  it('孙辈只连实际父母：世代按结构边逐代推算', () => {
    // 1=viewer 2=viewer 配偶 3=子女 4=子女配偶 5=孙辈
    const data = makeData({
      nodes: [makeNode(1), makeNode(2), makeNode(3), makeNode(4), makeNode(5)],
      topology_edges: [
        makeTopologyEdge('spouse', 1, 2),
        makeTopologyEdge('parent', 1, 3, 'biological'),
        makeTopologyEdge('parent', 2, 3, 'biological'),
        makeTopologyEdge('spouse', 3, 4),
        makeTopologyEdge('parent', 3, 5, 'biological'),
        makeTopologyEdge('parent', 4, 5, 'biological'),
      ],
    })
    const outcome = applyTreeViewLayout(buildFamilyCanvas(data, 1), 1)
    expect(outcome.failed).toBe(false)
    const byUser = new Map(outcome.nodes.map((node) => [node.userId, node]))
    expect(byUser.get(1)?.y).toBe(0)
    expect(byUser.get(3)?.y).toBe(ROW_SPACING)
    expect(byUser.get(4)?.y).toBe(ROW_SPACING)
    expect(byUser.get(5)?.y).toBe(2 * ROW_SPACING)
  })

  it('世代约束矛盾 → failed=true 并回退自由画布摆位', () => {
    // 2—3 对称同代 与 2→3 parent 差一代 同时成立：矛盾
    const data = makeData({
      nodes: [makeNode(1), makeNode(2), makeNode(3)],
      topology_edges: [
        makeTopologyEdge('parent', 1, 2, 'biological'),
        makeTopologyEdge('spouse', 2, 3),
        makeTopologyEdge('parent', 2, 3, 'biological'),
      ],
    })
    const outcome = applyTreeViewLayout(buildFamilyCanvas(data, 1), 1)
    expect(outcome.failed).toBe(true)
    // 自由画布摆位仍然完整覆盖全部节点
    expect(outcome.nodes).toHaveLength(3)
  })

  it('输入顺序打乱不影响布局结果（确定性）', () => {
    const edges = [
      makeTopologyEdge('parent', 2, 1, 'biological'),
      makeTopologyEdge('parent', 2, 3, 'biological'),
      makeTopologyEdge('spouse', 1, 4),
    ]
    const nodes = [makeNode(1), makeNode(2), makeNode(3), makeNode(4)]
    const forward = applyTreeViewLayout(
      buildFamilyCanvas(makeData({ nodes, topology_edges: edges }), 1),
      1,
    )
    const shuffled = applyTreeViewLayout(
      buildFamilyCanvas(
        makeData({ nodes: [...nodes].reverse(), topology_edges: [...edges].reverse() }),
        1,
      ),
      1,
    )
    const byUserForward = new Map(forward.nodes.map((node) => [node.userId, node]))
    const byUserShuffled = new Map(shuffled.nodes.map((node) => [node.userId, node]))
    expect(byUserShuffled).toEqual(byUserForward)
    expect(forward.failed).toBe(false)
  })
})

describe('applyTreeViewLayout（topology_edges 缺失 → 摘要几何估计，不画线）', () => {
  const father = makeNode(2)
  const child = makeNode(3)
  const spouse = makeNode(4)
  const data = makeData({
    nodes: [makeNode(VIEWER_ID), father, child, spouse],
    edges: [
      makeSummaryEdge(VIEWER_ID, 2, [makeStep(VIEWER_ID, 2, 'up')]),
      makeSummaryEdge(VIEWER_ID, 3, [makeStep(VIEWER_ID, 3, 'down')]),
      makeSummaryEdge(VIEWER_ID, 4, [makeStep(VIEWER_ID, 4, 'sym', 'spouse')], {
        path_class: 'affinal',
      }),
    ],
    topology_edges: null,
  })

  it('按摘要 path 方向分带：长辈在上、晚辈在下、同辈与 viewer 同行', () => {
    const outcome = applyTreeViewLayout(buildFamilyCanvas(data, VIEWER_ID), VIEWER_ID)
    expect(outcome.failed).toBe(false)
    const byUser = new Map(outcome.nodes.map((node) => [node.userId, node]))
    expect(byUser.get(2)?.y).toBe(-ROW_SPACING)
    expect(byUser.get(3)?.y).toBe(ROW_SPACING)
    expect(byUser.get(VIEWER_ID)?.y).toBe(0)
    expect(byUser.get(4)?.y).toBe(0)
  })

  it('同带成员围绕 x=0 居中排开（列距 COL_SPACING）', () => {
    const outcome = applyTreeViewLayout(buildFamilyCanvas(data, VIEWER_ID), VIEWER_ID)
    const rowZero = outcome.nodes.filter((node) => node.y === 0)
    expect(rowZero).toHaveLength(2) // viewer + 配偶
    const xs = rowZero.map((node) => node.x).sort((a, b) => a - b)
    expect(xs[0]).toBeCloseTo(-COL_SPACING / 2)
    expect(xs[1]).toBeCloseTo(COL_SPACING / 2)
  })

  it('viewerId 为 null 时全部落在 0 代带（安全退化）', () => {
    const outcome = applyTreeViewLayout(buildFamilyCanvas(data, null), null)
    expect(outcome.nodes.every((node) => node.y === 0)).toBe(true)
  })
})

describe('applyFreeCanvasLayout（自由画布）', () => {
  it('viewer 固定在原点，其余成员获得确定的非重叠环形位置', () => {
    const data = makeData({
      nodes: [makeNode(VIEWER_ID), makeNode(2), makeNode(3), makeNode(4)],
    })
    const model = buildFamilyCanvas(data, VIEWER_ID)
    const nodes = applyFreeCanvasLayout(model)

    const self = nodes.find((node) => node.isSelf)
    expect(self?.x).toBe(0)
    expect(self?.y).toBe(0)

    const others = nodes.filter((node) => !node.isSelf)
    const keys = new Set(others.map((node) => `${node.x}:${node.y}`))
    expect(keys.size).toBe(others.length)
    expect(applyFreeCanvasLayout(model)).toEqual(nodes)
  })
})
