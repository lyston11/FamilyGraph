import type {
  MemberRelationLabelEdge,
  PersonalFamilyViewData,
  PersonalFamilyViewDisplay,
  PersonalFamilyViewEdge,
  PersonalFamilyViewInferredEdge,
  PersonalFamilyViewTopologyEdge,
  PersonalFamilyViewTargetStatus,
} from '@/types/api'

import { structuralEdgeLabel } from '@/components/canvas/relationshipDisplay'

import { computeFamilyTreeLayout } from './familyTreeLayout'

/**
 * 家族树画布 view-model（09-01 design.md §5.2 / 09-13 design.md §5.1）：
 *
 * - 纯函数层：把已解码的 PersonalFamilyViewData 构造成 Vue Flow 可用的节点/边规格；
 *   旧 graph 的成员/边/位置数据一律不得混入（红线）；
 * - **个人摘要与结构边严格分离**（09-13 R1）：个人摘要 edges 只提供节点称谓
 *   （node.term）与个人路径说明，绝不画成星形连线；画布连线只来自 confirmed
 *   topology_edges（server 端直接亲属事实）；
 * - topology_edges === null 表示旧后端载荷未提供结构数据：节点可按摘要估计
 *   几何摆位，但不绘制替代连线，由视图提示刷新重试；
 * - 自由画布：viewer 固定原点，其余节点按确定性环形摆位（会话内可拖动，
 *   不持久化；自由画布改变位置，不改变结构边身份和含义）。
 */

/** Vue Flow member 节点的注入数据（MemberNode 纯展示合同） */
export interface FamilyCanvasNodeData {
  display: PersonalFamilyViewDisplay
  visibilityLevel: 'self_private' | 'household_detail' | 'lineage_summary'
  isSelf: boolean
  /** 服务端解析的 viewer→该成员称谓；null = 暂无 */
  term: string | null
  termStatus?: PersonalFamilyViewTargetStatus | null
  /** 推测层：该成员仅经推测边上树（inclusion_reason_code=inferred_path） */
  inferred: boolean
  /** 推测成员的 viewer 视角称谓（backend viewer_term）；null = 暂无 */
  inferredTerm: string | null
  /**
   * 服务端 inclusion_reason_code：'space_member' = 授权空间成员但当前无 viewer
   * 可解析的 confirmed 亲属路径（09-18 R2/R5）。仅用于安全的「无路径」提示，
   * 不得据此伪造称谓或关系线。
   */
  inclusionReason: string
}

export interface FamilyCanvasNode {
  userId: number
  display: PersonalFamilyViewDisplay
  visibilityLevel: 'self_private' | 'household_detail' | 'lineage_summary'
  isSelf: boolean
  /** 服务端解析的 viewer→该成员称谓；null = 暂无 */
  term: string | null
  termStatus?: PersonalFamilyViewTargetStatus | null
  /** 推测层成员：节点卡片渲染「推测」角标 */
  inferred: boolean
  /** 推测成员的 viewer 视角称谓（backend viewer_term）；null = 暂无 */
  inferredTerm: string | null
  /** 服务端 inclusion_reason_code（'space_member' = 授权成员但暂无亲属路径） */
  inclusionReason: string
  x: number
  y: number
}

/** 画布结构边：来自 confirmed topology_edges，不代表 viewer 与目标的称谓路径 */
export interface FamilyStructuralEdge {
  key: string
  edge: PersonalFamilyViewTopologyEdge
  /** 结构标签（亲子/配偶/伴侣/兄弟姐妹 + 子类型），不用 viewer 视角称谓 */
  label: string
  /** parent 边：source=家长、target=子女；对称边：规范化端点（from≤to） */
  sourceUserId: number
  targetUserId: number
  /** parent=纵向（底部→顶部端口）；对称=横向（左右端口按实际 x 位置选择） */
  orientation: 'vertical' | 'horizontal'
}

/**
 * 关系词标注边画布规格（09-20）：第三种边样式，**不是亲属结构**。
 *
 * 只画在两端点都在节点集合内时；不参与布局与世代计算。
 */
export interface FamilyLabelEdgeSpec {
  key: string
  edge: MemberRelationLabelEdge
  /** 标注词原文（自由文本，如「朋友」「闺蜜」「族兄」） */
  label: string
  sourceUserId: number
  targetUserId: number
}

/** 推测边画布规格（09-13 推测层）：虚线渲染 + 一键确认/驳回 */
export interface FamilyCanvasInferredEdgeSpec {
  key: string
  edge: PersonalFamilyViewInferredEdge
  /** 虚线边标签：单跳确定性称谓（subject→object 方向） */
  label: string | null
  sourceUserId: number
  targetUserId: number
}

