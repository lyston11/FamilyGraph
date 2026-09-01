import { ref } from 'vue'
import { defineStore } from 'pinia'

import {
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from '@/api/notifications'
import type { NotificationsPage, NotificationsSnapshot } from '@/types/api'

/**
 * 通知状态（design.md §4.4）：按账号 + 明确 space_id 保存服务端授权通知列表。
 *
 * 红线：
 * - 已读状态（read_at / unread_count）、ActionCard 引用（card_id/revision）与
 *   领域状态（domain_status）严格分离：markRead 只触发服务端已读 + 重读列表，
 *   不本地改写 domain_status，也不触碰 ActionCard store；
 * - 无乐观更新：已读操作成功后一律重新读取服务端列表；
 * - 每个请求绑定 space + epoch，切换空间或清理后旧响应一律丢弃（不回写）。
 */
export const useNotificationsStore = defineStore('notifications', () => {
  const bySpace = ref<Map<number, NotificationsSnapshot>>(new Map())
  const loadingSpaceIds = ref<Set<number>>(new Set())
  const errorBySpace = ref<Map<number, unknown>>(new Map())
  /** 请求代际：clear/clearSpace 递增，late response 校验后丢弃 */
  let epoch = 0

  function setLoading(spaceId: number, value: boolean): void {
    const next = new Set(loadingSpaceIds.value)
    if (value) next.add(spaceId)
    else next.delete(spaceId)
    loadingSpaceIds.value = next
  }

  function setError(spaceId: number, cause: unknown): void {
    const next = new Map(errorBySpace.value)
    if (cause === null) next.delete(spaceId)
    else next.set(spaceId, cause)
    errorBySpace.value = next
  }

  function isLoading(spaceId: number): boolean {
    return loadingSpaceIds.value.has(spaceId)
  }

  function errorFor(spaceId: number): unknown {
    return errorBySpace.value.get(spaceId) ?? null
  }

  function forSpace(spaceId: number): NotificationsPage | null {
    return bySpace.value.get(spaceId)?.data ?? null
  }

  /** 未读数来自服务端载荷，不从 items 本地推导 */
  function unreadCountOf(spaceId: number): number {
    return forSpace(spaceId)?.unread_count ?? 0
  }

  /**
   * 读取指定空间的通知列表。`force` 跳过 ETag 条件请求；
   * 否则带上已有 etag，304 时保留同一快照对象。
   */
  async function load(
    spaceId: number,
    options: { force?: boolean } = {},
  ): Promise<NotificationsPage | null> {
    const requestEpoch = epoch
    const cached = bySpace.value.get(spaceId) ?? null
    setLoading(spaceId, true)
    setError(spaceId, null)
    try {
      const snapshot = await fetchNotifications(
        spaceId,
        options.force ? null : cached?.etag ?? null,
      )
      // 空间已切走或缓存已清：这条响应属于旧上下文，不得回写
      if (requestEpoch !== epoch) return null
      if (snapshot === null) return cached?.data ?? null
      bySpace.value = new Map(bySpace.value).set(spaceId, snapshot)
      return snapshot.data
    } catch (cause) {
      if (requestEpoch === epoch) setError(spaceId, cause)
      throw cause
    } finally {
      if (requestEpoch === epoch) setLoading(spaceId, false)
    }
  }

  function refresh(spaceId: number): Promise<NotificationsPage | null> {
    return load(spaceId, { force: true })
  }

  /**
   * 单条已读：仅调服务端已读端点，成功后重读列表对齐 read_at/unread_count。
   * 已读不触发任何领域状态变更，也不改写 ActionCard 引用。
   */
  async function markRead(spaceId: number, notificationId: number): Promise<void> {
    const requestEpoch = epoch
    await markNotificationRead(notificationId)
    // 已读请求期间会话/空间被清理：不再为旧上下文回读列表
    if (requestEpoch !== epoch) return
    await load(spaceId)
  }

  /** 全部已读：同样只影响已读状态，成功后重读服务端列表 */
  async function markAllRead(spaceId: number): Promise<void> {
    const requestEpoch = epoch
    await markAllNotificationsRead(spaceId)
    if (requestEpoch !== epoch) return
    await load(spaceId)
  }

  function clearSpace(spaceId: number): void {
    epoch += 1
    const nextSpaces = new Map(bySpace.value)
    nextSpaces.delete(spaceId)
    bySpace.value = nextSpaces
    setLoading(spaceId, false)
    setError(spaceId, null)
  }

  function clear(): void {
    epoch += 1
    bySpace.value = new Map()
    loadingSpaceIds.value = new Set()
    errorBySpace.value = new Map()
  }

  return {
    bySpace,
    loadingSpaceIds,
    errorBySpace,
    isLoading,
    errorFor,
    load,
    refresh,
    forSpace,
    unreadCountOf,
    markRead,
    markAllRead,
    clearSpace,
    clear,
  }
})
