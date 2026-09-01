/**
 * 通知展示投影（PRD §2.6 / design §5.4）：kind/domain_status 的中文标签与
 * 三分区归类纯函数。只做展示层分类，不改变任何领域状态，也不推导授权。
 *
 * 三个分区严格按服务端字段归类：
 * - 待我处理：ActionCard 引用且领域仍 pending（去处理走既有 ActionCard 流程）；
 * - 已完成·历史：read_at 有值且领域已到终态（非 pending 即视为该通知的稳定态）；
 * - 通知：其余（含未读的领域终态告知、bridge/relation/membership 通知）。
 */
import type { NotificationDomainStatus, NotificationItem, NotificationKind } from '@/types/api'

export type NotificationSection = 'pending-action' | 'notices' | 'history'

export const NOTIFICATION_KIND_LABELS: Record<NotificationKind, string> = {
  action_card: '管家建议',
  space_membership: '空间成员',
  bridge: '家族连接',
  relation: '关系',
}

export const NOTIFICATION_DOMAIN_STATUS_LABELS: Record<NotificationDomainStatus, string> = {
  pending: '待处理',
  active: '已生效',
  accepted: '已接受',
  rejected: '已拒绝',
  cancelled: '已取消',
  revoked: '已撤销',
  expired: '已过期',
  withdrawn: '已撤回',
  removed: '已移除',
  done: '已完成',
}

/** 领域状态徽章阶（design.md §3.4 tokens.css 工具类，双主题同源） */
export const NOTIFICATION_DOMAIN_STATUS_BADGES: Record<NotificationDomainStatus, string> = {
  pending: 'fg-badge--accent',
  active: 'fg-badge--confirmed',
  accepted: 'fg-badge--confirmed',
  rejected: 'fg-badge--disputed',
  cancelled: 'fg-badge--neutral',
  revoked: 'fg-badge--disputed',
  expired: 'fg-badge--provisional',
  withdrawn: 'fg-badge--neutral',
  removed: 'fg-badge--neutral',
  done: 'fg-badge--confirmed',
}

/** 通知分区归类：ActionCard 引用且未处理 → 待我处理；已读且领域终态 → 历史 */
export function classifyNotification(item: NotificationItem): NotificationSection {
  if (item.kind === 'action_card' && item.domain_status === 'pending') {
    return 'pending-action'
  }
  if (item.read_at !== null && item.domain_status !== 'pending') {
    return 'history'
  }
  return 'notices'
}

export function classifyNotifications(items: readonly NotificationItem[]): {
  pendingAction: NotificationItem[]
  notices: NotificationItem[]
  history: NotificationItem[]
} {
  const pendingAction: NotificationItem[] = []
  const notices: NotificationItem[] = []
  const history: NotificationItem[] = []
  for (const item of items) {
    const section = classifyNotification(item)
    if (section === 'pending-action') pendingAction.push(item)
    else if (section === 'history') history.push(item)
    else notices.push(item)
  }
  return { pendingAction, notices, history }
}