export interface FamilyCanvasModel {
  nodes: FamilyCanvasNode[]
  /** confirmed 结构边：画布连线的唯一来源 */
  edges: FamilyStructuralEdge[]
  /** 管家推测边：独立虚线叠层，不进入 confirmed 布局约束 */
  inferredEdges: FamilyCanvasInferredEdgeSpec[]
  /** 关系词标注边：第三种边样式，非亲属结构，不进入布局与世代计算 */
  labelEdges: FamilyLabelEdgeSpec[]
  /** 个人摘要边（viewer→成员称谓路径）：只用于节点名牌称谓与几何估计，绝不画线 */
  summaryEdges: PersonalFamilyViewEdge[]
  /** false = 旧载荷缺 topology_edges 字段：结构数据未提供（区别于合法空数组） */
  topologyAvailable: boolean
}

/** 世代带行距 / 同带宽列距（谱卷节奏，与旧画布一致量级） */
export const ROW_SPACING = 240
export const COL_SPACING = 280

/**
 * 结构边端口 id（MemberNode 与画布边共用，单一来源）。
 * 四个端口全部显式 id：Vue Flow 对未指定 handle 的边取「该类型第一个端口」
 * 而非无 id 端口（vue-flow-core handleBounds[0]），因此每条边必须显式绑定，
 * 否则亲子边会从左右同代端口出线（视觉上把同代成员连起来）。
 */
export const HANDLE_TARGET_TOP = 'fg-handle-tgt-top'
export const HANDLE_SOURCE_BOTTOM = 'fg-handle-src-bottom'
export const HANDLE_TARGET_LEFT = 'fg-handle-tgt-left'
export const HANDLE_SOURCE_RIGHT = 'fg-handle-src-right'

/** viewer 视角称谓：取第一条连接 viewer 与该成员的摘要边的 term（仅节点名牌用） */
function termTowardViewer(
  userId: number,
  viewerId: number,
  edges: readonly PersonalFamilyViewEdge[],
): string | null {
  for (const edge of edges) {
    const connected =
      (edge.from_user_id === viewerId && edge.to_user_id === userId) ||
      (edge.to_user_id === viewerId && edge.from_user_id === userId)
    if (connected) return edge.term
  }
  return null
}

export function buildFamilyCanvas(
  data: PersonalFamilyViewData,
  viewerId: number | null,
): FamilyCanvasModel {
  const topologyAvailable = data.topology_edges !== null
  const edges: FamilyStructuralEdge[] = (data.topology_edges ?? []).map((edge) => ({
    key: edge.id,
    edge,
    label: structuralEdgeLabel(edge),
    sourceUserId: edge.from_user_id,
    targetUserId: edge.to_user_id,
    orientation: edge.edge_kind === 'parent' ? 'vertical' : 'horizontal',
  }))

  const nodeUserIds = new Set(data.nodes.map((node) => node.user_id))
  const inferredEdges: FamilyCanvasInferredEdgeSpec[] = data.inferred_edges
    .filter(
      (edge) => nodeUserIds.has(edge.subject_user_id) && nodeUserIds.has(edge.object_user_id),
    )
    .map((edge) => ({
      key: `i-${edge.id}`,
      edge,
      label: edge.term,
      sourceUserId: edge.subject_user_id,
      targetUserId: edge.object_user_id,
    }))

  const labelEdges: FamilyLabelEdgeSpec[] = (data.label_edges ?? [])
    .filter(
      (edge) => nodeUserIds.has(edge.from_user_id) && nodeUserIds.has(edge.to_user_id),
    )
    .map((edge) => ({
      key: edge.id,
      edge,
      label: edge.label,
      sourceUserId: edge.from_user_id,
      targetUserId: edge.to_user_id,
    }))

  const inferredTermByUser = new Map<number, string | null>()
  for (const edge of data.inferred_edges) {
    if (edge.new_user_id !== null && !inferredTermByUser.has(edge.new_user_id)) {
      inferredTermByUser.set(edge.new_user_id, edge.viewer_term)
    }
  }

  const nodes: FamilyCanvasNode[] = data.nodes.map((node) => ({
    userId: node.user_id,
    display: node.display,
    visibilityLevel: node.visibility_level,
    isSelf: viewerId !== null && node.user_id === viewerId,
    term: viewerId === null ? null : termTowardViewer(node.user_id, viewerId, data.edges),
    termStatus: data.progress?.targets.find((target) => target.user_id === node.user_id)?.status ?? null,
    inferred: node.inclusion_reason_code === 'inferred_path',
    inferredTerm: inferredTermByUser.get(node.user_id) ?? null,
    inclusionReason: node.inclusion_reason_code,
    x: 0,
    y: 0,
  }))

  return {
    nodes,
    edges,
    inferredEdges,
    labelEdges,
    summaryEdges: data.edges,
    topologyAvailable,
  }
}

