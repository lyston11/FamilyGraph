import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as memoryApi from '@/api/memory'
import { useMemoryStore } from '@/stores/memory'
import type { Memory, MemoryCandidate } from '@/types/memory'

vi.mock('@/api/memory', () => ({
  fetchMemoryCandidates: vi.fn(),
  fetchMemories: vi.fn(),
  confirmMemoryCandidate: vi.fn(),
  dismissMemoryCandidate: vi.fn(),
  revokeMemory: vi.fn(),
  deleteMemory: vi.fn(),
}))

const mockedFetchCandidates = vi.mocked(memoryApi.fetchMemoryCandidates)
const mockedFetchMemories = vi.mocked(memoryApi.fetchMemories)
const mockedConfirm = vi.mocked(memoryApi.confirmMemoryCandidate)
const mockedDismiss = vi.mocked(memoryApi.dismissMemoryCandidate)
const mockedRevoke = vi.mocked(memoryApi.revokeMemory)
const mockedDelete = vi.mocked(memoryApi.deleteMemory)

const candidate: MemoryCandidate = {
  id: 1,
  source_message_id: 7,
  source_document_ref: null,
  source_span_json: {},
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

function seed(): void {
  mockedFetchCandidates.mockResolvedValue([candidate])
  mockedFetchMemories.mockResolvedValue([memory])
}

describe('memory store (server state and explicit scope)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads candidates and memories for the selected space', async () => {
    seed()
    const store = useMemoryStore()

    await store.loadForSpace(5)

    expect(mockedFetchCandidates).toHaveBeenCalledWith()
    expect(mockedFetchMemories).toHaveBeenCalledWith(5)
    expect(store.selectedSpaceId).toBe(5)
    expect(store.candidates).toEqual([candidate])
    expect(store.memories).toEqual([memory])
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
})
