import type {
  ClaimStatus,
  GenderType,
  Maskable,
  PersonalFamilyViewData,
  PersonalFamilyViewDisplay,
  PersonalFamilyViewEdge,
  PersonalFamilyViewInferredEdge,
  PersonalFamilyViewNode,
  PersonalFamilyViewPathStep,
  PersonalFamilyViewSnapshot,
  PersonalFamilyViewStatus,
  PersonalFamilyViewTopologyEdge,
  PrivacyMode,
  StructuredDate,
  TopologyEdgeKind,
  TopologyEdgeSubtype,
  VisibilityLevel,
} from '@/types/api'
import { isMasked } from '@/types/api'

import { apiClient } from './client'

/**
 * PersonalFamilyView 解码层（design.md §4.1）：`unknown` → 共享类型的唯一入口。
 *
 * 红线：
 * - 组件与 store 都不做局部 `as` 投影；不安全节点/边在此丢弃而不是留给组件隐藏；
 * - `none` 层级与缺 baseline 字段（id/name）的节点直接剔除，不生成占位；
 * - 端点只按明确 `space_id` 请求，不存在跨空间或全局形态。
 */

const VIEW_STATUSES: readonly PersonalFamilyViewStatus[] = [
  'never_computed',
  'queued',
  'running',
  'current',
  'stale',
  'failed',
]

/** 载荷中合法的可见性层级；`none` 由后端转 404，出现即视为脏数据 */
const VISIBILITY_LEVELS: readonly Exclude<VisibilityLevel, 'none'>[] = [
  'self_private',
  'household_detail',
  'lineage_summary',
]

const GENDERS: readonly GenderType[] = ['m', 'f', 'unknown']
const PRIVACY_MODES: readonly PrivacyMode[] = ['perpetual', 'handover']
const CLAIM_STATUSES: readonly ClaimStatus[] = ['managed', 'claimed']

const TOPOLOGY_EDGE_KINDS: readonly TopologyEdgeKind[] = ['parent', 'spouse', 'partner', 'sibling']
const TOPOLOGY_EDGE_SUBTYPES: readonly TopologyEdgeSubtype[] = [
  'biological',
  'adoptive',
  'step',
  'guardian',
]

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

/** 遮罩字段解码：命中哨兵即保留哨兵，否则按明文守卫校验 */
function decodeMaskable<T>(
  value: unknown,
  isClear: (candidate: unknown) => candidate is T,
): Maskable<T> | undefined {
  if (isMasked(value)) return { __masked__: true }
  return isClear(value) ? value : undefined
}

function isStructuredDateOrNull(value: unknown): value is StructuredDate | null {
  if (value === null) return true
  if (!isRecord(value)) return false
  return (
    (value.cal_type === 'solar' || value.cal_type === 'lunar' || value.cal_type === 'none') &&
    isNullableString(value.date)
  )
}

function isOneOf<T extends string>(options: readonly T[]) {
  return (value: unknown): value is T =>
    typeof value === 'string' && (options as readonly string[]).includes(value)
}

const isGender = isOneOf(GENDERS)
const isPrivacyMode = isOneOf(PRIVACY_MODES)
const isClaimStatus = isOneOf(CLAIM_STATUSES)
const isViewStatus = isOneOf(VIEW_STATUSES)
const isVisibilityLevel = isOneOf(VISIBILITY_LEVELS)

/**
 * baseline 字段（id/name）恒明文；被遮罩或缺失说明投影不可信，丢弃该节点。
 * 导出供 HouseholdCard 复用：其 viewer/member display 与本视图是同一后端投影口径
 * （visibility.payload_from_decision），不允许两套解码各自漂移。
 */
export function decodeDisplay(value: unknown): PersonalFamilyViewDisplay | null {
  if (!isRecord(value)) return null
  if (typeof value.id !== 'number' || typeof value.name !== 'string') return null

  const gender = decodeMaskable(value.gender, isGender)
  const birth = decodeMaskable(value.birth, isStructuredDateOrNull)
  const death = decodeMaskable(value.death, isStructuredDateOrNull)
  const bio = decodeMaskable(value.bio, isNullableString)
  const avatarPath = decodeMaskable(value.avatar_path, isNullableString)
  const privacyMode = decodeMaskable(value.privacy_mode, isPrivacyMode)
  const claimStatus = decodeMaskable(value.claim_status, isClaimStatus)
  if (
    gender === undefined ||
    birth === undefined ||
    death === undefined ||
    bio === undefined ||
    avatarPath === undefined ||
    privacyMode === undefined ||
    claimStatus === undefined
  ) {
    return null
  }

  return {
    id: value.id,
    name: value.name,
    gender,
    birth,
    death,
    bio,
    avatar_path: avatarPath,
    privacy_mode: privacyMode,
    claim_status: claimStatus,
  }
}

