import { apiClient } from './client'

import type {
  ActionCard,
  ActionCardExecuteResponse,
  ActionCardState,
  ActionCardTransitionResponse,
} from '@/types/actionCard'

/**
 * ActionCard 浏览器 API 封装（V2.4 Block S3）。
 *
 * 复用统一 axios 实例：Authorization 注入与 401 静默刷新由拦截器负责；
 * 错误统一抛 ApiError。后端合同（S1/S2 收敛基线）：
 * - view/dismiss/accept 并发竞争 → 409 CARD_STATE_CONFLICT；
 * - 活跃态卡片过期 → 410 CARD_EXPIRED；
 * - execute 可重试失败 → 409 CARD_EXECUTE_REJECTED（detail.reason，服务端保持 accepted）；
 *   终态失效 → 410；
 * - flag 关闭 / 非活跃成员 → 403 SPACE_FORBIDDEN_ACTOR。
 */

export const ACTION_CARD_ERRORS = {
  CARD_STATE_CONFLICT: 'CARD_STATE_CONFLICT',
  CARD_EXPIRED: 'CARD_EXPIRED',
  CARD_EXECUTE_REJECTED: 'CARD_EXECUTE_REJECTED',
  SPACE_FORBIDDEN_ACTOR: 'SPACE_FORBIDDEN_ACTOR',
} as const

const ERROR_COPY: Record<string, string> = {
  CARD_STATE_CONFLICT: '卡片状态刚被其他操作更新，已为你刷新最新状态',
  CARD_EXPIRED: '该建议已过期失效',
  CARD_EXECUTE_REJECTED: '当前条件已发生变化，暂时无法执行，可稍后重试',
  SPACE_FORBIDDEN_ACTOR: '你已不是该空间的活跃成员',
}

/** 只映射用户文案，不透传 detail（spec/backend/error-handling.md） */
export function friendlyActionCardError(code: string, fallback?: string): string {
  return code in ERROR_COPY ? (ERROR_COPY[code] as string) : (fallback ?? '操作失败，请稍后重试')
}

export async function fetchActionCards(
  spaceId: number,
  state?: ActionCardState,
): Promise<ActionCard[]> {
  const { data } = await apiClient.get<ActionCard[]>('/action-cards', {
    params: state === undefined ? { space_id: spaceId } : { space_id: spaceId, state },
  })
  return data
}

export async function viewActionCard(cardId: number): Promise<ActionCardTransitionResponse> {
  const { data } = await apiClient.post<ActionCardTransitionResponse>(
    `/action-cards/${cardId}/view`,
  )
  return data
}

export async function dismissActionCard(cardId: number): Promise<ActionCardTransitionResponse> {
  const { data } = await apiClient.post<ActionCardTransitionResponse>(
    `/action-cards/${cardId}/dismiss`,
  )
  return data
}

export async function acceptActionCard(cardId: number): Promise<ActionCardTransitionResponse> {
  const { data } = await apiClient.post<ActionCardTransitionResponse>(
    `/action-cards/${cardId}/accept`,
  )
  return data
}

export async function executeActionCard(cardId: number): Promise<ActionCardExecuteResponse> {
  const { data } = await apiClient.post<ActionCardExecuteResponse>(
    `/action-cards/${cardId}/execute`,
    {},
  )
  return data
}
