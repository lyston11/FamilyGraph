import type {
  Maskable,
  NotificationActionCardRef,
  NotificationDomainStatus,
  NotificationItem,
  NotificationKind,
  NotificationReadAllResult,
  NotificationReadResult,
  NotificationsPage,
  NotificationsSnapshot,
  NotificationPayload,
} from '@/types/api'

import { isNullableString, isOneOf, isRecord, decodeMaskable } from './decode'
import { apiClient } from './client'

/**
 * Notifications 客户端合同占位（design.md §4.4）。
 *
 * BLOCKER: 服务端合同未落地 —— 下方路径与载荷形状为前端约定，后端任务对齐前
 * 仅以 fixture/decoder/store 测试驱动（对齐清单见任务 notes.md
 * 「前端客户端合同占位（待服务端任务对齐）」）。
 *
 * 合同（占位）：
 * - `GET /notifications?space_id=<id>`：按账号 + space_id 返回列表与未读数，
 *   携带 `If-None-Match` 走条件请求，304 由调用方复用上一份安全快照；
 * - `POST /notifications/{id}/read`：仅置已读（响应 `{ id, read_at }`），
 *   服务端不得触发任何领域状态变更；
 * - `POST /notifications/read-all`（body `{ space_id }`）：全部已读，
 *   响应 `{ space_id, marked_count }`；
 * - 已读状态（read_at）、ActionCard 引用（card_id/revision）与领域状态
 *   （domain_status）三者严格分离：通知已读不改变 ActionCard 或领域状态；
 * - 通知按账号 + space_id 过滤，Bridge 管理员通知不得携带敏感家庭数据；
 * - decoder 从 `unknown` 解码为共享类型：顶层标量非法即整份拒绝，
 *   单条通知非法（含未知 kind/domain_status、action_card 引用损坏）仅丢弃该条。
 */

const NOTIFICATION_KINDS: readonly NotificationKind[] = [
  'action_card',
  'space_membership',
  'bridge',
  'relation',
]

const NOTIFICATION_DOMAIN_STATUSES: readonly NotificationDomainStatus[] = [
  'pending',
  'active',
  'accepted',
  'rejected',
  'cancelled',
  'revoked',
  'expired',
  'withdrawn',
  'removed',
  'done',
]

const isNotificationKind = isOneOf(NOTIFICATION_KINDS)
const isNotificationDomainStatus = isOneOf(NOTIFICATION_DOMAIN_STATUSES)

/** `Maskable<string> | null` 解码：null=不适用，masked=不可见，其余必须为明文字符串 */
function decodeNullableMaskableString(value: unknown): Maskable<string> | null | undefined {
  if (value === null) return null
  return decodeMaskable(value, (candidate): candidate is string => typeof candidate === 'string')
}

function decodeActionCardRef(value: unknown): NotificationActionCardRef | null {
  if (!isRecord(value)) return null
  if (typeof value.card_id !== 'number' || typeof value.revision !== 'number') return null
  return { card_id: value.card_id, revision: value.revision }
}

function decodeNotificationPayload(value: unknown): NotificationPayload | null {
  if (!isRecord(value)) return null
  if (typeof value.title !== 'string') return null
  const summary = decodeNullableMaskableString(value.summary)
  const actorName = decodeNullableMaskableString(value.actor_name)
  const spaceName = decodeNullableMaskableString(value.space_name)
  if (summary === undefined || actorName === undefined || spaceName === undefined) return null
  return { title: value.title, summary, actor_name: actorName, space_name: spaceName }
}

function decodeNotificationItem(value: unknown): NotificationItem | null {
  if (!isRecord(value)) return null
  if (
    typeof value.id !== 'number' ||
    typeof value.space_id !== 'number' ||
    !isNotificationKind(value.kind) ||
    !isNotificationDomainStatus(value.domain_status) ||
    typeof value.created_at !== 'string' ||
    !isNullableString(value.read_at ?? null)
  ) {
    return null
  }
  const payload = decodeNotificationPayload(value.payload)
  if (payload === null) return null

  // 引用字段存在但损坏 → 整条丢弃；kind='action_card' 却无引用同样视为脏数据
  let actionCard: NotificationActionCardRef | null = null
  if (value.action_card !== null && value.action_card !== undefined) {
    const ref = decodeActionCardRef(value.action_card)
    if (ref === null) return null
    actionCard = ref
  }
  if (value.kind === 'action_card' && actionCard === null) return null

  return {
    id: value.id,
    space_id: value.space_id,
    kind: value.kind,
    payload,
    domain_status: value.domain_status,
    action_card: actionCard,
    created_at: value.created_at,
    read_at: typeof value.read_at === 'string' ? value.read_at : null,
  }
}

export function decodeNotificationsPage(value: unknown): NotificationsPage {
  if (!isRecord(value)) throw new Error('通知列表响应格式无效')
  if (
    typeof value.space_id !== 'number' ||
    !Array.isArray(value.items) ||
    typeof value.unread_count !== 'number'
  ) {
    throw new Error('通知列表响应格式无效')
  }
  const items: NotificationItem[] = []
  for (const raw of value.items) {
    const item = decodeNotificationItem(raw)
    if (item === null) continue
    items.push(item)
  }
  return { space_id: value.space_id, items, unread_count: value.unread_count }
}

function decodeReadResult(value: unknown): NotificationReadResult {
  if (!isRecord(value) || typeof value.id !== 'number' || typeof value.read_at !== 'string') {
    throw new Error('通知已读响应格式无效')
  }
  return { id: value.id, read_at: value.read_at }
}

function decodeReadAllResult(value: unknown): NotificationReadAllResult {
  if (
    !isRecord(value) ||
    typeof value.space_id !== 'number' ||
    typeof value.marked_count !== 'number'
  ) {
    throw new Error('通知全部已读响应格式无效')
  }
  return { space_id: value.space_id, marked_count: value.marked_count }
}

/**
 * 按明确 space_id 读取通知列表。传入上一份 etag 时走条件请求：
 * 304 返回 `null`，由 store 保留同一安全快照。
 */
export async function fetchNotifications(
  spaceId: number,
  etag?: string | null,
): Promise<NotificationsSnapshot | null> {
  const response = await apiClient.get<unknown>('/notifications', {
    params: { space_id: spaceId },
    headers: etag ? { 'If-None-Match': etag } : undefined,
    // 304 不是错误：交由调用方复用既有快照
    validateStatus: (status) => (status >= 200 && status < 300) || status === 304,
  })
  if (response.status === 304) return null
  const nextEtag = response.headers?.etag ?? response.headers?.ETag ?? null
  return {
    data: decodeNotificationsPage(response.data),
    etag: typeof nextEtag === 'string' ? nextEtag : null,
  }
}

/** 仅置已读：服务端不得触发任何领域状态变更，也不得改写 ActionCard revision */
export async function markNotificationRead(notificationId: number): Promise<NotificationReadResult> {
  const { data } = await apiClient.post<unknown>(`/notifications/${notificationId}/read`)
  return decodeReadResult(data)
}

/** 全部已读：按当前 space_id 提交，同样不触发任何领域状态变更 */
export async function markAllNotificationsRead(spaceId: number): Promise<NotificationReadAllResult> {
  const { data } = await apiClient.post<unknown>('/notifications/read-all', {
    space_id: spaceId,
  })
  return decodeReadAllResult(data)
}