function decodeNode(value: unknown): PersonalFamilyViewNode | null {
  if (!isRecord(value)) return null
  if (typeof value.user_id !== 'number') return null
  if (!isVisibilityLevel(value.visibility_level)) return null
  if (typeof value.inclusion_reason_code !== 'string') return null
  const display = decodeDisplay(value.display)
  if (display === null) return null
  return {
    user_id: value.user_id,
    display,
    visibility_level: value.visibility_level,
    inclusion_reason_code: value.inclusion_reason_code,
  }
}

function decodePathStep(value: unknown): PersonalFamilyViewPathStep | null {
  if (!isRecord(value)) return null
  if (
    typeof value.from !== 'number' ||
    typeof value.to !== 'number' ||
    typeof value.edge_type !== 'string' ||
    typeof value.direction !== 'string' ||
    typeof value.fact_id !== 'number' ||
    !isNullableString(value.subtype)
  ) {
    return null
  }
  return {
    from: value.from,
    to: value.to,
    edge_type: value.edge_type,
    subtype: value.subtype,
    direction: value.direction,
    fact_id: value.fact_id,
  }
}

/** 路径整条校验：任一步坏掉即整条丢弃，不返回半条路径 */
function decodePath(value: unknown): PersonalFamilyViewPathStep[] | null {
  if (!Array.isArray(value)) return null
  const steps: PersonalFamilyViewPathStep[] = []
  for (const raw of value) {
    const step = decodePathStep(raw)
    if (step === null) return null
    steps.push(step)
  }
  return steps
}

function decodeEdge(value: unknown): PersonalFamilyViewEdge | null {
  if (!isRecord(value)) return null
  if (
    typeof value.from_user_id !== 'number' ||
    typeof value.to_user_id !== 'number' ||
    typeof value.edge_kind !== 'string' ||
    typeof value.path_class !== 'string' ||
    typeof value.inclusion_reason_code !== 'string' ||
    !isNullableString(value.concept_code) ||
    !isNullableString(value.term)
  ) {
    return null
  }
  const path = decodePath(value.path)
  if (path === null) return null
  if (!Array.isArray(value.alternative_paths)) return null
  const alternativePaths: PersonalFamilyViewPathStep[][] = []
  for (const raw of value.alternative_paths) {
    const alt = decodePath(raw)
    if (alt === null) return null
    alternativePaths.push(alt)
  }
  return {
    from_user_id: value.from_user_id,
    to_user_id: value.to_user_id,
    edge_kind: value.edge_kind,
    path,
    alternative_paths: alternativePaths,
    path_class: value.path_class,
    concept_code: value.concept_code,
    term: value.term,
    inclusion_reason_code: value.inclusion_reason_code,
  }
}

/**
 * confirmed 结构边单条解码：正整数端点、两端不同、kind/subtype 组合合法
 * （parent 必须携带子类型，对称关系必须无子类型）。坏条目丢弃。
 */
function decodeTopologyEdge(value: unknown): PersonalFamilyViewTopologyEdge | null {
  if (!isRecord(value)) return null
  if (
    typeof value.id !== 'string' ||
    value.id.length === 0 ||
    typeof value.from_user_id !== 'number' ||
    typeof value.to_user_id !== 'number' ||
    !Number.isInteger(value.from_user_id) ||
    !Number.isInteger(value.to_user_id) ||
    value.from_user_id <= 0 ||
    value.to_user_id <= 0 ||
    value.from_user_id === value.to_user_id ||
    !isOneOf(TOPOLOGY_EDGE_KINDS)(value.edge_kind)
  ) {
    return null
  }
  const edgeKind = value.edge_kind
  if (edgeKind === 'parent') {
    if (!isOneOf(TOPOLOGY_EDGE_SUBTYPES)(value.subtype)) return null
    return {
      id: value.id,
      from_user_id: value.from_user_id,
      to_user_id: value.to_user_id,
      edge_kind: edgeKind,
      subtype: value.subtype,
    }
  }
  // 对称关系必须没有子类型；携带子类型即视为脏数据丢弃
  if (value.subtype !== null) return null
  return {
    id: value.id,
    from_user_id: value.from_user_id,
    to_user_id: value.to_user_id,
    edge_kind: edgeKind,
    subtype: null,
  }
}

/**
 * 结构边数组解码。三种形态显式区分：
 * - 字段缺失（旧后端载荷）→ null，表示结构数据未提供（安全降级提示）；
 * - 字段存在但不是数组 → 拒绝整个载荷（合同破坏，不能静默吞掉）；
 * - 合法数组 → 逐条解码、按 id 防御去重、端点不在已解码节点集合内丢弃。
 */
function decodeTopologyEdges(
  value: unknown,
  visibleIds: ReadonlySet<number>,
): PersonalFamilyViewTopologyEdge[] | null {
  if (value === undefined) return null
  if (!Array.isArray(value)) throw new Error('个人家族视图响应格式无效')
  const seen = new Set<string>()
  const edges: PersonalFamilyViewTopologyEdge[] = []
  for (const raw of value) {
    const edge = decodeTopologyEdge(raw)
    if (edge === null) continue
    if (seen.has(edge.id)) continue
    if (!visibleIds.has(edge.from_user_id) || !visibleIds.has(edge.to_user_id)) continue
    seen.add(edge.id)
    edges.push(edge)
  }
  return edges
}

