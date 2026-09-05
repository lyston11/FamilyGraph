/**
 * 敏感详情访问会话 store。
 *
 * 红线（PRD FE-F5 / design §5）：
 * - 明文票据只存 Pinia 内存，绝不写 localStorage/sessionStorage；
 * - 票据绑定单 user/space，不可跨目标复用；
 * - TTL 30 分钟；本地过期判定带 5 秒时钟余量，过期即丢弃并要求重新申请；
 * - 403 ADMIN_ACCESS_SESSION_INVALID（无/错目标/过期/撤销/跨管理员）时
 *   清掉对应内存票据，引导重新提交理由。
 */

import { reactive } from 'vue'
import { defineStore } from 'pinia'
import { apiCreateAccessSession } from '@/api/read'
import type { AccessTargetType } from '@/types/api'

export interface AccessTicket {
  token: string
  /** epoch ms */
  expiresAt: number
}

/** 本地过期提前量：规避客户端与服务端时钟的毫秒级漂移。 */
const EXPIRY_SKEW_MS = 5000

export function accessTicketKey(targetType: AccessTargetType, targetId: number): string {
  return `${targetType}:${targetId}`
}

export const useAdminAccessSessionStore = defineStore('adminAccessSession', () => {
  // key: `${target_type}:${target_id}` → 票据
  const tickets = reactive(new Map<string, AccessTicket>())

  function isExpired(ticket: AccessTicket): boolean {
    return ticket.expiresAt - EXPIRY_SKEW_MS <= Date.now()
  }

  /** 已绑定且未过期的票据；无/过期返回 null（过期条目顺手清理）。 */
  function peekValid(targetType: AccessTargetType, targetId: number): string | null {
    const key = accessTicketKey(targetType, targetId)
    const ticket = tickets.get(key)
    if (!ticket) return null
    if (isExpired(ticket)) {
      tickets.delete(key)
      return null
    }
    return ticket.token
  }

  /** 提交理由换取绑定单目标的 30 分钟票据；已有有效票据则复用。 */
  async function ensureTicket(
    targetType: AccessTargetType,
    targetId: number,
    reason: string,
  ): Promise<string> {
    const existing = peekValid(targetType, targetId)
    if (existing) return existing
    const created = await apiCreateAccessSession({
      target_type: targetType,
      target_id: targetId,
      reason,
    })
    const ticket: AccessTicket = {
      token: created.session_id,
      expiresAt: Date.parse(created.expires_at),
    }
    if (Number.isNaN(ticket.expiresAt)) {
      // 合同异常：绝不能把无过期时间的票据当有效票据用
      throw new Error('access session contract violation: invalid expires_at')
    }
    tickets.set(accessTicketKey(targetType, targetId), ticket)
    return ticket.token
  }

  /** 票据被服务端拒绝（403/错目标）：清内存票据并要求重新授权。 */
  function revoke(targetType: AccessTargetType, targetId: number): void {
    tickets.delete(accessTicketKey(targetType, targetId))
  }

  /** logout / 会话过期 / 目标切换 / 卸载时清空全部内存票据。 */
  function clearAll(): void {
    tickets.clear()
  }

  return { tickets, peekValid, ensureTicket, revoke, clearAll }
})
