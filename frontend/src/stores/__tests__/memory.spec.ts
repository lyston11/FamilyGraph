import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as memoryApi from '@/api/memory'
import { ApiError } from '@/api/errors'
import { useMemoryStore } from '@/stores/memory'
import type { Memory, MemoryCandidate, RagSearchResult } from '@/types/memory'

vi.mock('@/api/memory', () => ({
  fetchPlatformFeatures: vi.fn(),
  fetchMemoryCandidates: vi.fn(),
  fetchMemories: vi.fn(),
  confirmMemoryCandidate: vi.fn(),
  createMemoryCandidate: vi.fn(),
  dismissMemoryCandidate: vi.fn(),
  revokeMemory: vi.fn(),
  deleteMemory: vi.fn(),
  searchMemory: vi.fn().mockResolvedValue([]),
  friendlyMemoryError: vi.fn((_code: string, fallback?: string) => fallback ?? '操作失败'),
}))

const mockedFetchCandidates = vi.mocked(memoryApi.fetchMemoryCandidates)
const mockedFetchFeatures = vi.mocked(memoryApi.fetchPlatformFeatures)
const mockedFetchMemories = vi.mocked(memoryApi.fetchMemories)
const mockedConfirm = vi.mocked(memoryApi.confirmMemoryCandidate)
const mockedCreateCandidate = vi.mocked(memoryApi.createMemoryCandidate)
const mockedDismiss = vi.mocked(memoryApi.dismissMemoryCandidate)
const mockedRevoke = vi.mocked(memoryApi.revokeMemory)
const mockedDelete = vi.mocked(memoryApi.deleteMemory)
const mockedSearch = vi.mocked(memoryApi.searchMemory)

const candidate: MemoryCandidate = {
  id: 1,
  source_message_id: 7,
  source_document_ref: null,
  source_span_json: {},
  source_kind: 'agent_message',
  source_status: 'available',
  allowed_scopes: ['private', 'household:5'],
  raw_quote: '原话',
  summary: '摘要',
  suggested_scope: 'household',
  purpose: '家庭参考',
  sensitivity: 'normal',
  extractor_version: 'v1',
  status: 'pending',
  memory_id: null,
  created_at: '2026-08-26T00:00:00',
  decided_at: null,
}

const memory: Memory = {
  id: 2,
  source_candidate_id: 1,
  source_message_id: 7,
  source_document_ref: null,
  source_kind: 'agent_message',
  source_status: 'available',
  allowed_scopes: ['private', 'household:5'],
  raw_quote: '原话',
  content: '摘要',
  purpose: '家庭参考',
  scope: 'household',
  space_id: 5,
  sensitivity: 'normal',
  confirmation_status: 'confirmed',
  revision: 1,
  retention_until: null,
  status: 'active',
  revoked_at: null,
  created_at: '2026-08-26T00:00:00',
  updated_at: '2026-08-26T00:00:00',
}

const createPayload: memoryApi.CreateMemoryCandidatePayload = {
  source: { kind: 'manual' },
  idempotency_key: 'manual-operation-1',
  raw_quote: '我偏好清淡的菜',
  summary: '饮食偏好',
  purpose: '聚餐参考',
  suggested_scope: 'private',
  sensitivity: 'normal',
}

const ragHit: RagSearchResult = {
  chunk_id: 1, document_id: 2, source_type: 'memory', source_id: '2',
  text: '饮食偏好', scope: 'private', sensitivity: 'normal', revision: 1,
  index_version: 'v1', citation_handle: 'rag:2:r1:c1', space_id: null,
  allowed_scopes: ['private'],
}

function seed(): void {
  mockedFetchCandidates.mockResolvedValue([candidate])
  mockedFetchMemories.mockResolvedValue([memory])
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((accept, fail) => { resolve = accept; reject = fail })
  return { promise, resolve, reject }
}

