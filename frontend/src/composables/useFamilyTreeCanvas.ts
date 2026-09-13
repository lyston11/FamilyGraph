import type {
  PersonalFamilyViewData,
  PersonalFamilyViewDisplay,
  PersonalFamilyViewEdge,
  PersonalFamilyViewInferredEdge,
} from '@/types/api'

/**
 * 家族树画布 view-model（09-01 design.md §5.2）：
 *
 * - 纯函数层：把已解码的 PersonalFamilyViewData 构造成 Vue Flow 可用的节点/边规格；
 *   旧 graph 的成员/边/位置数据一律不得混入（红线），布局只使用服务端返回的
 *   path 方向元数据做几何摆位，不推导任何授权/关系结论；
 * - 节点只来自 PFV nodes（`none` 已在 decoder 丢弃，不会出现）；
 * - 树状布局（默认）：以 viewer 为基准行，按 path 步骤方向（up/down/sym）
 *   估计世代带（长辈在上、晚辈在下、同辈同行），同带按 user_id 升序横向排开；
 * - 自由画布：viewer 固定原点，其余节点按确定性环形摆位（旧 node_positions
 *   API 属旧 graph 合同，不复用）。
 */

/** Vue Flow member 节点的注入数据（MemberNode 纯展示合同） */
export interface FamilyCanvasNodeData {
  display: PersonalFamilyViewDisplay
  visibilityLevel: 'self_private' | 'household_detail' | 'lineage_summary'
  isSelf: boolean
  /** 服务端解析的 viewer→该成员称谓；null = 暂无 */
  term: string | null
  /** 推测层：该成员仅经推测边上树（inclusion_reason_code=inferred_path） */
  inferred: boolean
  /** 推测成员的 viewer 视角称谓（backend viewer_term）；null = 暂无 */
  inferredTerm: string | null
}

export interface FamilyCanvasNode {
  userId: number
  display: PersonalFamilyViewDisplay
  visibilityLevel: 'self_private' | 'household_detail' | 'lineage_summary'
  isSelf: boolean
  /** 服务端解析的 viewer→该成员称谓；null = 暂无 */
  term: string | null
  /** 推测层成员：节点卡片渲染「推测」角标 */
  inferred: boolean
  /** 推测成员的 viewer 视角称谓（backend viewer_term）；null = 暂无 */
  inferredTerm: string | null
  x: number
  y: number
}

export interface FamilyCanvasEdge {
  key: string
  edge: PersonalFamilyViewEdge
  /** 边标签：称谓优先；null 时 Vue Flow 不渲染 label */
  label: string | null
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
  edges: FamilyCanvasEdge[]
  inferredEdges: FamilyCanvasInferredEdgeSpec[]
}

/** 世代带行距 / 同带宽列距（谱卷节奏，与旧画布一致量级） */
export const ROW_SPACING = 240
export const COL_SPACING = 280

function edgeKey(edge: PersonalFamilyViewEdge, index: number): string {
  return `e-${edge.from_user_id}-${edge.to_user_id}-${index}`
}

/**
 * 从 viewer 出发的世代差：path 步骤 direction up=-1（向长辈）/ down=+1（向晚辈）
 * / sym=0。只做几何摆位，不产生任何关系语义结论。
 */
function generationDelta(edge: PersonalFamilyViewEdge): number {
  let delta = 0
  for (const step of edge.path) {
    if (step.direction === 'up') delta -= 1
    else if (step.direction === 'down') delta += 1
  }
  return delta
}

/** 服务端合同：PFV 边恒为 viewer(actor)→target；此处防御两种端点朝向。 */
function deltaTowardViewer(edge: PersonalFamilyViewEdge, viewerId: number): number | null {
  if (edge.from_user_id === viewerId && edge.to_user_id !== viewerId) return generationDelta(edge)
  if (edge.to_user_id === viewerId && edge.from_user_id !== viewerId) return -generationDelta(edge)
  return null
}

/** viewer 视角称谓：取第一条连接 viewer 与该成员的边的 term */
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

/**
 * 构造画布模型（不含位置）：nodes/edges 均来自已解码快照。
 * key 稳定可复现，供画布事件与关系面板联动。
 * 推测边（09-13）：独立 inferredEdges 规格，虚线渲染；端点必须已在节点集合。
 */
export function buildFamilyCanvas(
  data: PersonalFamilyViewData,
  viewerId: number | null,
): FamilyCanvasModel {
  const edges: FamilyCanvasEdge[] = data.edges.map((edge, index) => ({
    key: edgeKey(edge, index),
    edge,
    label: edge.term,
    sourceUserId: edge.from_user_id,
    targetUserId: edge.to_user_id,
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
    inferred: node.inclusion_reason_code === 'inferred_path',
    inferredTerm: inferredTermByUser.get(node.user_id) ?? null,
    x: 0,
    y: 0,
  }))

  return { nodes, edges, inferredEdges }
}

/** 树状布局（默认）：viewer 所在行为 0 代带，按世代差纵向分带、同带横向排开。 */
export function applyTreeViewLayout(
  model: FamilyCanvasModel,
  viewerId: number | null,
): FamilyCanvasNode[] {
  const deltaByUser = new Map<number, number>()
  for (const node of model.nodes) deltaByUser.set(node.userId, 0)

  const touched = new Set<number>(viewerId !== null ? [viewerId] : [])
  if (viewerId !== null) {
    for (const edge of model.edges) {
      const delta = deltaTowardViewer(edge.edge, viewerId)
      if (delta === null) continue
      const other =
        edge.sourceUserId === viewerId ? edge.targetUserId : edge.sourceUserId
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
    // 同带按 user_id 升序（确定性），围绕 x=0 居中排开
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
