import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '@/api/errors'
import * as memoryApi from '@/api/memory'
import type {
  Memory,
  MemoryCandidate,
  MemoryScope,
  PlatformFeatureFlags,
  RagSearchResult,
} from '@/types/memory'

export interface MemoryStoreError {
  code: string
  message: string
}

export interface MemoryPartition {
  memories: Memory[]
  ragResults: RagSearchResult[]
  ragLoading: boolean
  loaded: boolean
  requestId: number
  searchRequestId: number
}

function emptyPartition(): MemoryPartition {
  return {
    memories: [],
    ragResults: [],
    ragLoading: false,
    loaded: false,
    requestId: 0,
    searchRequestId: 0,
  }
}

function toStoreError(reason: unknown): MemoryStoreError {
  if (reason instanceof ApiError) return { code: reason.code, message: reason.message }
  return { code: 'MEMORY_LOAD_FAILED', message: '记忆暂时无法加载，请稍后重试' }
}

function sharedSpaceId(scope: MemoryScope): number | null {
  const match = /^(?:household|lineage):(\d+)$/.exec(scope)
  return match ? Number(match[1]) : null
}

/**
 * Server-backed memory projections. Candidate, memory and RAG statuses are
 * never changed locally: every mutation is followed by fresh server reads.
 * Shared memories are partitioned by space so a selected space cannot inherit
 * another space's private or shared data.
 */
