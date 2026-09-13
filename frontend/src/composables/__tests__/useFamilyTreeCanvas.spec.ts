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
} from '@/types/api'

/**
 * 家族树画布 view-model（design.md §5.2）：
 * - nodes/edges 只从已解码 PersonalFamilyViewData 构造，无任何旧 graph 输入；
 * - 树状布局按服务端 path 方向做几何分带（长辈在上、晚辈在下、同辈同行），
 *   不产生关系语义结论；自由画布确定性环形摆位。
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

function makeEdge(
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

function makeData(overrides: Partial<PersonalFamilyViewData> = {}): PersonalFamilyViewData {
  return {
    space_id: 9,
    status: 'current',
    view_version: 2,
    computed_at: '2026-09-01T08:00:00',
    nodes: [],
    inferred_edges: [],
    edges: [],
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
      inferred_edges: [],
      edges: [],
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

  it('edges 从 PersonalFamilyViewEdge 构造：key 确定可复现，label 取服务端称谓', () => {
    const path = [makeStep(VIEWER_ID, 2, 'up')]
    const data = makeData({
      nodes: [makeNode(VIEWER_ID), makeNode(2)],
      inferred_edges: [],
      edges: [makeEdge(VIEWER_ID, 2, path, { term: '父亲' })],
    })

    const model = buildFamilyCanvas(data, VIEWER_ID)
    expect(model.edges).toHaveLength(1)
    expect(model.edges[0]?.key).toBe(`e-${VIEWER_ID}-2-0`)
    expect(model.edges[0]?.label).toBe('父亲')
    expect(model.edges[0]?.sourceUserId).toBe(VIEWER_ID)
    expect(model.edges[0]?.targetUserId).toBe(2)
    // viewer 视角称谓注入节点
    expect(model.nodes.find((node) => node.userId === 2)?.term).toBe('父亲')
    expect(model.nodes.find((node) => node.userId === VIEWER_ID)?.term).toBeNull()
  })

  it('同一输入两次构造结果逐字段一致（无隐藏随机源/旧位置数据）', () => {
    const data = makeData({
      nodes: [makeNode(VIEWER_ID), makeNode(2)],
      inferred_edges: [],
      edges: [makeEdge(VIEWER_ID, 2, [makeStep(VIEWER_ID, 2, 'up')])],
    })
    expect(buildFamilyCanvas(data, VIEWER_ID)).toEqual(buildFamilyCanvas(data, VIEWER_ID))
  })
})

describe('applyTreeViewLayout（默认树状）', () => {
  const father = makeNode(2)
  const child = makeNode(3)
  const spouse = makeNode(4)
  const data = makeData({
    nodes: [makeNode(VIEWER_ID), father, child, spouse],
    inferred_edges: [],
    edges: [
      // viewer 的父亲：up → 长辈带（y < 0）
      makeEdge(VIEWER_ID, 2, [makeStep(VIEWER_ID, 2, 'up')]),
      // viewer 的孩子：down → 晚辈带（y > 0）
      makeEdge(VIEWER_ID, 3, [makeStep(VIEWER_ID, 3, 'down')]),
      // viewer 的配偶：sym → 同带（y = 0）
      makeEdge(VIEWER_ID, 4, [makeStep(VIEWER_ID, 4, 'sym', 'spouse')], { path_class: 'affinal' }),
    ],
  })

  it('按服务端 path 方向分带：长辈在上、晚辈在下、同辈与 viewer 同行', () => {
    const nodes = applyTreeViewLayout(buildFamilyCanvas(data, VIEWER_ID), VIEWER_ID)
    const byUser = new Map(nodes.map((node) => [node.userId, node]))
    expect(byUser.get(2)?.y).toBe(-ROW_SPACING)
    expect(byUser.get(3)?.y).toBe(ROW_SPACING)
    expect(byUser.get(VIEWER_ID)?.y).toBe(0)
    expect(byUser.get(4)?.y).toBe(0)
  })

  it('同带成员围绕 x=0 居中排开（列距 COL_SPACING）', () => {
    const nodes = applyTreeViewLayout(buildFamilyCanvas(data, VIEWER_ID), VIEWER_ID)
    const rowZero = nodes.filter((node) => node.y === 0)
    expect(rowZero).toHaveLength(2) // viewer + 配偶
    const xs = rowZero.map((node) => node.x).sort((a, b) => a - b)
    expect(xs[0]).toBeCloseTo(-COL_SPACING / 2)
    expect(xs[1]).toBeCloseTo(COL_SPACING / 2)
  })

  it('多步路径累计世代差（祖父母 -2、孙辈 +2）', () => {
    const nested = makeData({
      nodes: [makeNode(VIEWER_ID), makeNode(5), makeNode(6)],
      inferred_edges: [],
      edges: [
        makeEdge(VIEWER_ID, 5, [
          makeStep(VIEWER_ID, 2, 'up'),
          makeStep(2, 5, 'up'),
        ]),
        makeEdge(VIEWER_ID, 6, [
          makeStep(VIEWER_ID, 3, 'down'),
          makeStep(3, 6, 'down'),
        ]),
      ],
    })
    const nodes = applyTreeViewLayout(buildFamilyCanvas(nested, VIEWER_ID), VIEWER_ID)
    const byUser = new Map(nodes.map((node) => [node.userId, node]))
    expect(byUser.get(5)?.y).toBe(-2 * ROW_SPACING)
    expect(byUser.get(6)?.y).toBe(2 * ROW_SPACING)
  })

  it('viewerId 为 null 时全部落在 0 代带（安全退化）', () => {
    const nodes = applyTreeViewLayout(buildFamilyCanvas(data, null), null)
    expect(nodes.every((node) => node.y === 0)).toBe(true)
  })
})

describe('applyFreeCanvasLayout（自由画布）', () => {
  it('viewer 固定在原点，其余成员获得确定的非重叠环形位置', () => {
    const data = makeData({
      nodes: [makeNode(VIEWER_ID), makeNode(2), makeNode(3), makeNode(4)],
      inferred_edges: [],
      edges: [],
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

describe('buildFamilyCanvas 推测层（09-13）', () => {
  it('推测边进入独立 inferredEdges 规格（虚线渲染依据），confirmed 边不受影响', () => {
    const data = makeData({
      nodes: [
        makeNode(VIEWER_ID, 'self_private'),
        makeNode(2),
        {
          user_id: 3,
          display: makeDisplay(3),
          visibility_level: 'household_detail',
          inclusion_reason_code: 'inferred_path',
        },
      ],
      edges: [makeEdge(VIEWER_ID, 2, [makeStep(VIEWER_ID, 2, 'up')])],
      inferred_edges: [
        {
          id: 7,
          subject_user_id: 2,
          object_user_id: 3,
          relation_kind: 'spouse',
          term: '妻子',
          path: [],
          viewer_term: null,
          viewer_path: [],
          new_user_id: 3,
          evidence_fact_ids: [11, 12],
          revision: 1,
          created_at: '2026-09-13T08:00:00',
        },
      ],
    })

    const model = buildFamilyCanvas(data, VIEWER_ID)
    expect(model.edges).toHaveLength(1)
    expect(model.inferredEdges).toHaveLength(1)
    expect(model.inferredEdges[0]).toMatchObject({
      key: 'i-7',
      label: '妻子',
      sourceUserId: 2,
      targetUserId: 3,
    })
    // 新上树成员：inferred 标记 + viewer 视角推测称谓
    const newNode = model.nodes.find((node) => node.userId === 3)
    expect(newNode?.inferred).toBe(true)
    expect(newNode?.inferredTerm).toBeNull() // viewer_term 为空 → 仅角标无称谓
  })

  it('端点不在节点集合的推测边被丢弃（防御：解码已保证，画布二次防御）', () => {
    const data = makeData({
      nodes: [makeNode(VIEWER_ID, 'self_private'), makeNode(2)],
      inferred_edges: [
        {
          id: 8,
          subject_user_id: 2,
          object_user_id: 99,
          relation_kind: 'spouse',
          term: null,
          path: [],
          viewer_term: null,
          viewer_path: [],
          new_user_id: null,
          evidence_fact_ids: [],
          revision: 1,
          created_at: '2026-09-13T08:00:00',
        },
      ],
    })

    const model = buildFamilyCanvas(data, VIEWER_ID)
    expect(model.inferredEdges).toHaveLength(0)
  })

  it('推测层节点摆位：parent 类推测单跳把新成员放到下一世代带（只做几何摆位）', () => {
    const data = makeData({
      nodes: [
        makeNode(VIEWER_ID, 'self_private'),
        makeNode(2),
        {
          user_id: 3,
          display: makeDisplay(3),
          visibility_level: 'household_detail',
          inclusion_reason_code: 'inferred_path',
        },
      ],
      inferred_edges: [
        {
          id: 9,
          subject_user_id: 2,
          object_user_id: 3,
          relation_kind: 'biological_parent',
          term: '儿子',
          path: [],
          viewer_term: '儿子的推测亲属',
          viewer_path: [],
          new_user_id: 3,
          evidence_fact_ids: [11],
          revision: 1,
          created_at: '2026-09-13T08:00:00',
        },
      ],
      edges: [makeEdge(VIEWER_ID, 2, [makeStep(VIEWER_ID, 2, 'up')])],
    })

    const model = buildFamilyCanvas(data, VIEWER_ID)
    const positioned = applyTreeViewLayout(model, VIEWER_ID)
    const subject = positioned.find((node) => node.userId === 2)
    const object = positioned.find((node) => node.userId === 3)
    expect(subject).toBeDefined()
    expect(object).toBeDefined()
    expect(object!.y).toBe((subject!.y ?? 0) + ROW_SPACING)
  })
})
