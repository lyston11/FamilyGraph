import { ref } from 'vue'
import { defineStore } from 'pinia'

import { fetchPersonalFamilyView } from '@/api/personalFamilyView'
import type {
  PersonalFamilyViewData,
  PersonalFamilyViewEdge,
  PersonalFamilyViewNode,
  PersonalFamilyViewSnapshot,
} from '@/types/api'

/**
 * PersonalFamilyView 状态（design.md §4.1）：按明确 space_id 保存服务端授权快照。
 *
 * 红线：
 * - 组件按 space_id 读取，不存在语义含糊的「当前视图」getter；
 * - 每个请求绑定 space + epoch，切换空间或清理后旧响应一律丢弃（不回写）；
 * - 无乐观更新：所有变更都重新读服务端；
 * - 已解码查询（getVisiblePerson/getRelationshipDetail）在此暴露，组件不碰原始 payload。
 */
export const usePersonalFamilyViewStore = defineStore('personalFamilyView', () => {
  const bySpace = ref<Map<number, PersonalFamilyViewSnapshot>>(new Map())
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

  function forSpace(spaceId: number): PersonalFamilyViewData | null {
    return bySpace.value.get(spaceId)?.data ?? null
  }

  /**
   * 读取指定空间的投影。`force` 跳过 ETag 条件请求（用户显式刷新）；
   * 否则带上已有 etag，304 时保留同一快照对象。
   */
  async function load(
    spaceId: number,
    options: { force?: boolean } = {},
  ): Promise<PersonalFamilyViewData | null> {
    const requestEpoch = epoch
    const cached = bySpace.value.get(spaceId) ?? null
    setLoading(spaceId, true)
    setError(spaceId, null)
    try {
      const snapshot = await fetchPersonalFamilyView(
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

  function refresh(spaceId: number): Promise<PersonalFamilyViewData | null> {
    return load(spaceId, { force: true })
  }

  /**
   * Bridge 状态变化（pending → active / revoke 等）后的授权投影重载入口
   * （PRD §2.4/§2.6 边界）：Bridge 的同意/撤销等操作只存在于通知/待办与领域
   * 流程，本 store 不提供任何 Bridge 写操作；状态变化后由通知/待办接线调用
   * 本入口强制 refresh（跳过 ETag，让服务端重新授权复核）。
   *
   * 权限红线：空间管理员对跨 LineageSpace bridge 只有通知查看权——本入口
   * 只做已授权投影的只读重载，不代表也不提供批准/否决/修改/撤销能力。
   */
  function reloadAfterBridgeChange(spaceId: number): Promise<PersonalFamilyViewData | null> {
    return refresh(spaceId)
  }

  /** 已解码的授权人物查询：只在当前快照内命中，找不到即返回 null（安全不可见） */
  function getVisiblePerson(spaceId: number, userId: number): PersonalFamilyViewNode | null {
    const data = forSpace(spaceId)
    if (data === null) return null
    return data.nodes.find((node) => node.user_id === userId) ?? null
  }

  /** 关系边查询：无方向假设，两端任一顺序命中即返回同一条边 */
  function getRelationshipDetail(spaceId: number, userId: number): PersonalFamilyViewEdge | null {
    const data = forSpace(spaceId)
    if (data === null) return null
    return (
      data.edges.find(
        (edge) => edge.from_user_id === userId || edge.to_user_id === userId,
      ) ?? null
    )
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
    reloadAfterBridgeChange,
    forSpace,
    getVisiblePerson,
    getRelationshipDetail,
    clearSpace,
    clear,
  }
})
