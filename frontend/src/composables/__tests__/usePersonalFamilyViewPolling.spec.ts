import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'

import { deferred, familyProgress, familySnapshot } from '@/__tests__/personalFamilyViewFixtures'
import { ApiError } from '@/api/errors'
import { fetchPersonalFamilyView } from '@/api/personalFamilyView'
import { usePersonalFamilyViewPolling } from '@/composables/usePersonalFamilyViewPolling'
import { useAuthStore } from '@/stores/auth'
import { usePersonalFamilyViewStore } from '@/stores/personalFamilyView'
import type { PersonalFamilyViewResponse } from '@/types/api'

vi.mock('@/api/personalFamilyView', () => ({ fetchPersonalFamilyView: vi.fn(), demandPersonalFamilyView: vi.fn() }))
const fetchView = vi.mocked(fetchPersonalFamilyView)

describe('personal family view polling lifecycle', () => {
  let wrapper: VueWrapper | undefined
  let live: ReturnType<typeof usePersonalFamilyViewPolling>
  let store: ReturnType<typeof usePersonalFamilyViewStore>
  let hidden: MockInstance<() => boolean>
  let online: MockInstance<() => boolean>

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-14T00:00:00Z'))
    vi.resetAllMocks()
    hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
    online = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true)
    setActivePinia(createPinia())
    useAuthStore().user = { id: 1, name: '本人', pin_must_change: false, claim_status: 'claimed', profile_status: 'identity_confirmed' }
    store = usePersonalFamilyViewStore()
  })
  afterEach(() => {
    wrapper?.unmount(); wrapper = undefined
    store.clear(); store.$dispose()
    vi.restoreAllMocks(); vi.useRealTimers()
  })

  async function open() {
    wrapper = mount(defineComponent({ setup() {
      live = usePersonalFamilyViewPolling(ref(9), ref(true))
      return () => h('div', live.progressMessage.value)
    } }))
    await flushPromises()
  }

  it.each(['ready', 'failed'] as const)('running + %s with next_poll_ms=0 uses low frequency, never a zero-delay loop', async (phase) => {
    fetchView.mockImplementation(async () => familySnapshot({ status: 'running', progress: familyProgress({ phase, next_poll_ms: 0,
      targets: [{ user_id: 2, status: phase === 'ready' ? 'ready' : 'failed', reason_code: null }] }) }))
    await open()
    await vi.advanceTimersByTimeAsync(29_999)
    expect(fetchView).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(fetchView).toHaveBeenCalledTimes(2)
    if (phase === 'ready') expect(live.progressMessage.value).toContain('已整理完成')
  })

  it('continues retrying-phase progress even when the old top-level status is stale', async () => {
    fetchView.mockImplementation(async () => familySnapshot({ status: 'stale', progress: familyProgress({ phase: 'retrying' }) }))
    await open()
    await vi.advanceTimersByTimeAsync(1000)
    expect(fetchView).toHaveBeenCalledTimes(2)
  })

  it('bounds fast first-skeleton reads and exposes a delayed state for a stalled worker', async () => {
    fetchView.mockImplementation(async () => familySnapshot({ nodes: [], topology_edges: [],
      progress: familyProgress({ phase: 'preparing', targets: [], next_poll_ms: 250 }) }))
    await open()
    await vi.advanceTimersByTimeAsync(31_000)
    expect(fetchView.mock.calls.length).toBeLessThan(40)
    expect(live.state.value).toBe('delayed')
    expect(live.notice.value).toContain('等待时间较长')
  })

  it('stops retrying after bounded network failures and resumes on an online event', async () => {
    fetchView.mockRejectedValue(new ApiError(0, 'NETWORK_ERROR', '连接中断'))
    await open()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetchView).toHaveBeenCalledTimes(4)
    expect(live.state.value).toBe('stopped')
    fetchView.mockResolvedValue(familySnapshot())
    window.dispatchEvent(new Event('online'))
    await flushPromises()
    expect(fetchView).toHaveBeenCalledTimes(5)
    expect(store.forSpace(9)?.nodes).toHaveLength(2)
  })

  it('pauses hidden/offline reads while expiry continues, then immediately reauthorizes in foreground', async () => {
    fetchView.mockResolvedValueOnce(familySnapshot({}, { displayExpiresAt: Date.now() + 100 }))
    await open()
    hidden.mockReturnValue(true)
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(101)
    expect(fetchView).toHaveBeenCalledTimes(1)
    expect(store.bySpace.has(9)).toBe(false)
    online.mockReturnValue(false)
    hidden.mockReturnValue(false)
    document.dispatchEvent(new Event('visibilitychange'))
    expect(live.state.value).toBe('offline')
    fetchView.mockResolvedValue(familySnapshot())
    online.mockReturnValue(true)
    window.dispatchEvent(new Event('online'))
    await flushPromises()
    expect(fetchView).toHaveBeenCalledTimes(2)
    expect(fetchView.mock.calls[1]?.[1]).toBeNull()
  })

  it('never rearms polling after an unmounted in-flight request completes', async () => {
    const response = deferred<PersonalFamilyViewResponse>()
    fetchView.mockReturnValue(response.promise)
    await open()
    wrapper?.unmount(); wrapper = undefined
    response.resolve(familySnapshot())
    await flushPromises()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetchView).toHaveBeenCalledTimes(1)
    expect(store.bySpace.size).toBe(0)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('revalidates before a short display deadline even after viewer-ready', async () => {
    fetchView.mockResolvedValueOnce(familySnapshot({ progress: familyProgress({ phase: 'ready', next_poll_ms: 0,
      targets: [{ user_id: 2, status: 'ready', reason_code: null }] }) }, { displayExpiresAt: Date.now() + 500 }))
    fetchView.mockImplementation(async () => ({ notModified: true, etag: '"7:1"', displayExpiresAt: Date.now() + 60_000 }))
    await open()
    await vi.advanceTimersByTimeAsync(401)
    expect(fetchView).toHaveBeenCalledTimes(2)
    expect(store.forSpace(9)).not.toBeNull()
    expect(store.remainingDisplayMs(9)).toBeGreaterThan(59_000)
  })
})
