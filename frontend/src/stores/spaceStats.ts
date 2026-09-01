import { ref } from 'vue'
import { defineStore } from 'pinia'

import { fetchSpaceStats } from '@/api/spaceStats'
import type { SpaceStatsData, SpaceStatsSnapshot } from '@/types/api'

/**
 * 空间化统计状态（design.md §4.4）：按明确 space_id 保存服务端授权聚合。
 *
 * 红线：
 * - 只消费 api/spaceStats.ts 的已解码投影；不从 PersonalFamilyView 节点数组、
 *   graph store 或任何本地数组推导统计；
 * - 统计跟随当前选中空间，不做默认跨空间总计；
 * - 每个请求绑定 space + epoch，切换空间或清理后旧响应一律丢弃（不回写）；
 * - 无乐观更新：304 保留同一快照对象，变更一律重新读服务端。
 */
export const useSpaceStatsStore = defineStore('spaceStats', () => {
  const bySpace = ref<Map<number, SpaceStatsSnapshot>>(new Map())
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

  function forSpace(spaceId: number): SpaceStatsData | null {
    return bySpace.value.get(spaceId)?.data ?? null
  }

  /**
   * 读取指定空间的授权统计。`force` 跳过 ETag 条件请求（用户显式刷新）；
   * 否则带上已有 etag，304 时保留同一快照对象。
   */
  async function load(
    spaceId: number,
    options: { force?: boolean } = {},
  ): Promise<SpaceStatsData | null> {
    const requestEpoch = epoch
    const cached = bySpace.value.get(spaceId) ?? null
    setLoading(spaceId, true)
    setError(spaceId, null)
    try {
      const snapshot = await fetchSpaceStats(
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

  function refresh(spaceId: number): Promise<SpaceStatsData | null> {
    return load(spaceId, { force: true })
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
    clearSpace,
    clear,
  }
})
