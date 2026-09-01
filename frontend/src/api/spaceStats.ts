import type { DirClass, SpaceStatsData, SpaceStatsRelationSlice, SpaceStatsSnapshot } from '@/types/api'

import { isNullableString, isOneOf, isRecord, isViewStatus } from './decode'
import { apiClient } from './client'

/**
 * SpaceStats 客户端合同占位（design.md §4.4）。
 *
 * BLOCKER: 服务端合同未落地 —— 下方路径与载荷形状为前端约定，后端任务对齐前
 * 仅以 fixture/decoder/store 测试驱动（对齐清单见任务 notes.md
 * 「前端客户端合同占位（待服务端任务对齐）」）。
 *
 * 合同（占位）：
 * - 请求：`GET /stats?space_id=<id>`，为既有 `/stats` 端点的空间限定查询形态；
 *   旧无空间合同（api/stats.ts）已随 Phase 5 删除，统计页无任何消费方，
 *   本模块不做旧语义回退；
 * - 载荷：`SpaceStatsData`（types/api.ts）—— space_id / space_kind / status /
 *   view_version / node_count / edge_count / member_count /
 *   relation_distribution: [{ dir_class, count }] /
 *   pending_action_cards / pending_memberships / computed_at / stale_reason；
 * - 聚合为服务端授权口径：隐藏对象与未授权分支不计入，前端不得从
 *   PersonalFamilyView 节点数组推导统计；
 * - decoder 从 `unknown` 解码为共享类型：顶层标量非法即整份拒绝，
 *   单个分布切片非法（含未知 dir_class）仅丢弃该切片。
 */

const SPACE_KINDS: readonly ('household' | 'lineage')[] = ['household', 'lineage']
const isSpaceKind = isOneOf(SPACE_KINDS)

const DIR_CLASSES: readonly DirClass[] = ['elder', 'younger', 'peer', 'spouse']
const isDirClass = isOneOf(DIR_CLASSES)

function decodeRelationSlice(value: unknown): SpaceStatsRelationSlice | null {
  if (!isRecord(value)) return null
  if (!isDirClass(value.dir_class) || typeof value.count !== 'number') return null
  return { dir_class: value.dir_class, count: value.count }
}

export function decodeSpaceStats(value: unknown): SpaceStatsData {
  if (!isRecord(value)) throw new Error('空间统计响应格式无效')
  if (
    typeof value.space_id !== 'number' ||
    !isSpaceKind(value.space_kind) ||
    !isViewStatus(value.status) ||
    (value.view_version !== null && typeof value.view_version !== 'number') ||
    typeof value.node_count !== 'number' ||
    typeof value.edge_count !== 'number' ||
    typeof value.member_count !== 'number' ||
    !Array.isArray(value.relation_distribution) ||
    typeof value.pending_action_cards !== 'number' ||
    typeof value.pending_memberships !== 'number' ||
    !isNullableString(value.computed_at ?? null) ||
    !isNullableString(value.stale_reason ?? null)
  ) {
    throw new Error('空间统计响应格式无效')
  }

  const relationDistribution: SpaceStatsRelationSlice[] = []
  for (const raw of value.relation_distribution) {
    const slice = decodeRelationSlice(raw)
    if (slice === null) continue
    relationDistribution.push(slice)
  }

  return {
    space_id: value.space_id,
    space_kind: value.space_kind,
    status: value.status,
    view_version: typeof value.view_version === 'number' ? value.view_version : null,
    node_count: value.node_count,
    edge_count: value.edge_count,
    member_count: value.member_count,
    relation_distribution: relationDistribution,
    pending_action_cards: value.pending_action_cards,
    pending_memberships: value.pending_memberships,
    computed_at: typeof value.computed_at === 'string' ? value.computed_at : null,
    stale_reason: typeof value.stale_reason === 'string' ? value.stale_reason : null,
  }
}

/**
 * 按明确 space_id 读取授权统计。传入上一份 etag 时走条件请求：
 * 304 返回 `null`，由 store 保留同一安全快照。
 */
export async function fetchSpaceStats(
  spaceId: number,
  etag?: string | null,
): Promise<SpaceStatsSnapshot | null> {
  const response = await apiClient.get<unknown>('/stats', {
    params: { space_id: spaceId },
    headers: etag ? { 'If-None-Match': etag } : undefined,
    // 304 不是错误：交由调用方复用既有快照
    validateStatus: (status) => (status >= 200 && status < 300) || status === 304,
  })
  if (response.status === 304) return null
  const nextEtag = response.headers?.etag ?? response.headers?.ETag ?? null
  return {
    data: decodeSpaceStats(response.data),
    etag: typeof nextEtag === 'string' ? nextEtag : null,
  }
}
