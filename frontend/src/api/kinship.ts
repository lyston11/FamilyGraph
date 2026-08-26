import { apiClient } from './client'

import type {
  KinshipResolve,
  MyTerm,
  ParseResult,
  UsageCreated,
} from '@/types/kinship'

/**
 * Kinship 浏览器 API 封装（V2.3 Block E4c）。
 *
 * 复用统一 axios 实例：Authorization 注入与 401 静默刷新由拦截器负责；
 * 错误统一抛 ApiError（feature flag 关闭时后端返回 503 KINSHIP_FLAG_DISABLED，
 * 由 store 捕获并驱动 UI 隐藏入口）。
 */

export const KINSHIP_FLAG_DISABLED = 'KINSHIP_FLAG_DISABLED'

/** 列出本人 personal 词条；带 spaceId 时附该空间语境的实时生效解析 */
export async function fetchMyTerms(spaceId?: number): Promise<MyTerm[]> {
  const { data } = await apiClient.get<MyTerm[]>('/kinship/terms/my', {
    params: spaceId === undefined ? undefined : { space_id: spaceId },
  })
  return data
}

/** 个人称谓纠正：立即生效，写领域事件，不改结构关系 */
export async function updateMyTerm(payload: {
  spaceId: number
  conceptCode: string
  term: string
}): Promise<MyTerm> {
  const { data } = await apiClient.put<MyTerm>('/kinship/terms/my', {
    space_id: payload.spaceId,
    concept_code: payload.conceptCode,
    term: payload.term,
  })
  return data
}

/** 主路径 + 称谓 + 来源级别 + 替代路径 + 事实状态（from 必须是登录者本人） */
export async function resolveKinship(
  spaceId: number,
  fromUserId: number,
  toUserId: number,
): Promise<KinshipResolve> {
  const { data } = await apiClient.get<KinshipResolve>('/kinship/resolve', {
    params: { space_id: spaceId, from_user_id: fromUserId, to_user_id: toUserId },
  })
  return data
}

/** 记录使用证据（两人晋升输入）；source_event 固定枚举 */
export async function recordTermUsage(payload: {
  spaceId: number
  conceptCode: string
  term: string
  sourceEvent: 'manual_select' | 'assistant_query'
}): Promise<UsageCreated> {
  const { data } = await apiClient.post<UsageCreated>('/kinship/usages', {
    space_id: payload.spaceId,
    concept_code: payload.conceptCode,
    term: payload.term,
    source_event: payload.sourceEvent,
  })
  return data
}

/** 自由文本解析：原文另存服务端，任何产物不覆盖 SourceFact 与原文 */
export async function parseRelationText(spaceId: number, text: string): Promise<ParseResult> {
  const { data } = await apiClient.post<ParseResult>('/kinship/parse', {
    space_id: spaceId,
    text,
  })
  return data
}