/**
 * 推测边解码（09-13 推测层）：逐字段运行时校验；任一字段不合法整条丢弃。
 * 端点必须命中已解码节点集合（推测边两端永远可见）。
 */
function decodeInferredEdge(
  value: unknown,
  visibleIds: Set<number>,
): PersonalFamilyViewInferredEdge | null {
  if (!isRecord(value)) return null
  if (
    typeof value.id !== 'number' ||
    typeof value.subject_user_id !== 'number' ||
    typeof value.object_user_id !== 'number' ||
    typeof value.relation_kind !== 'string' ||
    !isNullableString(value.term) ||
    !isNullableString(value.viewer_term) ||
    typeof value.revision !== 'number' ||
    (value.new_user_id !== null && typeof value.new_user_id !== 'number') ||
    !Array.isArray(value.evidence_fact_ids) ||
    !value.evidence_fact_ids.every((v) => typeof v === 'number')
  ) {
    return null
  }
  const path = decodePath(value.path)
  if (path === null) return null
  const viewerPath = decodePath(value.viewer_path)
  if (viewerPath === null) return null
  if (
    !visibleIds.has(value.subject_user_id) ||
    !visibleIds.has(value.object_user_id) ||
    (value.new_user_id !== null && !visibleIds.has(value.new_user_id))
  ) {
    return null
  }
  return {
    id: value.id,
    subject_user_id: value.subject_user_id,
    object_user_id: value.object_user_id,
    relation_kind: value.relation_kind,
    term: value.term,
    path,
    viewer_term: value.viewer_term,
    viewer_path: viewerPath,
    new_user_id: value.new_user_id,
    evidence_fact_ids: value.evidence_fact_ids,
    revision: value.revision,
    created_at: typeof value.created_at === 'string' ? value.created_at : '',
  }
}

export function decodePersonalFamilyView(value: unknown): PersonalFamilyViewData {
  if (!isRecord(value)) throw new Error('个人家族视图响应格式无效')
  if (
    typeof value.space_id !== 'number' ||
    typeof value.view_version !== 'number' ||
    !isViewStatus(value.status) ||
    !Array.isArray(value.nodes) ||
    !Array.isArray(value.edges) ||
    typeof value.truncated !== 'boolean' ||
    !isNullableString(value.next_cursor) ||
    !isNullableString(value.computed_at ?? null) ||
    !isNullableString(value.stale_reason ?? null)
  ) {
    throw new Error('个人家族视图响应格式无效')
  }

  const nodes: PersonalFamilyViewNode[] = []
  const visibleIds = new Set<number>()
  for (const raw of value.nodes) {
    const node = decodeNode(raw)
    if (node === null) continue
    nodes.push(node)
    visibleIds.add(node.user_id)
  }

  // 边两端必须都在已解码节点集合内：任一端被丢弃即不渲染该边，避免悬挂引用
  const edges: PersonalFamilyViewEdge[] = []
  for (const raw of value.edges) {
    const edge = decodeEdge(raw)
    if (edge === null) continue
    if (!visibleIds.has(edge.from_user_id) || !visibleIds.has(edge.to_user_id)) continue
    edges.push(edge)
  }

  // 推测边（09-13）：载荷缺失/开关关闭 → 空数组；逐条校验，坏条目静默丢弃
  const inferredEdges: PersonalFamilyViewInferredEdge[] = []
  if (Array.isArray(value.inferred_edges)) {
    for (const raw of value.inferred_edges) {
      const inferred = decodeInferredEdge(raw, visibleIds)
      if (inferred === null) continue
      inferredEdges.push(inferred)
    }
  }
  const topologyEdges = decodeTopologyEdges(value.topology_edges, visibleIds)

  return {
    space_id: value.space_id,
    status: value.status,
    view_version: value.view_version,
    computed_at: typeof value.computed_at === 'string' ? value.computed_at : null,
    nodes,
    inferred_edges: inferredEdges,
    edges,
    topology_edges: topologyEdges,
    truncated: value.truncated,
    next_cursor: value.next_cursor,
    stale_reason: typeof value.stale_reason === 'string' ? value.stale_reason : null,
  }
}

/**
 * 按明确 space_id 读取投影。传入上一份 etag 时走条件请求：
 * 304 返回 `null`，由 store 保留同一安全快照（design.md §4.1）。
 */
export async function fetchPersonalFamilyView(
  spaceId: number,
  etag?: string | null,
): Promise<PersonalFamilyViewSnapshot | null> {
  const response = await apiClient.get<unknown>('/personal-family-view', {
    params: { space_id: spaceId },
    headers: etag ? { 'If-None-Match': etag } : undefined,
    // 304 不是错误：交由调用方复用既有快照
    validateStatus: (status) => (status >= 200 && status < 300) || status === 304,
  })
  if (response.status === 304) return null
  const nextEtag = response.headers?.etag ?? response.headers?.ETag ?? null
  return {
    data: decodePersonalFamilyView(response.data),
    etag: typeof nextEtag === 'string' ? nextEtag : null,
  }
}