describe('memory store (server state and explicit scope)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    useMemoryStore().features = { memory_enabled: true, rag_enabled: true }
  })

  it('loads candidates and memories for the selected space', async () => {
    seed()
    const store = useMemoryStore()

    await store.loadForSpace(5)

    expect(mockedFetchCandidates).toHaveBeenCalledWith(false)
    expect(mockedFetchMemories).toHaveBeenCalledWith(5)
    expect(store.selectedSpaceId).toBe(5)
    expect(store.candidates).toEqual([candidate])
    expect(store.memories).toEqual([memory])
  })

  it('a failed feature refresh clears a previously enabled state instead of treating it as disabled', async () => {
    const store = useMemoryStore()
    mockedFetchFeatures.mockRejectedValue(new ApiError(503, 'HTTP_ERROR', '暂时无法读取能力状态'))

    await expect(store.loadFeatureState()).rejects.toBeInstanceOf(ApiError)

    expect(store.features).toBeNull()
    expect(store.featureStateKnown).toBe(false)
    expect(store.memoryEnabled).toBe(false)
    expect(store.ragEnabled).toBe(false)
    expect(store.featureStateError?.message).toBe('暂时无法读取能力状态')
    await expect(store.createCandidate(createPayload)).rejects.toMatchObject({ code: 'MEMORY_FEATURE_STATE_UNAVAILABLE' })
    expect(mockedCreateCandidate).not.toHaveBeenCalled()
  })

  it('a late feature response cannot re-enable a cleared account', async () => {
    let resolveFeatures!: (value: { memory_enabled: boolean; rag_enabled: boolean }) => void
    mockedFetchFeatures.mockReturnValue(new Promise((resolve) => { resolveFeatures = resolve }))
    const store = useMemoryStore()
    const pending = store.loadFeatureState()
    store.clear()
    resolveFeatures({ memory_enabled: true, rag_enabled: true })
    await pending

    expect(store.features).toBeNull()
    expect(store.featureStateKnown).toBe(false)
    expect(store.featureStateLoading).toBe(false)
  })

  it('Memory off blocks every write while independent RAG search still works', async () => {
    const store = useMemoryStore()
    store.features = { memory_enabled: false, rag_enabled: true }
    mockedSearch.mockResolvedValue([ragHit])

    for (const write of [
      () => store.createCandidate(createPayload),
      () => store.confirmCandidate(1, 'private'),
      () => store.dismissCandidate(1),
      () => store.revoke(2),
      () => store.remove(2),
    ]) {
      await expect(write()).rejects.toMatchObject({ code: 'MEMORY_DISABLED' })
    }
    for (const api of [mockedCreateCandidate, mockedConfirm, mockedDismiss, mockedRevoke, mockedDelete]) {
      expect(api).not.toHaveBeenCalled()
    }
    await store.search(5, '饮食')
    expect(store.partitionOf(5).ragResults).toEqual([ragHit])
  })

  it.each(['unavailable', 'unverified'] as const)('does not confirm a %s source', async (sourceStatus) => {
    const store = useMemoryStore()
    store.candidates = [{ ...candidate, source_status: sourceStatus, allowed_scopes: [] }]

    await expect(store.confirmCandidate(1, 'private')).rejects.toMatchObject({ code: 'MEMORY_SCOPE_FORBIDDEN' })
    expect(mockedConfirm).not.toHaveBeenCalled()
    expect(store.candidates[0]?.status).toBe('pending')
  })

  it('does not invent a shareable scope absent from the server source projection', async () => {
    const store = useMemoryStore()
    store.candidates = [{ ...candidate, source_kind: 'rag_chunk', allowed_scopes: ['private'] }]

    await expect(store.confirmCandidate(1, 'household:5')).rejects.toMatchObject({ code: 'MEMORY_SCOPE_FORBIDDEN' })
    expect(mockedConfirm).not.toHaveBeenCalled()
  })

  it('creation rejection preserves the last server list and does not insert a candidate', async () => {
    const store = useMemoryStore()
    store.candidates = [candidate]
    const reason = new ApiError(403, 'MEMORY_SCOPE_FORBIDDEN', '来源不可用')
    mockedCreateCandidate.mockRejectedValue(reason)

    await expect(store.createCandidate(createPayload)).rejects.toBe(reason)
    expect(store.candidates).toEqual([candidate])
    expect(store.error?.code).toBe('MEMORY_SCOPE_FORBIDDEN')
    expect(mockedFetchCandidates).not.toHaveBeenCalled()
  })

  it('reports a committed operation whose refresh failed and permits the same-key retry', async () => {
    const store = useMemoryStore()
    mockedCreateCandidate.mockResolvedValue({ ...candidate, id: 9 })
    mockedFetchCandidates.mockRejectedValueOnce(new Error('list unavailable')).mockResolvedValue([{ ...candidate, id: 9 }])

    await expect(store.createCandidate(createPayload)).rejects.toMatchObject({ code: 'MEMORY_REFRESH_FAILED' })
    expect(store.candidates).toEqual([])
    expect(store.error?.message).toContain('操作已提交')

    await store.createCandidate(createPayload)
    expect(mockedCreateCandidate.mock.calls.map(([payload]) => payload.idempotency_key)).toEqual([
      'manual-operation-1', 'manual-operation-1',
    ])
    expect(store.candidates.map((item) => item.id)).toEqual([9])
  })

  it('a rejected confirmation neither removes the pending candidate nor reloads a fake success', async () => {
    const store = useMemoryStore()
    store.candidates = [candidate]
    mockedConfirm.mockRejectedValue(new ApiError(409, 'MEMORY_STATE_CONFLICT', '确认范围已变化'))

    await expect(store.confirmCandidate(1, 'private')).rejects.toMatchObject({ code: 'MEMORY_STATE_CONFLICT' })
    expect(store.candidates).toEqual([candidate])
    expect(mockedFetchCandidates).not.toHaveBeenCalled()
    expect(mockedFetchMemories).not.toHaveBeenCalled()
  })

  it('confirm reloads both server projections and preserves the explicit scope', async () => {
    seed()
    const store = useMemoryStore()
    await store.loadForSpace(5)
    mockedConfirm.mockResolvedValue(memory)
    mockedFetchCandidates.mockResolvedValue([])
    mockedFetchMemories.mockResolvedValue([memory])

    await store.confirmCandidate(1, 'household:5')

    expect(mockedConfirm).toHaveBeenCalledWith(1, { scope: 'household:5' })
    expect(mockedFetchCandidates).toHaveBeenCalledTimes(2)
    expect(mockedFetchMemories).toHaveBeenCalledTimes(2)
    expect(store.candidates).toEqual([])
  })

  it('createCandidate only creates a candidate and re-reads the pending list (no optimistic insert)', async () => {
    seed()
    const store = useMemoryStore()
    await store.loadForSpace(5)
    mockedFetchCandidates.mockClear()

    const created = { ...candidate, id: 9 }
    mockedCreateCandidate.mockResolvedValue(created)
    // 服务端重读：新候选出现在列表中（不做乐观插入）
    mockedFetchCandidates.mockResolvedValue([created])

    await store.createCandidate({
      source: { kind: 'manual' },
      idempotency_key: 'manual-create-one',
      raw_quote: '爷爷生于 1948 年',
      summary: '爷爷出生年份',
      purpose: '族谱整理',
      suggested_scope: 'private',
      sensitivity: 'normal',
    })

    expect(mockedCreateCandidate).toHaveBeenCalledWith({
      source: { kind: 'manual' },
      idempotency_key: 'manual-create-one',
      raw_quote: '爷爷生于 1948 年',
      summary: '爷爷出生年份',
      purpose: '族谱整理',
      suggested_scope: 'private',
      sensitivity: 'normal',
    })
    expect(mockedFetchCandidates).toHaveBeenCalledTimes(1)
    expect(store.candidates).toEqual([created])
  })

  it('dismiss, revoke and delete all re-read server state', async () => {
    seed()
    const store = useMemoryStore()
    await store.loadForSpace(5)
    mockedDismiss.mockResolvedValue({ ...candidate, status: 'dismissed' })
    mockedRevoke.mockResolvedValue({ ...memory, status: 'revoked' })
    mockedDelete.mockResolvedValue(undefined)
    mockedFetchCandidates.mockResolvedValue([])
    mockedFetchMemories.mockResolvedValue([])

    await store.dismissCandidate(1)
    await store.revoke(2)
    await store.remove(2)

    expect(mockedDismiss).toHaveBeenCalledWith(1)
    expect(mockedRevoke).toHaveBeenCalledWith(2)
    expect(mockedDelete).toHaveBeenCalledWith(2)
    expect(mockedFetchCandidates).toHaveBeenCalledTimes(4)
    expect(mockedFetchMemories).toHaveBeenCalledTimes(4)
    expect(store.memories).toEqual([])
  })

  it('keeps private memories and only the selected space shared projection', async () => {
    const privateMemory = { ...memory, id: 6, scope: 'private' as const, space_id: null }
    const otherSpaceMemory = { ...memory, id: 7, space_id: 6 }
    mockedFetchCandidates.mockResolvedValue([])
    mockedFetchMemories.mockResolvedValue([privateMemory, memory, otherSpaceMemory])
    const store = useMemoryStore()

    await store.loadForSpace(5)
    expect(store.memories).toEqual([privateMemory, memory])

    await store.loadForSpace(6)
    expect(store.memories).toEqual([privateMemory, otherSpaceMemory])
  })

  it('clear invalidates in-flight responses', async () => {
    let resolveCandidates: ((value: MemoryCandidate[]) => void) | undefined
    let resolveMemories: ((value: Memory[]) => void) | undefined
    mockedFetchCandidates.mockReturnValue(new Promise((resolve) => { resolveCandidates = resolve }))
    mockedFetchMemories.mockReturnValue(new Promise((resolve) => { resolveMemories = resolve }))
    const store = useMemoryStore()
    const loading = store.loadForSpace(5)
    store.clear()

    resolveCandidates?.([candidate])
    resolveMemories?.([memory])
    await loading

    expect(store.candidates).toEqual([])
    expect(store.memories).toEqual([])
  })

  it('resetForSpace clears the space partition including shared memories and RAG citations', async () => {
    const sharedMemory = { ...memory, id: 8, scope: 'household' as const, space_id: 5 }
    mockedFetchCandidates.mockResolvedValue([])
    mockedFetchMemories.mockResolvedValue([sharedMemory])
    mockedSearch.mockResolvedValue([{
      chunk_id: 1,
      document_id: 2,
      source_type: 'memory',
      source_id: '8',
      text: '春节包饺子',
      scope: 'household:5',
      sensitivity: 'normal',
      revision: 1,
      index_version: 'v1',
      space_id: 5,
      allowed_scopes: ['private', 'household:5'],
      citation_handle: 'rag:8:r1:c1',
    }])
    const store = useMemoryStore()

    await store.ensureMemories(5)
    await store.search(5, '春节')
    expect(store.partitionOf(5).memories).toHaveLength(1)
    expect(store.partitionOf(5).ragResults).toHaveLength(1)

    // 空间切换清理（useSpaceContext 调用）：共享记忆与 RAG 引用一并失效
    store.resetForSpace(5)
    expect(store.selectedSpaceId).toBeNull()
    const partition = store.partitionOf(5)
    expect(partition.memories).toHaveLength(0)
    expect(partition.ragResults).toHaveLength(0)
    expect(partition.loaded).toBe(false)
  })

  it('a late old query cannot replace the latest result or repopulate a cleared search', async () => {
    let resolveOld!: (value: RagSearchResult[]) => void
    mockedSearch.mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve }))
      .mockResolvedValueOnce([{ ...ragHit, chunk_id: 2, text: '最新查询结果' }])
    const store = useMemoryStore()
    const oldQuery = store.search(5, '旧查询')
    await store.search(5, '新查询')
    resolveOld([ragHit])
    await oldQuery
    expect(store.partitionOf(5).ragResults[0]?.text).toBe('最新查询结果')

    mockedSearch.mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve }))
    const lastQuery = store.search(5, '再次查询')
    await store.search(5, '')
    resolveOld([ragHit])
    await lastQuery
    expect(store.partitionOf(5).ragResults).toEqual([])
    expect(store.partitionOf(5).ragLoading).toBe(false)
  })

  it('source mutations clear old saveable RAG results and fence their pending responses', async () => {
    seed()
    let resolveSearch!: (value: RagSearchResult[]) => void
    mockedSearch.mockReturnValueOnce(new Promise((resolve) => { resolveSearch = resolve }))
    mockedRevoke.mockResolvedValue({ ...memory, status: 'revoked' })
    const store = useMemoryStore()
    store.partitionOf(5).ragResults = [ragHit]
    const pendingSearch = store.search(5, '饮食')
    await store.revoke(2)
    resolveSearch([ragHit])
    await pendingSearch

    expect(store.partitionOf(5).ragResults).toEqual([])
  })

  it('a delayed combined refresh cannot restore an available candidate over a newer restricted projection', async () => {
    const oldCandidates = deferred<MemoryCandidate[]>()
    mockedFetchMemories.mockResolvedValue([])
    mockedFetchCandidates.mockReturnValueOnce(oldCandidates.promise).mockResolvedValueOnce([{
      ...candidate, source_status: 'unavailable', allowed_scopes: [], raw_quote: null, summary: null, purpose: null,
    }])
    const store = useMemoryStore()
    const oldRefresh = store.loadForSpace(5)
    await vi.waitFor(() => expect(mockedFetchCandidates).toHaveBeenCalledTimes(1))
    await store.loadCandidates()
    oldCandidates.resolve([candidate])
    await oldRefresh

    expect(store.candidates[0]?.source_status).toBe('unavailable')
    expect(store.candidates[0]?.raw_quote).toBeNull()
  })

  it('a delayed private list cannot replace a newer restricted projection', async () => {
    const oldMemories = deferred<Memory[]>()
    mockedFetchMemories.mockReturnValueOnce(oldMemories.promise).mockResolvedValueOnce([{
      ...memory, scope: 'private', space_id: null, source_status: 'unavailable',
      raw_quote: null, content: null, purpose: null, allowed_scopes: [],
    }])
    const store = useMemoryStore()
    const oldLoad = store.loadPrivateMemories()
    await store.loadPrivateMemories()
    oldMemories.resolve([{ ...memory, scope: 'private', space_id: null }])
    await oldLoad

    expect(store.privateMemories[0]?.source_status).toBe('unavailable')
    expect(store.privateMemories[0]?.raw_quote).toBeNull()
  })

  it('a pending space list cannot restore a revoked memory after a mutation refresh', async () => {
    const oldMemories = deferred<Memory[]>()
    mockedFetchMemories.mockReturnValueOnce(oldMemories.promise).mockResolvedValueOnce([])
    mockedFetchCandidates.mockResolvedValue([])
    mockedRevoke.mockResolvedValue({ ...memory, status: 'revoked' })
    const store = useMemoryStore()
    const oldLoad = store.ensureMemories(5)
    await store.revoke(memory.id, 5)
    oldMemories.resolve([memory])
    await oldLoad

    expect(store.partitionOf(5).memories).toEqual([])
  })

  it('a reset space stays removed when its old combined refresh completes', async () => {
    const oldMemories = deferred<Memory[]>()
    mockedFetchMemories.mockReturnValueOnce(oldMemories.promise)
    mockedFetchCandidates.mockResolvedValue([])
    const store = useMemoryStore()
    const otherPartition = store.partitionOf(6)
    otherPartition.memories = [{ ...memory, space_id: 6 }]
    const oldLoad = store.loadForSpace(5)
    store.resetForSpace(5)
    oldMemories.resolve([memory])
    await oldLoad

    expect(store.partitions.has(5)).toBe(false)
    expect(store.partitionOf(6)).toBe(otherPartition)
    expect(store.partitionOf(6).memories).toHaveLength(1)
  })

  it.each(['clear', 'reset'] as const)('an old mutation cannot start another refresh after %s', async (reset) => {
    const oldRevoke = deferred<Memory>()
    mockedRevoke.mockReturnValueOnce(oldRevoke.promise)
    mockedFetchMemories.mockResolvedValue([memory])
    mockedFetchCandidates.mockResolvedValue([candidate])
    const store = useMemoryStore()
    store.selectedSpaceId = 5
    store.partitionOf(5).memories = [memory]
    const result = store.revoke(memory.id, 5).catch((reason: unknown) => reason)
    if (reset === 'clear') store.clear()
    else store.resetForSpace(5)
    oldRevoke.resolve({ ...memory, status: 'revoked' })
    expect(await result).toMatchObject({ code: 'MEMORY_STATE_CONFLICT' })

    expect(mockedFetchMemories).not.toHaveBeenCalled()
    expect(mockedFetchCandidates).not.toHaveBeenCalled()
    expect(store.partitions.has(5)).toBe(false)
    expect(store.candidates).toEqual([])
  })

  it('a refresh failure discards the previously readable private and shared lists', async () => {
    const store = useMemoryStore()
    store.privateMemories = [{ ...memory, scope: 'private', space_id: null }]
    store.partitionOf(5).memories = [memory]
    mockedFetchMemories.mockRejectedValue(new ApiError(403, 'MEMORY_SCOPE_FORBIDDEN', '读取权限已变化'))
    mockedFetchCandidates.mockResolvedValue([])

    await expect(store.loadForSpace(5)).rejects.toMatchObject({ code: 'MEMORY_SCOPE_FORBIDDEN' })

    expect(store.privateMemories).toEqual([])
    expect(store.partitionOf(5).memories).toEqual([])
    expect(store.partitionOf(5).loaded).toBe(false)
  })

  it('late load failures do not put the cleared account back into an error state', async () => {
    const oldMemories = deferred<Memory[]>()
    mockedFetchMemories.mockReturnValueOnce(oldMemories.promise)
    const store = useMemoryStore()
    const loading = store.loadPrivateMemories().catch((reason: unknown) => reason)
    store.clear()
    oldMemories.reject(new ApiError(403, 'MEMORY_SCOPE_FORBIDDEN', '旧账户无权读取'))
    await loading

    expect(store.error).toBeNull()
  })

  it('feature refresh discards cached and pending RAG results across disable and re-enable', async () => {
    const oldSearch = deferred<RagSearchResult[]>()
    mockedSearch.mockReturnValueOnce(oldSearch.promise)
    mockedFetchFeatures.mockResolvedValueOnce({ memory_enabled: true, rag_enabled: false })
      .mockResolvedValueOnce({ memory_enabled: true, rag_enabled: true })
    const store = useMemoryStore()
    store.partitionOf(5).ragResults = [ragHit]
    const search = store.search(5, '旧查询')
    await store.loadFeatureState()
    const disabledResults = [...store.partitionOf(5).ragResults]
    await store.loadFeatureState()
    oldSearch.resolve([ragHit])
    await search

    expect(disabledResults).toEqual([])
    expect(store.partitionOf(5).ragResults).toEqual([])
    expect(store.partitionOf(5).ragLoading).toBe(false)
  })
})