/**
 * 树状布局（默认）：confirmed 结构边存在时走确定性世代布局（design.md §6）；
 * 世代约束矛盾 → failed=true 并回退自由画布摆位。结构数据未提供（旧载荷）
 * 时仅按个人摘要估计几何位置——不绘制任何星形替代连线。
 */
export interface TreeLayoutOutcome {
  nodes: FamilyCanvasNode[]
  /**
   * true = confirmed 结构边存在世代约束矛盾，无法一致分代。
   * nodes 已按自由画布摆位，视图应切换自由画布并提示。
   */
  failed: boolean
}

export function applyTreeViewLayout(
  model: FamilyCanvasModel,
  viewerId: number | null,
): TreeLayoutOutcome {
  if (!model.topologyAvailable) {
    return { nodes: estimatePositionsFromSummary(model, viewerId), failed: false }
  }
  // confirmed 节点只由 confirmed 结构边约束（推测边不重排 confirmed 世代）
  const confirmedNodes = model.nodes.filter((node) => !node.inferred)
  const positions = computeFamilyTreeLayout({
    userIds: confirmedNodes.map((node) => node.userId),
    edges: model.edges.map((spec) => spec.edge),
    viewerId,
  })
  if (positions === null) {
    return { nodes: applyFreeCanvasLayout(model), failed: true }
  }
  const allPositions = placeInferredNodes(model, positions)
  return {
    nodes: model.nodes.map((node) => ({
      ...node,
      x: allPositions.get(node.userId)?.x ?? 0,
      y: allPositions.get(node.userId)?.y ?? 0,
    })),
    failed: false,
  }
}

/**
 * 推测节点摆位（09-13 推测层 × 结构布局并存）：沿推测单跳方向传播世代带
 * （parent 类低一带、对称类同带；逐轮传播直至稳定，上限 = 推测边数——有界、
 * 确定性），横向锚定首个已定位邻居并按占用扫描避让；未锚定时回退环形兜底位。
 * 只影响几何摆位：推测边由视图单独虚线渲染，不进入 confirmed 结构。
 */
function placeInferredNodes(
  model: FamilyCanvasModel,
  confirmedPositions: Map<number, { x: number; y: number }>,
): Map<number, { x: number; y: number }> {
  const result = new Map(confirmedPositions)
  const inferredNodes = model.nodes.filter(
    (node) => node.inferred && !result.has(node.userId),
  )
  if (inferredNodes.length === 0) return result

  const rankByUser = new Map<number, number>()
  for (const [userId, position] of confirmedPositions) {
    rankByUser.set(userId, Math.round(position.y / ROW_SPACING))
  }
  for (let round = 0; round <= model.inferredEdges.length; round += 1) {
    let changed = false
    for (const spec of model.inferredEdges) {
      const subjectId = spec.edge.subject_user_id
      const objectId = spec.edge.object_user_id
      const sym =
        spec.edge.relation_kind === 'spouse' ||
        spec.edge.relation_kind === 'partner' ||
        spec.edge.relation_kind === 'direct_sibling'
      const rankSubject = rankByUser.get(subjectId)
      const rankObject = rankByUser.get(objectId)
      if (rankSubject !== undefined && rankObject === undefined) {
        rankByUser.set(objectId, sym ? rankSubject : rankSubject + 1)
        changed = true
      } else if (rankObject !== undefined && rankSubject === undefined) {
        rankByUser.set(subjectId, sym ? rankObject : rankObject - 1)
        changed = true
      }
    }
    if (!changed) break
  }

  const fallbackRing = new Map(
    applyFreeCanvasLayout(model)
      .filter((node) => node.inferred)
      .map((node) => [node.userId, { x: node.x, y: node.y }]),
  )
  const occupied = new Set(
    [...result.values()].map((p) => `${Math.round(p.x)}:${Math.round(p.y)}`),
  )
  for (const node of inferredNodes) {
    const rank = rankByUser.get(node.userId)
    if (rank === undefined) {
      const fallback = fallbackRing.get(node.userId)
      if (fallback) {
        result.set(node.userId, fallback)
        occupied.add(`${Math.round(fallback.x)}:${Math.round(fallback.y)}`)
      }
      continue
    }
    const y = rank * ROW_SPACING
    let anchorX: number | null = null
    for (const spec of model.inferredEdges) {
      const subjectId = spec.edge.subject_user_id
      const objectId = spec.edge.object_user_id
      const other =
        subjectId === node.userId
          ? objectId
          : objectId === node.userId
            ? subjectId
            : null
      if (other === null) continue
      const anchor = result.get(other)
      if (anchor !== undefined) {
        anchorX = anchor.x
        break
      }
    }
    let x = anchorX === null ? 0 : anchorX + COL_SPACING
    while (occupied.has(`${Math.round(x)}:${Math.round(y)}`)) x += COL_SPACING
    result.set(node.userId, { x, y })
    occupied.add(`${Math.round(x)}:${Math.round(y)}`)
  }
  return result
}

