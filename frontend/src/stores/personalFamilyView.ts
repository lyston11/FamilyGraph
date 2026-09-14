import { onScopeDispose, ref, watch } from 'vue'
import { defineStore } from 'pinia'

import { demandPersonalFamilyView, fetchPersonalFamilyView } from '@/api/personalFamilyView'
import { ApiError } from '@/api/errors'
import { useAuthStore } from '@/stores/auth'
import type {
  PersonalFamilyViewData,
  PersonalFamilyViewEdge,
  PersonalFamilyViewNode,
  PersonalFamilyViewSnapshot,
  PersonalFamilyViewTarget,
} from '@/types/api'

interface Position { x: number; y: number }
interface Viewport extends Position { zoom: number }
interface ReadRequest {
  sequence: number
  controller: AbortController
  promise: Promise<PersonalFamilyViewData | null>
}

/** Authenticated cumulative snapshots. Only a current request may replace data or renew its lease. */
export const usePersonalFamilyViewStore = defineStore('personalFamilyView', () => {
  const auth = useAuthStore()
  const bySpace = ref<Map<number, PersonalFamilyViewSnapshot>>(new Map())
  const loadingSpaceIds = ref<Set<number>>(new Set())
  const errorBySpace = ref<Map<number, unknown>>(new Map())
  const positionsBySpace = ref(new Map<number, Map<number, Position>>())
  const viewports = ref(new Map<number, Viewport>())
  const viewModes = ref(new Map<number, 'tree' | 'canvas'>())
  // Keep only ordering metadata after expiration, so a late body cannot resurrect an older generation.
  const versions = new Map<number, { generation: number | null; revision: number; viewVersion: number }>()
  const requests = new Map<number, ReadRequest>()
  const sequences = new Map<number, number>()
  const expiryTimers = new Map<number, ReturnType<typeof setTimeout>>()
  const expiryClocks = new Map<number, { wall: number; monotonic: number }>()
  const demands = new Map<string, { spaceId: number; controller: AbortController; promise: Promise<void> }>()
  const focused = new Set<string>()
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

  function stopExpiry(spaceId: number): void {
    const timer = expiryTimers.get(spaceId)
    if (timer !== undefined) clearTimeout(timer)
    expiryTimers.delete(spaceId)
  }

  function discardSnapshot(spaceId: number): void {
    stopExpiry(spaceId)
    expiryClocks.delete(spaceId)
    const next = new Map(bySpace.value)
    next.delete(spaceId)
    bySpace.value = next
  }

  function expire(spaceId: number): void {
    discardSnapshot(spaceId)
    setError(spaceId, new ApiError(0, 'PFV_DISPLAY_EXPIRED', '家谱内容需要重新验证，请重新加载'))
  }

  function armExpiry(spaceId: number): void {
    stopExpiry(spaceId)
    const deadline = bySpace.value.get(spaceId)?.displayExpiresAt
    if (deadline === null || deadline === undefined) return
    if (expiryClocks.get(spaceId)?.wall !== deadline) {
      expiryClocks.set(spaceId, { wall: deadline, monotonic: performance.now() + Math.max(0, deadline - Date.now()) })
    }
    const remaining = remainingDisplayMs(spaceId) ?? 0
    if (remaining <= 0) { expire(spaceId); return }
    expiryTimers.set(spaceId, setTimeout(() => {
      expiryTimers.delete(spaceId)
      if ((remainingDisplayMs(spaceId) ?? 1) <= 0) expire(spaceId)
      else armExpiry(spaceId)
    }, Math.min(remaining, 2_147_483_647)))
  }

  function forSpace(spaceId: number): PersonalFamilyViewData | null {
    const snapshot = bySpace.value.get(spaceId)
    if ((remainingDisplayMs(spaceId) ?? 1) <= 0) return null
    return snapshot?.data ?? null
  }

  function remainingDisplayMs(spaceId: number): number | null {
    const deadline = bySpace.value.get(spaceId)?.displayExpiresAt
    if (deadline === undefined || deadline === null) return null
    const clock = expiryClocks.get(spaceId)
    // A backwards wall-clock adjustment must not extend an offline display authorization.
    return Math.max(0, Math.min(deadline - Date.now(), clock?.wall === deadline ? clock.monotonic - performance.now() : Infinity))
  }

  function cancelRead(spaceId: number): void {
    sequences.set(spaceId, (sequences.get(spaceId) ?? 0) + 1)
    requests.get(spaceId)?.controller.abort()
    requests.delete(spaceId)
    setLoading(spaceId, false)
  }

  function clearSpace(spaceId: number): void {
    cancelRead(spaceId)
    discardSnapshot(spaceId)
    setError(spaceId, null)
    positionsBySpace.value.delete(spaceId)
    viewports.value.delete(spaceId)
    viewModes.value.delete(spaceId)
    versions.delete(spaceId)
    for (const [key, demand] of demands) {
      if (demand.spaceId === spaceId) { demand.controller.abort(); demands.delete(key) }
    }
    for (const key of focused) if (key.startsWith(spaceId + ':')) focused.delete(key)
  }

  function clear(): void {
    epoch += 1
    for (const request of requests.values()) request.controller.abort()
    requests.clear()
    for (const timer of expiryTimers.values()) clearTimeout(timer)
    expiryTimers.clear()
    expiryClocks.clear()
    for (const demand of demands.values()) demand.controller.abort()
    demands.clear()
    focused.clear()
    bySpace.value = new Map()
    loadingSpaceIds.value = new Set()
    errorBySpace.value = new Map()
    positionsBySpace.value = new Map()
    viewports.value = new Map()
    viewModes.value = new Map()
    versions.clear()
  }

  watch(() => auth.user?.id ?? null, clear, { flush: 'sync' })
  onScopeDispose(clear)

  function acceptError(spaceId: number, cause: unknown): void {
    if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) clearSpace(spaceId)
    else if (!(cause instanceof ApiError && (cause.code === 'NETWORK_ERROR' || cause.status >= 500 || cause.status === 429)) ||
             (remainingDisplayMs(spaceId) ?? 0) <= 0) discardSnapshot(spaceId)
    setError(spaceId, cause)
  }

  /** Duplicate readers share one request. A forced refresh cancels and supersedes that request. */
  function load(
    spaceId: number,
    options: { force?: boolean; progressive?: boolean; signal?: AbortSignal } = {},
  ): Promise<PersonalFamilyViewData | null> {
    if (options.signal?.aborted) return Promise.resolve(null)
    const active = requests.get(spaceId)
    if (active && !options.force) return active.promise
    if (active) cancelRead(spaceId)
    if ((remainingDisplayMs(spaceId) ?? 1) <= 0) expire(spaceId)
    const requestEpoch = epoch
    const identity = auth.user?.id ?? null
    const sequence = (sequences.get(spaceId) ?? 0) + 1
    sequences.set(spaceId, sequence)
    const cached = bySpace.value.get(spaceId) ?? null
    const sentEtag = options.force ? null : cached?.etag ?? null
    const controller = new AbortController()
    const abort = () => {
      controller.abort()
      if (requests.get(spaceId)?.sequence === sequence) cancelRead(spaceId)
    }
    options.signal?.addEventListener('abort', abort, { once: true })
    const isCurrent = () => requestEpoch === epoch && identity === (auth.user?.id ?? null) &&
      sequence === sequences.get(spaceId) && !controller.signal.aborted
    setLoading(spaceId, true)
    const promise = (async () => {
      try {
        const response = await fetchPersonalFamilyView(spaceId, sentEtag, {
          progressive: options.progressive ?? true, signal: controller.signal,
        })
        if (!isCurrent()) return null
        if (response === null) return forSpace(spaceId)
        if ('notModified' in response) {
          if (cached === null || bySpace.value.get(spaceId) !== cached || sentEtag === null ||
              response.etag !== sentEtag) return forSpace(spaceId)
          if (response.displayExpiresAt !== undefined && response.displayExpiresAt !== null &&
              response.displayExpiresAt > Date.now()) {
            cached.displayUntil = response.displayUntil
            cached.serverDate = response.serverDate
            cached.displayExpiresAt = response.displayExpiresAt
            setError(spaceId, null)
            armExpiry(spaceId)
          }
          return forSpace(spaceId)
        }
        if (response.data.space_id !== spaceId) throw new ApiError(0, 'PFV_PROTOCOL_INVALID', '家谱响应与当前空间不一致')
        const previous = versions.get(spaceId)
        const incoming = response.data.progress
        if (previous?.generation !== null && previous?.generation !== undefined && incoming && (incoming.generation < previous.generation ||
            (incoming.generation === previous.generation && incoming.revision < previous.revision))) return forSpace(spaceId)
        if (!incoming && previous && (previous.generation !== null || response.data.view_version < previous.viewVersion)) return forSpace(spaceId)
        if (incoming && (!response.etag || (response.displayExpiresAt ?? 0) <= Date.now())) {
          throw new ApiError(0, 'PFV_DISPLAY_EXPIRED', '家谱响应缺少有效的展示期限，请重新加载')
        }
        // A new generation replaces the complete result set, never concatenating older rows.
        versions.set(spaceId, { generation: incoming?.generation ?? null,
          revision: incoming?.revision ?? 0, viewVersion: response.data.view_version })
        bySpace.value = new Map(bySpace.value).set(spaceId, response)
        // Empty preparation/invalidation bodies hide content, but do not describe the next topology.
        // Keep only coordinates until an authorized skeleton (or an authoritative empty view) arrives.
        if (response.data.nodes.length > 0 || (incoming ? incoming.phase === 'ready' : response.data.status === 'current')) {
          const visible = new Set(response.data.nodes.map((node) => node.user_id))
          const positions = positionsBySpace.value.get(spaceId)
          if (positions) for (const id of positions.keys()) if (!visible.has(id)) positions.delete(id)
        }
        setError(spaceId, null)
        armExpiry(spaceId)
        return forSpace(spaceId)
      } catch (cause) {
        const cancelled = controller.signal.aborted
        if (isCurrent()) acceptError(spaceId, cause)
        if (cancelled) return null
        throw cause
      } finally {
        options.signal?.removeEventListener('abort', abort)
        if (requests.get(spaceId)?.sequence === sequence) {
          requests.delete(spaceId)
          setLoading(spaceId, false)
        }
      }
    })()
    requests.set(spaceId, { sequence, controller, promise })
    return promise
  }

  function refresh(spaceId: number): Promise<PersonalFamilyViewData | null> {
    return load(spaceId, { force: true })
  }

  function getVisiblePerson(spaceId: number, userId: number): PersonalFamilyViewNode | null {
    return forSpace(spaceId)?.nodes.find((node) => node.user_id === userId) ?? null
  }

  function getRelationshipDetail(spaceId: number, userId: number): PersonalFamilyViewEdge | null {
    return forSpace(spaceId)?.edges.find((edge) => edge.from_user_id === userId || edge.to_user_id === userId) ?? null
  }

  function targetFor(spaceId: number, userId: number): PersonalFamilyViewTarget | null {
    return forSpace(spaceId)?.progress?.targets.find((target) => target.user_id === userId) ?? null
  }

  function demand(spaceId: number, options: { focusUserId?: number; retry?: boolean }): Promise<void> {
    const data = forSpace(spaceId)
    if (options.focusUserId !== undefined && targetFor(spaceId, options.focusUserId)?.status !== 'pending') return Promise.resolve()
    const key = [spaceId, data?.progress?.generation ?? 0, options.focusUserId ?? 'retry'].join(':')
    const active = demands.get(key)
    if (active) return active.promise
    if (options.focusUserId !== undefined && focused.has(key)) return Promise.resolve()
    const requestEpoch = epoch
    const identity = auth.user?.id ?? null
    const requestGeneration = data?.progress?.generation ?? null
    const controller = new AbortController()
    const isCurrentDemand = () => requestEpoch === epoch && identity === (auth.user?.id ?? null) &&
      !controller.signal.aborted && requestGeneration === (forSpace(spaceId)?.progress?.generation ?? null)
    const promise = demandPersonalFamilyView(spaceId, { ...options, signal: controller.signal })
      .then(() => {
        if (isCurrentDemand() && options.focusUserId !== undefined) focused.add(key)
      }).catch((cause: unknown) => {
        const cancelled = controller.signal.aborted
        if (isCurrentDemand()) acceptError(spaceId, cause)
        if (!cancelled) throw cause
      }).finally(() => { if (demands.get(key)?.controller === controller) demands.delete(key) })
    demands.set(key, { spaceId, controller, promise })
    return promise
  }

  function focusTarget(spaceId: number, userId: number): Promise<void> {
    return demand(spaceId, { focusUserId: userId })
  }

  async function retry(spaceId: number, signal?: AbortSignal): Promise<PersonalFamilyViewData | null> {
    if (signal?.aborted) return null
    const data = forSpace(spaceId)
    if (!data || data.progress) await demand(spaceId, { retry: true })
    if (signal?.aborted) return null
    return load(spaceId, { force: true, signal })
  }

  function rememberPositions(spaceId: number, entries: Iterable<[number, Position]>): void {
    const positions = new Map(positionsBySpace.value.get(spaceId))
    for (const [id, position] of entries) if (getVisiblePerson(spaceId, id)) positions.set(id, { ...position })
    positionsBySpace.value = new Map(positionsBySpace.value).set(spaceId, positions)
  }

  function rememberViewport(spaceId: number, viewport: Viewport): void {
    viewports.value = new Map(viewports.value).set(spaceId, { ...viewport })
  }

  function rememberViewMode(spaceId: number, mode: 'tree' | 'canvas'): void {
    viewModes.value = new Map(viewModes.value).set(spaceId, mode)
  }

  return {
    bySpace, loadingSpaceIds, errorBySpace, positionsBySpace, viewports, viewModes,
    isLoading: (spaceId: number) => loadingSpaceIds.value.has(spaceId),
    errorFor: (spaceId: number) => errorBySpace.value.get(spaceId) ?? null,
    forSpace, remainingDisplayMs, load, refresh, clearSpace, clear,
    reloadAfterBridgeChange: refresh,
    getVisiblePerson, getRelationshipDetail, targetFor, focusTarget, retry,
    rememberPositions, rememberViewport, rememberViewMode,
  }
})
