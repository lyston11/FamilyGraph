import { ref } from 'vue'
import { defineStore } from 'pinia'

import {
  dismissSuggestion,
  fetchSuggestions,
  submitSuggestion,
} from '@/api/stewardSuggestions'
import type { SuggestionsPage } from '@/types/api'

/**
 * Steward 建议审核状态（09-11 candidate-review）：按账号 + 明确 space_id 保存
 * 服务端授权的建议列表。
 *
 * 红线：
 * - 建议 kind/state/allowed_actions 全部来自服务端投影，前端不本地推导授权；
 * - 打开详情（open_details）与提交（submit）/驳回（dismiss）严格分离：
 *   查看不产生任何写请求，提交必须显式 confirm=true + Idempotency-Key；
 * - 无乐观更新：dismiss/submit 成功后一律重读服务端列表；
 * - 每个请求绑定 epoch：切换空间/清理/登出后旧响应一律丢弃（不回写），
 *   401 会话失效时由 apiClient 统一清理（clear）。
 */
export const useStewardSuggestionsStore = defineStore('stewardSuggestions', () => {
  const bySpace = ref<Map<number, SuggestionsPage>>(new Map())
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

  function forSpace(spaceId: number): SuggestionsPage | null {
    return bySpace.value.get(spaceId) ?? null
  }

  /** 活跃待核实建议（proposed/submitted 且未被本人驳回） */
  function activeForSpace(spaceId: number): SuggestionsPage['items'] {
    return (forSpace(spaceId) ?? { items: [] }).items
  }

  async function load(spaceId: number): Promise<SuggestionsPage | null> {
    const requestEpoch = epoch
    setLoading(spaceId, true)
    setError(spaceId, null)
    try {
      const page = await fetchSuggestions(spaceId)
      // 空间已切走或缓存已清：这条响应属于旧上下文，不得回写
      if (requestEpoch !== epoch) return null
      bySpace.value = new Map(bySpace.value).set(spaceId, page)
      return page
    } catch (cause) {
      if (requestEpoch === epoch) setError(spaceId, cause)
      throw cause
    } finally {
      if (requestEpoch === epoch) setLoading(spaceId, false)
    }
  }

  /** 驳回（幂等）：成功后重读服务端列表对齐 state/revision */
  async function dismiss(
    spaceId: number,
    suggestionId: number,
    expectedRevision: number,
  ): Promise<void> {
    const requestEpoch = epoch
    await dismissSuggestion(spaceId, suggestionId, expectedRevision)
    if (requestEpoch !== epoch) return
    await load(spaceId).catch(() => undefined)
  }

  /** 提交（confirm=true + Idempotency-Key）：成功后重读列表 */
  async function submit(
    spaceId: number,
    suggestionId: number,
    body: { expected_revision: number; evidence_hash: string; confirm: true },
    idempotencyKey: string,
  ): Promise<void> {
    const requestEpoch = epoch
    await submitSuggestion(spaceId, suggestionId, body, idempotencyKey)
    if (requestEpoch !== epoch) return
    await load(spaceId).catch(() => undefined)
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
    forSpace,
    activeForSpace,
    load,
    dismiss,
    submit,
    clearSpace,
    clear,
  }
})
