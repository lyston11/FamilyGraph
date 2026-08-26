import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '@/api/errors'
import * as memoryApi from '@/api/memory'
import type { Memory, MemoryCandidate, MemoryCitation, MemoryScope } from '@/types/memory'

export interface MemoryStoreError {
  code: string
  message: string
}

export interface MemoryPartition {
  memories: Memory[]
  ragResults: MemoryCitation[]
  ragLoading: boolean
  loaded: boolean
  requestId: number
}

function emptyPartition(): MemoryPartition {
  return {
    memories: [],
    ragResults: [],
    ragLoading: false,
    loaded: false,
    requestId: 0,
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
  let storeGeneration = 0

  function partitionOf(spaceId: number): MemoryPartition {
    let partition = partitions.value.get(spaceId)
    if (!partition) {
      partition = emptyPartition()
      partitions.value.set(spaceId, partition)
    }
    return partition
  }

  async function loadCandidates(includeDecided = false): Promise<void> {
    const requestId = ++candidateRequestId
    candidatesLoading.value = true
    try {
      const result = await memoryApi.fetchMemoryCandidates(includeDecided)
      if (requestId === candidateRequestId) candidates.value = result
    } catch (reason) {
      if (requestId === candidateRequestId) error.value = toStoreError(reason)
      throw reason
    } finally {
      if (requestId === candidateRequestId) candidatesLoading.value = false
    }
  }

  async function loadPrivateMemories(): Promise<void> {
    const generation = storeGeneration
    try {
      const result = await memoryApi.fetchMemories()
      if (generation === storeGeneration) privateMemories.value = result
    } catch (reason) {
      error.value = toStoreError(reason)
      throw reason
    }
  }

  async function ensureMemories(spaceId: number): Promise<void> {
    selectedSpaceId.value = spaceId
    const generation = storeGeneration
    const partition = partitionOf(spaceId)
    const requestId = ++partition.requestId
    try {
      const result = await memoryApi.fetchMemories(spaceId)
      if (generation !== storeGeneration || requestId !== partition.requestId) return
      partition.memories = result.filter((item) => item.space_id === spaceId && item.scope !== 'private')
      partition.loaded = true
    } catch (reason) {
      error.value = toStoreError(reason)
      throw reason
    }
  }

  async function loadForSpace(spaceId: number | null): Promise<void> {
    selectedSpaceId.value = spaceId
    error.value = null
    const generation = storeGeneration
    const result = await memoryApi.fetchMemories(spaceId === null ? undefined : spaceId)
    const nextCandidates = await memoryApi.fetchMemoryCandidates()
    if (generation !== storeGeneration) return
    privateMemories.value = result.filter((item) => item.scope === 'private' || item.space_id === null)
    if (spaceId !== null) {
      const partition = partitionOf(spaceId)
      partition.memories = result.filter((item) => item.space_id === spaceId && item.scope !== 'private')
      partition.loaded = true
    }
    candidates.value = nextCandidates
  }

  async function refreshAfterMutation(spaceId = selectedSpaceId.value): Promise<void> {
    await loadForSpace(spaceId)
  }

  async function confirmCandidate(
    candidateId: number,
    scope: MemoryScope,
    retentionDays: number | null = null,
  ): Promise<void> {
    await memoryApi.confirmMemoryCandidate(candidateId, {
      scope,
      ...(retentionDays === null ? {} : { retention_days: retentionDays }),
    })
    await refreshAfterMutation(sharedSpaceId(scope) ?? selectedSpaceId.value)
  }

  async function dismissCandidate(candidateId: number): Promise<void> {
    await memoryApi.dismissMemoryCandidate(candidateId)
    await refreshAfterMutation()
  }

  async function revoke(memoryId: number, spaceId: number | null = null): Promise<void> {
    await memoryApi.revokeMemory(memoryId)
    await refreshAfterMutation(spaceId)
  }

  async function remove(memoryId: number, spaceId: number | null = null): Promise<void> {
    await memoryApi.deleteMemory(memoryId)
    await refreshAfterMutation(spaceId)
  }

  async function search(spaceId: number, query: string): Promise<void> {
    const partition = partitionOf(spaceId)
    const cleanQuery = query.trim()
    if (!cleanQuery) {
      partition.ragResults = []
      return
    }
    partition.ragLoading = true
    try {
      partition.ragResults = await memoryApi.searchMemory(spaceId, cleanQuery)
    } catch (reason) {
      error.value = toStoreError(reason)
      throw reason
    } finally {
      partition.ragLoading = false
    }
  }

  function resetForSpace(spaceId: number): void {
    partitions.value.delete(spaceId)
    if (selectedSpaceId.value === spaceId) selectedSpaceId.value = null
  }

  function clear(): void {
    storeGeneration += 1
    candidateRequestId += 1
    candidates.value = []
    privateMemories.value = []
    partitions.value.clear()
    selectedSpaceId.value = null
    candidatesLoading.value = false
    error.value = null
  }

  return {
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
    dismissCandidate,
    revoke,
    remove,
    search,
    resetForSpace,
    clear,
  }
})
