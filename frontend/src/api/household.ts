import type {
  HouseholdCardActions,
  HouseholdCardData,
  HouseholdCardMember,
  HouseholdCardSnapshot,
} from '@/types/api'

import { isNullableString, isAuthorizedVisibilityLevel, isRecord } from './decode'
import { decodeDisplay } from './personalFamilyView'
import { apiClient } from './client'

/**
 * HouseholdCard 客户端合同占位（design.md §4.2）。
 *
 * BLOCKER: 服务端合同未落地 —— 下方路径与载荷形状为前端约定，后端任务对齐前
 * 仅以 fixture/decoder/store 测试驱动（对齐清单见任务 notes.md
 * 「前端客户端合同占位（待服务端任务对齐）」）。不得回退 `/users` 全局列表或旧
 * members 列表拼装家庭卡。
 *
 * 合同（占位）：
 * - 请求：`GET /household-card?space_id=<id>`，携带 `If-None-Match` 走条件请求，
 *   304 由调用方复用上一份安全快照；
 * - 载荷：`HouseholdCardData`（types/api.ts）——
 *   space_id / space_kind='household' / space_name / view_version / computed_at /
 *   viewer: PersonalFamilyViewDisplay /
 *   members: [{ user_id, display, household_label, visibility_level }] /
 *   allowed_actions: { can_invite_members, can_create_household, empty_state_hint }；
 * - 不包含 lineage 节点数组、隐藏成员数量或其他空间资料；
 * - decoder 从 `unknown` 解码为共享类型：顶层标量/viewer/allowed_actions 非法即整份
 *   拒绝；单个成员非法仅丢弃该成员（fail-closed，不生成占位）。
 */

function decodeHouseholdMember(value: unknown): HouseholdCardMember | null {
  if (!isRecord(value)) return null
  if (
    typeof value.user_id !== 'number' ||
    !isAuthorizedVisibilityLevel(value.visibility_level) ||
    typeof value.household_label !== 'string'
  ) {
    return null
  }
  const display = decodeDisplay(value.display)
  if (display === null) return null
  return {
    user_id: value.user_id,
    display,
    household_label: value.household_label,
    visibility_level: value.visibility_level,
  }
}

function decodeAllowedActions(value: unknown): HouseholdCardActions | null {
  if (!isRecord(value)) return null
  if (
    typeof value.can_invite_members !== 'boolean' ||
    typeof value.can_create_household !== 'boolean' ||
    !isNullableString(value.empty_state_hint ?? null)
  ) {
    return null
  }
  return {
    can_invite_members: value.can_invite_members,
    can_create_household: value.can_create_household,
    empty_state_hint: typeof value.empty_state_hint === 'string' ? value.empty_state_hint : null,
  }
}

export function decodeHouseholdCard(value: unknown): HouseholdCardData {
  if (!isRecord(value)) throw new Error('家庭卡响应格式无效')
  if (
    typeof value.space_id !== 'number' ||
    value.space_kind !== 'household' ||
    typeof value.space_name !== 'string' ||
    typeof value.view_version !== 'number' ||
    !isNullableString(value.computed_at ?? null) ||
    !Array.isArray(value.members)
  ) {
    throw new Error('家庭卡响应格式无效')
  }
  // viewer 是卡片必备主体：其投影不可信时整份载荷不可信
  const viewer = decodeDisplay(value.viewer)
  if (viewer === null) throw new Error('家庭卡响应格式无效')
  const allowedActions = decodeAllowedActions(value.allowed_actions)
  if (allowedActions === null) throw new Error('家庭卡响应格式无效')

  const members: HouseholdCardMember[] = []
  for (const raw of value.members) {
    const member = decodeHouseholdMember(raw)
    if (member === null) continue
    members.push(member)
  }

  return {
    space_id: value.space_id,
    space_kind: 'household',
    space_name: value.space_name,
    view_version: value.view_version,
    computed_at: typeof value.computed_at === 'string' ? value.computed_at : null,
    viewer,
    members,
    allowed_actions: allowedActions,
  }
}

/**
 * 按明确 space_id 读取家庭卡投影。传入上一份 etag 时走条件请求：
 * 304 返回 `null`，由 store 保留同一安全快照。
 */
export async function fetchHouseholdCard(
  spaceId: number,
  etag?: string | null,
): Promise<HouseholdCardSnapshot | null> {
  const response = await apiClient.get<unknown>('/household-card', {
    params: { space_id: spaceId },
    headers: etag ? { 'If-None-Match': etag } : undefined,
    // 304 不是错误：交由调用方复用既有快照
    validateStatus: (status) => (status >= 200 && status < 300) || status === 304,
  })
  if (response.status === 304) return null
  const nextEtag = response.headers?.etag ?? response.headers?.ETag ?? null
  return {
    data: decodeHouseholdCard(response.data),
    etag: typeof nextEtag === 'string' ? nextEtag : null,
  }
}