export const useMemoryStore = defineStore('memory', () => {
  const features = ref<PlatformFeatureFlags | null>(null)
  const featureStateLoading = ref(false)
  const featureStateError = ref<MemoryStoreError | null>(null)
  const featureStateKnown = computed(
    () => features.value !== null && !featureStateLoading.value && featureStateError.value === null,
  )
  const memoryEnabled = computed(() => featureStateKnown.value && features.value?.memory_enabled === true)
  const ragEnabled = computed(() => featureStateKnown.value && features.value?.rag_enabled === true)

  const candidates = ref<MemoryCandidate[]>([])
  const privateMemories = ref<Memory[]>([])
  const partitions = ref<Map<number, MemoryPartition>>(new Map())
  const selectedSpaceId = ref<number | null>(null)
  const candidatesLoading = ref(false)
  const error = ref<MemoryStoreError | null>(null)
  const pendingCandidates = computed(() => candidates.value.filter((item) => item.status === 'pending'))
  const memories = computed(() => {
    const shared = selectedSpaceId.value === null
      ? []
      : partitionOf(selectedSpaceId.value).memories
    return [...privateMemories.value, ...shared]
  })

  let candidateRequestId = 0
  let privateRequestId = 0
  let featureRequestId = 0
  let storeGeneration = 0

  function partitionOf(spaceId: number): MemoryPartition {
    let partition = partitions.value.get(spaceId)
    if (!partition) {
      partitions.value.set(spaceId, emptyPartition())
      partition = partitions.value.get(spaceId)!
    }
    return partition
  }

  function invalidateRagResults(): void {
    for (const partition of partitions.value.values()) {
      partition.searchRequestId += 1
      partition.ragResults = []
      partition.ragLoading = false
    }
  }

  async function loadFeatureState(): Promise<PlatformFeatureFlags> {
    const requestId = ++featureRequestId
    // Reopening search after an unknown/disabled state requires a fresh read.
    invalidateRagResults()
    featureStateLoading.value = true
    featureStateError.value = null
    try {
      const result = await memoryApi.fetchPlatformFeatures()
      if (requestId === featureRequestId) features.value = result
      return result
    } catch (reason) {
      if (requestId === featureRequestId) {
        features.value = null
        featureStateError.value = toStoreError(reason)
      }
      throw reason
    } finally {
      if (requestId === featureRequestId) featureStateLoading.value = false
    }
  }
  async function loadCandidates(includeDecided = false): Promise<void> {
    const requestId = ++candidateRequestId
    candidatesLoading.value = true
    try {
      const result = await memoryApi.fetchMemoryCandidates(includeDecided)
      if (requestId === candidateRequestId) candidates.value = result
    } catch (reason) {
      if (requestId === candidateRequestId) {
        candidates.value = []
        error.value = toStoreError(reason)
      }
      throw reason
    } finally {
      if (requestId === candidateRequestId) candidatesLoading.value = false
    }
  }


  async function loadMemoryProjection(spaceId: number | null, includePrivate: boolean): Promise<void> {
    const generation = storeGeneration
    const privateId = includePrivate ? ++privateRequestId : null
    const partition = spaceId === null ? null : partitionOf(spaceId)
    const requestId = partition === null ? null : ++partition.requestId
    const currentContext = () => generation === storeGeneration &&
      (spaceId === null || partitions.value.get(spaceId) === partition)
    const currentPrivate = () => privateId !== null && privateId === privateRequestId
    const currentShared = () => partition !== null && requestId === partition.requestId
    try {
      const result = await memoryApi.fetchMemories(spaceId === null ? undefined : spaceId)
      if (!currentContext()) return
      if (currentPrivate()) privateMemories.value = result.filter((item) => item.scope === 'private')
      if (partition && currentShared()) {
        partition.memories = result.filter((item) => item.space_id === spaceId && item.scope !== 'private')
        partition.loaded = true
      }
    } catch (reason) {
      if (currentContext() && (currentPrivate() || currentShared())) {
        if (currentPrivate()) privateMemories.value = []
        if (partition && currentShared()) {
          partition.memories = []
          partition.loaded = false
        }
        error.value = toStoreError(reason)
      }
      throw reason
    }
  }

  async function loadPrivateMemories(): Promise<void> {
    await loadMemoryProjection(null, true)
  }

  async function ensureMemories(spaceId: number): Promise<void> {
    selectedSpaceId.value = spaceId
    await loadMemoryProjection(spaceId, false)
  }

  async function loadForSpace(spaceId: number | null): Promise<void> {
    selectedSpaceId.value = spaceId
    error.value = null
    await refreshAfterMutation(spaceId)
  }

  async function refreshAfterMutation(spaceId = selectedSpaceId.value): Promise<void> {
    // Use the same projection requests as standalone reloads so their newer
    // authorization results cannot be overwritten by a delayed combined load.
    await Promise.all([loadMemoryProjection(spaceId, true), loadCandidates()])
  }

  function requireMemoryEnabled(): void {
    if (!featureStateKnown.value) {
      throw new ApiError(503, 'MEMORY_FEATURE_STATE_UNAVAILABLE', '能力状态暂时无法确认，请刷新后重试')
    }
    if (!memoryEnabled.value) {
      throw new ApiError(403, 'MEMORY_DISABLED', '记忆功能当前未启用，请联系系统管理员')
    }
  }

  async function mutateAndRefresh<T>(
    mutate: () => Promise<T>,
    refresh: () => Promise<void>,
    spaceId = selectedSpaceId.value,
  ): Promise<T> {
    requireMemoryEnabled()
    const generation = storeGeneration
    const partition = spaceId === null ? null : partitionOf(spaceId)
    const currentContext = () => generation === storeGeneration &&
      (spaceId === null || partitions.value.get(spaceId) === partition)
    const staleContext = () => new ApiError(409, 'MEMORY_STATE_CONFLICT', '会话或空间已变化，请重新加载记忆状态')
    error.value = null
    let result: T
    try {
      result = await mutate()
    } catch (reason) {
      if (currentContext()) error.value = toStoreError(reason)
      throw reason
    }
    // A completed request from a cleared account/space must not issue new
    // reads using the current credentials or recreate the removed partition.
    if (!currentContext()) throw staleContext()
    // An old search hit must not remain saveable after a source mutation.
    invalidateRagResults()
    try {
      await refresh()
    } catch {
      if (!currentContext()) throw staleContext()
      const reason = new ApiError(503, 'MEMORY_REFRESH_FAILED', '操作已提交，但最新状态未能加载，请刷新或重试')
      error.value = toStoreError(reason)
      throw reason
    }
    if (!currentContext()) throw staleContext()
    return result
  }

  async function confirmCandidate(
    candidateId: number,
    scope: MemoryScope,
    retentionDays: number | null = null,
  ): Promise<void> {
    const candidate = candidates.value.find((item) => item.id === candidateId)
    if (candidate && (candidate.source_status !== 'available' || !candidate.allowed_scopes.includes(scope))) {
      throw new ApiError(403, 'MEMORY_SCOPE_FORBIDDEN', '来源当前不可用或不允许保存到所选范围，请刷新后重试')
    }
    const spaceId = sharedSpaceId(scope) ?? selectedSpaceId.value
    await mutateAndRefresh(
      () => memoryApi.confirmMemoryCandidate(candidateId, {
        scope,
        ...(retentionDays === null ? {} : { retention_days: retentionDays }),
      }),
      () => refreshAfterMutation(spaceId),
      spaceId,
    )
  }

  /**
   * 手动新建候选（私有记忆「新增」与检索结果「保存」共用入口）：
   * 只创建候选（POST /memory-candidates），成功后重读服务端候选列表；
   * 不做乐观插入，也不直接创建可检索记忆（V2.5 合同）。
   */
  async function createCandidate(
    payload: memoryApi.CreateMemoryCandidatePayload,
  ): Promise<MemoryCandidate> {
    return mutateAndRefresh(
      () => memoryApi.createMemoryCandidate(payload),
      () => loadCandidates(),
      payload.source.kind === 'rag_chunk' ? payload.source.space_id : selectedSpaceId.value,
    )
  }

  async function dismissCandidate(candidateId: number): Promise<void> {
    const spaceId = selectedSpaceId.value
    await mutateAndRefresh(() => memoryApi.dismissMemoryCandidate(candidateId), () => refreshAfterMutation(spaceId), spaceId)
  }

  async function revoke(memoryId: number, spaceId: number | null = null): Promise<void> {
    await mutateAndRefresh(() => memoryApi.revokeMemory(memoryId), () => refreshAfterMutation(spaceId), spaceId ?? selectedSpaceId.value)
  }

  async function remove(memoryId: number, spaceId: number | null = null): Promise<void> {
    await mutateAndRefresh(() => memoryApi.deleteMemory(memoryId), () => refreshAfterMutation(spaceId), spaceId ?? selectedSpaceId.value)
  }

  async function search(spaceId: number, query: string): Promise<void> {
    if (!featureStateKnown.value) {
      throw new ApiError(503, 'MEMORY_FEATURE_STATE_UNAVAILABLE', '能力状态暂时无法确认，请刷新后重试')
    }
    if (!ragEnabled.value) throw new ApiError(403, 'RAG_DISABLED', '检索与引用当前未启用')
    const partition = partitionOf(spaceId)
    const requestId = ++partition.searchRequestId
    const generation = storeGeneration
    const cleanQuery = query.trim()
    if (!cleanQuery) {
      partition.ragResults = []
      partition.ragLoading = false
      return
    }
    partition.ragLoading = true
    try {
      const result = await memoryApi.searchMemory(spaceId, cleanQuery)
      if (generation === storeGeneration && requestId === partition.searchRequestId && ragEnabled.value) {
        partition.ragResults = result
      }
    } catch (reason) {
      if (generation === storeGeneration && requestId === partition.searchRequestId) {
        partition.ragResults = []
        error.value = toStoreError(reason)
      }
      throw reason
    } finally {
      if (requestId === partition.searchRequestId) partition.ragLoading = false
    }
  }

  function resetForSpace(spaceId: number): void {
    partitions.value.delete(spaceId)
    if (selectedSpaceId.value === spaceId) selectedSpaceId.value = null
  }

  function clear(): void {
    storeGeneration += 1
    candidateRequestId += 1
    privateRequestId += 1
    featureRequestId += 1
    features.value = null
    featureStateLoading.value = false
    featureStateError.value = null
    candidates.value = []
    privateMemories.value = []
    partitions.value.clear()
    selectedSpaceId.value = null
    candidatesLoading.value = false
    error.value = null
  }

  return {
    features,
    memoryEnabled,
    ragEnabled,
    featureStateKnown,
    featureStateLoading,
    featureStateError,
    loadFeatureState,
    candidates,
    pendingCandidates,
    memories,
    privateMemories,
    partitions,
    selectedSpaceId,
    candidatesLoading,
    error,
    partitionOf,
    loadCandidates,
    loadPrivateMemories,
    ensureMemories,
    loadForSpace,
    confirmCandidate,
    createCandidate,
    dismissCandidate,
    revoke,
    remove,
    search,
    resetForSpace,
    clear,
  }
})
