import { ref } from 'vue'
import { defineStore } from 'pinia'

import {
  dismissSuggestion,
  fetchSuggestionDetail,
  fetchSuggestions,
  submitSuggestion,
} from '@/api/stewardSuggestions'
import { useKinshipStore } from '@/stores/kinship'
import { useNotificationsStore } from '@/stores/notifications'
import type {
  SuggestionItem,
  SuggestionsPage,
  SuggestionSubmitPreferenceResult,
  SuggestionSubmitProposalResult,
} from '@/types/api'

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
  /** 按 ID 详情缓存：键 `${spaceId}:${suggestionId}`；切空间/清理时随列表一并清除 */
  const detailBySpaceAndId = ref<Map<string, SuggestionItem>>(new Map())
  const loadingSpaceIds = ref<Set<number>>(new Set())
  const errorBySpace = ref<Map<number, unknown>>(new Map())
  /** 请求代际：clear/clearSpace 递增，late response 校验后丢弃 */
  let epoch = 0
  const spaceEpochs = new Map<number, number>()
  const listRequests = new Map<number, number>()
  const detailRequests = new Map<string, number>()
  let nextRequestId = 0

  function versionOf(spaceId: number): string {
    return `${epoch}:${spaceEpochs.get(spaceId) ?? 0}`
  }

  function detailFor(spaceId: number, suggestionId: number): SuggestionItem | null {
    return detailBySpaceAndId.value.get(`${spaceId}:${suggestionId}`) ?? null
  }

  function cacheDetail(spaceId: number, item: SuggestionItem): void {
    detailBySpaceAndId.value = new Map(detailBySpaceAndId.value).set(
      `${spaceId}:${item.id}`,
      item,
    )
  }

  function refreshNotifications(spaceId: number): Promise<unknown> {
    const notifications = useNotificationsStore()
    // 正在进行的已读/列表请求也必须失效，不能在动作完成后覆盖新领域状态。
    notifications.clearSpace(spaceId)
    return notifications.refresh(spaceId)
  }

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
    const requestVersion = versionOf(spaceId)
    const requestId = ++nextRequestId
    listRequests.set(spaceId, requestId)
    const isCurrent = () =>
      requestVersion === versionOf(spaceId) && listRequests.get(spaceId) === requestId
    setLoading(spaceId, true)
    setError(spaceId, null)
    try {
      const page = await fetchSuggestions(spaceId)
      // 空间已切走或缓存已清：这条响应属于旧上下文，不得回写
      if (!isCurrent()) return null
      bySpace.value = new Map(bySpace.value).set(spaceId, page)
      return page
    } catch (cause) {
      if (isCurrent()) setError(spaceId, cause)
      throw cause
    } finally {
      if (isCurrent()) setLoading(spaceId, false)
    }
  }

  /** 驳回（幂等）：成功后重读服务端列表对齐 state/revision */
  async function dismiss(
    spaceId: number,
    suggestionId: number,
    expectedRevision: number,
  ): Promise<SuggestionItem | null> {
    const requestVersion = versionOf(spaceId)
    await dismissSuggestion(spaceId, suggestionId, expectedRevision)
    if (requestVersion !== versionOf(spaceId)) return null
    const details = new Map(detailBySpaceAndId.value)
    details.delete(`${spaceId}:${suggestionId}`)
    detailBySpaceAndId.value = details
    const [detail] = await Promise.allSettled([
      loadDetail(spaceId, suggestionId),
      load(spaceId),
      refreshNotifications(spaceId),
    ])
    if (requestVersion !== versionOf(spaceId)) return null
    return detail.status === 'fulfilled' ? detail.value : null
  }

  /** 提交（confirm=true + Idempotency-Key）：成功后重读列表 */
  async function submit(
    spaceId: number,
    suggestionId: number,
    body: { expected_revision: number; evidence_hash: string; confirm: true },
    idempotencyKey: string,
  ): Promise<SuggestionSubmitProposalResult | SuggestionSubmitPreferenceResult | null> {
    const requestEpoch = epoch
    const requestVersion = versionOf(spaceId)
    const result = await submitSuggestion(spaceId, suggestionId, body, idempotencyKey)
    if (requestEpoch !== epoch) return null
    // 个人词条跨空间生效；空间切换不取消已成功写入的本人偏好失效通知。
    const refreshTerms = 'linked_preference' in result
      ? useKinshipStore().refreshAfterTermChange(spaceId, 'personal')
      : Promise.resolve()
    if (requestVersion !== versionOf(spaceId)) {
      await refreshTerms
      return null
    }
    detailRequests.set(`${spaceId}:${suggestionId}`, ++nextRequestId)
    cacheDetail(spaceId, result.suggestion)
    await Promise.allSettled([load(spaceId), refreshNotifications(spaceId), refreshTerms])
    return requestVersion === versionOf(spaceId) ? result : null
  }

  /** 按 ID 读取详情（超过首页缓存的旧建议也能打开）；epoch 隔离旧响应 */
  async function loadDetail(spaceId: number, suggestionId: number): Promise<SuggestionItem | null> {
    const requestVersion = versionOf(spaceId)
    const key = `${spaceId}:${suggestionId}`
    const requestId = ++nextRequestId
    detailRequests.set(key, requestId)
    // 每次打开都重验服务端当前状态，缓存仅供当前视图消费，不替代授权读取。
    const item = await fetchSuggestionDetail(spaceId, suggestionId)
    if (requestVersion !== versionOf(spaceId) || detailRequests.get(key) !== requestId) return null
    cacheDetail(spaceId, item)
    return item
  }

  function clearSpace(spaceId: number): void {
    spaceEpochs.set(spaceId, (spaceEpochs.get(spaceId) ?? 0) + 1)
    listRequests.delete(spaceId)
    const nextSpaces = new Map(bySpace.value)
    nextSpaces.delete(spaceId)
    bySpace.value = nextSpaces
    const nextDetails = new Map(detailBySpaceAndId.value)
    for (const key of Array.from(nextDetails.keys())) {
      if (key.startsWith(`${spaceId}:`)) nextDetails.delete(key)
    }
    for (const key of detailRequests.keys()) {
      if (key.startsWith(`${spaceId}:`)) detailRequests.delete(key)
    }
    detailBySpaceAndId.value = nextDetails
    setLoading(spaceId, false)
    setError(spaceId, null)
  }

  function clear(): void {
    epoch += 1
    spaceEpochs.clear()
    listRequests.clear()
    detailRequests.clear()
    bySpace.value = new Map()
    detailBySpaceAndId.value = new Map()
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
    loadDetail,
    detailFor,
    dismiss,
    submit,
    clearSpace,
    clear,
  }
})
