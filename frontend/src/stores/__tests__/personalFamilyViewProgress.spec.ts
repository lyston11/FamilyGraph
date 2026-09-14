import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { deferred, familyNode, familyProgress, familySnapshot } from '@/__tests__/personalFamilyViewFixtures'
import { ApiError } from '@/api/errors'
import { demandPersonalFamilyView, fetchPersonalFamilyView } from '@/api/personalFamilyView'
import { useAuthStore } from '@/stores/auth'
import { usePersonalFamilyViewStore } from '@/stores/personalFamilyView'
import type { PersonalFamilyViewDemandResult, PersonalFamilyViewResponse } from '@/types/api'

vi.mock('@/api/personalFamilyView', () => ({ fetchPersonalFamilyView: vi.fn(), demandPersonalFamilyView: vi.fn() }))
const fetchView = vi.mocked(fetchPersonalFamilyView)
const demandView = vi.mocked(demandPersonalFamilyView)

describe('PFV version ordering and display authorization', () => {
  let store: ReturnType<typeof usePersonalFamilyViewStore>
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-14T00:00:00Z'))
    vi.resetAllMocks()
    setActivePinia(createPinia())
    useAuthStore().user = { id: 1, name: '本人', pin_must_change: false, claim_status: 'claimed', profile_status: 'identity_confirmed' }
    store = usePersonalFamilyViewStore()
  })
  afterEach(() => { store.clear(); store.$dispose(); vi.useRealTimers() })

  it('coalesces readers and lets a forced refresh supersede even a newer-looking late response', async () => {
    const old = deferred<PersonalFamilyViewResponse>()
    const fresh = deferred<PersonalFamilyViewResponse>()
    fetchView.mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
    const first = store.load(9)
    const second = store.load(9)
    expect(fetchView).toHaveBeenCalledTimes(1)
    const refresh = store.refresh(9)
    expect(fetchView.mock.calls[0]?.[2]?.signal?.aborted).toBe(true)
    fresh.resolve(familySnapshot({ progress: familyProgress({ generation: 8, revision: 4 }) }))
    await refresh
    old.resolve(familySnapshot({ progress: familyProgress({ generation: 999, revision: 99 }) }))
    await expect(first).resolves.toBeNull()
    await expect(second).resolves.toBeNull()
    expect(store.forSpace(9)?.progress?.generation).toBe(8)
    expect(store.isLoading(9)).toBe(false)
  })

  it('rejects generation/revision regressions without renewal and replaces all rows on a new generation', async () => {
    fetchView.mockResolvedValueOnce(familySnapshot({ progress: familyProgress({ generation: 8, revision: 4 }) }))
    await store.load(9)
    const current = store.forSpace(9)
    store.rememberPositions(9, [[2, { x: 50, y: 60 }]])
    for (const progress of [familyProgress({ generation: 7, revision: 99 }), familyProgress({ generation: 8, revision: 3 })]) {
      fetchView.mockResolvedValueOnce(familySnapshot({ progress }, { displayExpiresAt: Date.now() + 999_000 }))
      expect(await store.load(9)).toBe(current)
      expect(store.remainingDisplayMs(9)).toBe(60_000)
    }
    fetchView.mockResolvedValueOnce(familySnapshot({ nodes: [familyNode(1)], edges: [], topology_edges: [],
      progress: familyProgress({ generation: 9, revision: 0, topology_revision: 'self-only', targets: [] }) }))
    await store.load(9)
    expect(store.getVisiblePerson(9, 2)).toBeNull()
    expect(store.positionsBySpace.get(9)?.has(2)).toBe(false)
    expect(store.forSpace(9)?.progress?.generation).toBe(9)
  })

  it.each([
    { status: 'queued', phase: 'preparing', reason_code: null },
    { status: 'stale', phase: 'retrying', reason_code: 'input_changed' },
  ] as const)('keeps coordinates through $phase and prunes only after the next authorized skeleton', async ({ status, phase, reason_code }) => {
    fetchView.mockResolvedValueOnce(familySnapshot({ nodes: [familyNode(1), familyNode(2), familyNode(3)] }))
    await store.load(9)
    const position = { x: 721, y: 385 }
    store.rememberPositions(9, [[2, position], [3, { x: 561, y: 700 }]])
    fetchView.mockResolvedValueOnce(familySnapshot({ status, nodes: [], topology_edges: [], edges: [], inferred_edges: [],
      progress: familyProgress({ revision: 2, phase, reason_code, targets: [] }) }))
    await store.refresh(9)
    expect(store.forSpace(9)?.nodes).toEqual([])
    expect(store.getVisiblePerson(9, 2)).toBeNull()
    expect(store.positionsBySpace.get(9)?.get(2)).toEqual(position)
    expect(store.positionsBySpace.get(9)?.has(3)).toBe(true)
    fetchView.mockResolvedValueOnce(familySnapshot({ progress: familyProgress({ generation: 8 }) }))
    await store.refresh(9)
    expect(store.positionsBySpace.get(9)?.get(2)).toEqual(position)
    expect(store.positionsBySpace.get(9)?.has(3)).toBe(false)
  })

  it('an authoritative empty ready preview clears remembered coordinates', async () => {
    fetchView.mockResolvedValueOnce(familySnapshot())
    await store.load(9)
    store.rememberPositions(9, [[2, { x: 721, y: 385 }]])
    fetchView.mockResolvedValueOnce(familySnapshot({ nodes: [], topology_edges: [],
      progress: familyProgress({ generation: 8, phase: 'ready', targets: [], next_poll_ms: 0 }) }))
    await store.refresh(9)
    expect(store.positionsBySpace.get(9)?.size).toBe(0)
  })

  it.each(['personal', 'space'] as const)('%s term invalidation cancels old work and payloads while preserving only this viewer layout', async (scope) => {
    fetchView.mockResolvedValueOnce(familySnapshot()).mockResolvedValueOnce(familySnapshot({ space_id: 10 }))
    await store.load(9)
    await store.load(10)
    const position = { x: 721, y: 385 }
    const viewport = { x: -100, y: 40, zoom: 0.8 }
    for (const spaceId of [9, 10]) {
      store.rememberPositions(spaceId, [[2, position]])
      store.rememberViewport(spaceId, viewport)
      store.rememberViewMode(spaceId, 'canvas')
    }

    const oldResponse = deferred<PersonalFamilyViewResponse>()
    const oldAcknowledgement = deferred<PersonalFamilyViewDemandResult>()
    fetchView.mockReturnValueOnce(oldResponse.promise)
    demandView.mockReturnValueOnce(oldAcknowledgement.promise)
    const oldRead = store.load(9)
    const oldFocus = store.focusTarget(9, 2)
    const readSignal = fetchView.mock.calls.at(-1)?.[2]?.signal
    const focusSignal = demandView.mock.calls.at(-1)?.[1]?.signal
    if (scope === 'personal') store.invalidateTermInputs()
    else store.invalidateTermInputs(9)

    expect(readSignal?.aborted).toBe(true)
    expect(focusSignal?.aborted).toBe(true)
    expect(store.bySpace.has(9)).toBe(false)
    expect(store.remainingDisplayMs(9)).toBeNull()
    expect(store.isLoading(9)).toBe(false)
    expect(store.bySpace.has(10)).toBe(scope === 'space')
    for (const spaceId of [9, 10]) {
      expect(store.positionsBySpace.get(spaceId)?.get(2)).toEqual(position)
      expect(store.viewports.get(spaceId)).toEqual(viewport)
      expect(store.viewModes.get(spaceId)).toBe('canvas')
    }

    fetchView.mockResolvedValueOnce(familySnapshot({ progress: familyProgress({ generation: 8 }) }))
    await store.load(9)
    expect(fetchView).toHaveBeenLastCalledWith(9, null, expect.objectContaining({ progressive: true }))
    oldResponse.resolve(familySnapshot({ progress: familyProgress({ generation: 999 }) }))
    oldAcknowledgement.resolve({ status: 'queued', focus_user_id: 2 })
    await expect(oldRead).resolves.toBeNull()
    await oldFocus
    expect(store.forSpace(9)?.progress?.generation).toBe(8)
    expect(store.positionsBySpace.get(9)?.get(2)).toEqual(position)

    // Preserving layout for a term change must never carry it into another identity.
    store.invalidateTermInputs()
    useAuthStore().user = { ...useAuthStore().user!, id: 4 }
    expect(store.positionsBySpace.size).toBe(0)
    expect(store.viewports.size).toBe(0)
    expect(store.viewModes.size).toBe(0)
  })

  it('renews a matched 304 without replacing data; missing and mismatched headers never extend expiry', async () => {
    fetchView.mockResolvedValueOnce(familySnapshot())
    await store.load(9)
    const data = store.forSpace(9)
    fetchView.mockResolvedValueOnce({ notModified: true, etag: '"7:1"', displayUntil: 123,
      serverDate: Date.now(), displayExpiresAt: Date.now() + 70_000 })
    expect(await store.load(9)).toBe(data)
    expect(store.remainingDisplayMs(9)).toBe(70_000)
    fetchView.mockResolvedValueOnce({ notModified: true, etag: '"7:1"' })
    await store.load(9)
    fetchView.mockResolvedValueOnce({ notModified: true, etag: '"other"', displayExpiresAt: Date.now() + 999_000 })
    await store.load(9)
    expect(store.remainingDisplayMs(9)).toBe(70_000)
    await vi.advanceTimersByTimeAsync(70_001)
    expect(store.bySpace.has(9)).toBe(false)
    expect(store.errorFor(9)).toMatchObject({ code: 'PFV_DISPLAY_EXPIRED' })
  })

  it('a late 304 cannot renew a replacement snapshot', async () => {
    fetchView.mockResolvedValueOnce(familySnapshot())
    await store.load(9)
    const old = deferred<PersonalFamilyViewResponse>()
    fetchView.mockReturnValueOnce(old.promise)
    const oldRead = store.load(9)
    fetchView.mockResolvedValueOnce(familySnapshot({ progress: familyProgress({ generation: 8 }) }))
    await store.refresh(9)
    old.resolve({ notModified: true, etag: '"7:1"', displayExpiresAt: Date.now() + 999_000 })
    await oldRead
    expect(store.forSpace(9)?.progress?.generation).toBe(8)
    expect(store.remainingDisplayMs(9)).toBe(60_000)
  })

  it('remembers version ordering after expiration and never resurrects older or legacy bodies', async () => {
    fetchView.mockResolvedValueOnce(familySnapshot({ progress: familyProgress({ generation: 8, revision: 4 }) },
      { displayExpiresAt: Date.now() + 100 }))
    await store.load(9)
    await vi.advanceTimersByTimeAsync(101)
    for (const progress of [familyProgress({ generation: 7, revision: 99 }), familyProgress({ generation: 8, revision: 3 }), null]) {
      fetchView.mockResolvedValueOnce(familySnapshot({ progress }))
      await store.load(9)
      expect(store.forSpace(9)).toBeNull()
    }
    fetchView.mockResolvedValueOnce(familySnapshot({ progress: familyProgress({ generation: 8, revision: 4 }) }))
    await store.load(9)
    expect(store.forSpace(9)?.progress?.revision).toBe(4)
    expect(store.errorFor(9)).toBeNull()
  })

  it.each([401, 403, 404])('%s clears content and coordinates immediately; a retry never reveals the denied snapshot', async (status) => {
    fetchView.mockResolvedValueOnce(familySnapshot())
    await store.load(9)
    store.rememberPositions(9, [[2, { x: 721, y: 385 }]])
    fetchView.mockRejectedValueOnce(new ApiError(status, 'FORBIDDEN', '无法读取'))
    await expect(store.load(9)).rejects.toMatchObject({ status })
    expect(store.forSpace(9)).toBeNull()
    expect(store.positionsBySpace.has(9)).toBe(false)
    const next = deferred<PersonalFamilyViewResponse>()
    fetchView.mockReturnValueOnce(next.promise)
    const request = store.load(9)
    expect(store.forSpace(9)).toBeNull()
    expect(store.errorFor(9)).toMatchObject({ status })
    next.resolve(familySnapshot({ progress: familyProgress({ generation: 8 }) }))
    await request
    expect(store.errorFor(9)).toBeNull()
  })

  it('retains a network-failed snapshot only until the independently scheduled deadline', async () => {
    fetchView.mockResolvedValueOnce(familySnapshot({}, { displayExpiresAt: Date.now() + 100 }))
    await store.load(9)
    const data = store.forSpace(9)
    fetchView.mockRejectedValueOnce(new ApiError(0, 'NETWORK_ERROR', '连接中断'))
    await expect(store.load(9)).rejects.toMatchObject({ code: 'NETWORK_ERROR' })
    expect(store.forSpace(9)).toBe(data)
    await vi.advanceTimersByTimeAsync(101)
    expect(store.bySpace.has(9)).toBe(false)
  })

  it('rejects progressive data without an explicit valid deadline', async () => {
    fetchView.mockResolvedValueOnce(familySnapshot({}, { displayExpiresAt: null }))
    await expect(store.load(9)).rejects.toMatchObject({ code: 'PFV_DISPLAY_EXPIRED' })
    expect(store.forSpace(9)).toBeNull()
  })

  it('moving the local wall clock backwards does not extend an offline display deadline', async () => {
    fetchView.mockResolvedValueOnce(familySnapshot({}, { displayExpiresAt: Date.now() + 1000 }))
    await store.load(9)
    vi.setSystemTime(Date.now() - 3_600_000)
    await vi.advanceTimersByTimeAsync(1001)
    expect(store.forSpace(9)).toBeNull()
    expect(store.bySpace.has(9)).toBe(false)
  })

  it('identity changes cancel pending reads, data, layout and later callbacks', async () => {
    fetchView.mockResolvedValueOnce(familySnapshot())
    await store.load(9)
    store.rememberViewport(9, { x: 1, y: 2, zoom: 1.4 })
    store.rememberPositions(9, [[2, { x: 721, y: 385 }]])
    const late = deferred<PersonalFamilyViewResponse>()
    fetchView.mockReturnValueOnce(late.promise)
    const request = store.load(9)
    useAuthStore().user = { ...useAuthStore().user!, id: 3 }
    expect(store.bySpace.size).toBe(0)
    expect(store.viewports.size).toBe(0)
    expect(store.positionsBySpace.size).toBe(0)
    late.resolve(familySnapshot())
    await expect(request).resolves.toBeNull()
    expect(store.bySpace.size).toBe(0)
  })

  it('deduplicates authorized pending-target demand and sends an explicit bounded retry', async () => {
    fetchView.mockResolvedValue(familySnapshot())
    await store.load(9)
    const response = deferred<PersonalFamilyViewDemandResult>()
    demandView.mockReturnValueOnce(response.promise)
    const first = store.focusTarget(9, 2)
    const second = store.focusTarget(9, 2)
    await store.focusTarget(9, 999)
    expect(demandView).toHaveBeenCalledTimes(1)
    response.resolve({ status: 'already_active', focus_user_id: 2 })
    await Promise.all([first, second])
    await store.focusTarget(9, 2)
    expect(demandView).toHaveBeenCalledTimes(1)
    demandView.mockResolvedValueOnce({ status: 'queued', focus_user_id: null })
    await store.retry(9)
    expect(demandView).toHaveBeenLastCalledWith(9, expect.objectContaining({ retry: true }))
  })

  it('a rejected retry clears data and propagates the denial without issuing a following read', async () => {
    fetchView.mockResolvedValueOnce(familySnapshot())
    await store.load(9)
    demandView.mockRejectedValueOnce(new ApiError(403, 'FORBIDDEN', '无法读取'))
    await expect(store.retry(9)).rejects.toMatchObject({ status: 403 })
    expect(fetchView).toHaveBeenCalledTimes(1)
    expect(store.forSpace(9)).toBeNull()
    expect(store.errorFor(9)).toMatchObject({ status: 403 })
  })

  it('a late focus acknowledgement cannot clear a newer read error', async () => {
    fetchView.mockResolvedValueOnce(familySnapshot())
    await store.load(9)
    const pending = deferred<PersonalFamilyViewDemandResult>()
    demandView.mockReturnValueOnce(pending.promise)
    const focus = store.focusTarget(9, 2)
    fetchView.mockResolvedValueOnce(familySnapshot({ progress: familyProgress({ generation: 8 }) }))
    await store.load(9)
    fetchView.mockRejectedValueOnce(new ApiError(0, 'NETWORK_ERROR', '连接中断'))
    await expect(store.load(9)).rejects.toMatchObject({ code: 'NETWORK_ERROR' })
    pending.resolve({ status: 'already_active', focus_user_id: 2 })
    await focus
    expect(store.errorFor(9)).toMatchObject({ code: 'NETWORK_ERROR' })
  })
})
