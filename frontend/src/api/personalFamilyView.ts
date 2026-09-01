import type {
  ClaimStatus,
  GenderType,
  Maskable,
  PersonalFamilyViewData,
  PersonalFamilyViewDisplay,
  PersonalFamilyViewEdge,
  PersonalFamilyViewNode,
  PersonalFamilyViewPathStep,
  PersonalFamilyViewSnapshot,
  PersonalFamilyViewStatus,
  PrivacyMode,
  StructuredDate,
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

  return {
    space_id: value.space_id,
    status: value.status,
    view_version: value.view_version,
    computed_at: typeof value.computed_at === 'string' ? value.computed_at : null,
    nodes,
    edges,
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