/**
 * 结构数据未提供时的几何估计（旧 09-01 世代带逻辑）：按摘要路径的
 * up/down/sym 方向累计世代带，同带按 user_id 排开。只用于摆位，
 * 不产生任何连线或关系结论。
 */
function estimatePositionsFromSummary(
  model: FamilyCanvasModel,
  viewerId: number | null,
): FamilyCanvasNode[] {
  const deltaByUser = new Map<number, number>()
  for (const node of model.nodes) deltaByUser.set(node.userId, 0)

  const touched = new Set<number>(viewerId !== null ? [viewerId] : [])
  if (viewerId !== null) {
    const summaryEdges = model.summaryEdges ?? []
    for (const edge of summaryEdges) {
      const delta = summaryGenerationDelta(edge, viewerId)
      if (delta === null) continue
      const other = edge.from_user_id === viewerId ? edge.to_user_id : edge.from_user_id
      // 同一成员多条边时取首次结果，保证确定性
      if (!touched.has(other)) {
        touched.add(other)
        deltaByUser.set(other, delta)
      }
    }
  }

  // 推测层节点摆位（09-13）：沿推测单跳方向传播世代差（parent 类 subject 是
  // object 的家长 → object 低一带；对称类同带）。链式推测逐轮传播直至稳定，
  // 上限 = 推测边数（确定性；只影响几何摆位，不产生关系语义结论）。
  for (let round = 0; round <= model.inferredEdges.length; round += 1) {
    let changed = false
    for (const spec of model.inferredEdges) {
      const subjectId = spec.edge.subject_user_id
      const objectId = spec.edge.object_user_id
      const sym =
        spec.edge.relation_kind === 'spouse' ||
        spec.edge.relation_kind === 'partner' ||
        spec.edge.relation_kind === 'direct_sibling'
      const deltaSubject = deltaByUser.get(subjectId)
      const deltaObject = deltaByUser.get(objectId)
      if (deltaSubject !== undefined && deltaObject === undefined) {
        deltaByUser.set(objectId, sym ? deltaSubject : deltaSubject + 1)
        touched.add(objectId)
        changed = true
      } else if (deltaObject !== undefined && deltaSubject === undefined) {
        deltaByUser.set(subjectId, sym ? deltaObject : deltaObject - 1)
        touched.add(subjectId)
        changed = true
      }
    }
    if (!changed) break
  }

  const bands = new Map<number, number[]>()
  for (const node of model.nodes) {
    const delta = deltaByUser.get(node.userId) ?? 0
    const list = bands.get(delta) ?? []
    list.push(node.userId)
    bands.set(delta, list)
  }

  const positioned = new Map<number, { x: number; y: number }>()
  for (const [delta, userIds] of bands) {
    const sorted = [...userIds].sort((a, b) => a - b)
    sorted.forEach((userId, index) => {
      positioned.set(userId, {
        x: (index - (sorted.length - 1) / 2) * COL_SPACING,
        y: delta * ROW_SPACING,
      })
    })
  }

  return model.nodes.map((node) => ({
    ...node,
    x: positioned.get(node.userId)?.x ?? 0,
    y: positioned.get(node.userId)?.y ?? 0,
  }))
}

/** 摘要边世代差：path 步骤 direction up=-1 / down=+1 / sym=0（仅几何估计用） */
function summaryGenerationDelta(edge: PersonalFamilyViewEdge, viewerId: number): number | null {
  if (edge.from_user_id === viewerId && edge.to_user_id !== viewerId) {
    return accumulateDelta(edge.path, 1)
  }
  if (edge.to_user_id === viewerId && edge.from_user_id !== viewerId) {
    return accumulateDelta(edge.path, -1)
  }
  return null
}

function accumulateDelta(path: PersonalFamilyViewEdge['path'], sign: 1 | -1): number {
  let delta = 0
  for (const step of path) {
    if (step.direction === 'up') delta -= sign
    else if (step.direction === 'down') delta += sign
  }
  return delta
}

/** 自由画布：viewer 固定原点，其余成员按确定性环形摆位（会话内可拖动，不持久化）。 */
export function applyFreeCanvasLayout(model: FamilyCanvasModel): FamilyCanvasNode[] {
  let ring = 0
  let placed = 0
  return model.nodes.map((node) => {
    if (node.isSelf) return { ...node, x: 0, y: 0 }
    const angle = (placed % 8) * (Math.PI / 4) + ring * 0.4
    const radius = 240 + ring * 90
    const position = { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius }
    placed += 1
    if (placed % 8 === 0) ring += 1
    return { ...node, ...position }
  })
}
