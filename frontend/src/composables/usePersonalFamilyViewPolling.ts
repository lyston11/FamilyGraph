import { computed, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'

import { ApiError } from '@/api/errors'
import { useAuthStore } from '@/stores/auth'
import { usePersonalFamilyViewStore } from '@/stores/personalFamilyView'
import type { PersonalFamilyViewData } from '@/types/api'

const KEEPALIVE_MS = 30_000
const MAX_FAILURES = 4
const FAST_READS = 4

/** Tree and profile share the same read/expiry protocol; the store coalesces concurrent readers. */
export function usePersonalFamilyViewPolling(
  spaceId: Readonly<Ref<number | null>>,
  enabled: Readonly<Ref<boolean>>,
  options: { autoLoad?: boolean } = {},
) {
  const pfv = usePersonalFamilyViewStore()
  const auth = useAuthStore()
  const data = computed(() => spaceId.value === null ? null : pfv.forSpace(spaceId.value))
  const state = ref<'idle' | 'updating' | 'retrying' | 'offline' | 'stopped' | 'delayed'>('idle')
  let mounted = false
  let generation = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  let controller: AbortController | null = null
  let failures = 0
  let fastReads = 0
  let lastProgress = ''
  let lastAdvancedAt = Date.now()

  const progressMessage = computed(() => {
    const progress = data.value?.progress
    if (!progress) return ''
    const count = `${progress.completed_count}/${progress.total_count} 位家人的称谓已整理`
    if (progress.phase === 'ready') return `称谓已整理完成（${progress.completed_count}/${progress.total_count}）`
    if (progress.phase === 'failed') return `部分称谓未整理成功（${count}），可以重试。`
    if (progress.phase === 'retrying') return `资料已更新，正在重新整理（${count}）。`
    if (progress.phase === 'queued') return '已排队，正在等待整理家谱。'
    if (progress.phase === 'preparing') return '正在准备家谱。'
    return count
  })

  const notice = computed(() => {
    if (state.value === 'offline') return '网络已断开，暂时保留有效期内的家谱；恢复连接后会重新验证。'
    if (state.value === 'retrying') return '连接暂时中断，正在重试；有效期结束后将隐藏家谱。'
    if (state.value === 'stopped') return '自动更新已暂停，请重新加载。'
    if (state.value === 'delayed') return '整理等待时间较长，已降低检查频率；可以重新加载。'
    return ''
  })

  function stopTimer(): void {
    if (timer !== null) clearTimeout(timer)
    timer = null
  }

  function stop(): void {
    generation += 1
    stopTimer()
    controller?.abort()
    controller = null
  }

  function canRead(): boolean {
    return mounted && enabled.value && auth.user !== null && spaceId.value !== null && !document.hidden
  }

  function schedule(): void {
    stopTimer()
    if (!canRead() || state.value === 'stopped') return
    if (!navigator.onLine) { state.value = 'offline'; return }
    const snapshot = data.value
    const progress = snapshot?.progress
    const phase = progress?.phase
    const pending = snapshot === null || (phase
      ? phase !== 'ready' && phase !== 'failed'
      : snapshot?.status === 'never_computed' || snapshot?.status === 'queued' || snapshot?.status === 'running')
    const signature = progress
      ? `${progress.generation}:${progress.revision}:${progress.phase}`
      : `${snapshot?.view_version}:${snapshot?.status}:${snapshot?.nodes.length}`
    if (signature !== lastProgress) {
      lastProgress = signature
      lastAdvancedAt = Date.now()
      if (state.value === 'delayed') state.value = 'idle'
    }
    let delay = KEEPALIVE_MS
    if (failures > 0) delay = Math.min(1000 * 2 ** (failures - 1), KEEPALIVE_MS)
    else if (pending) {
      if (Date.now() - lastAdvancedAt >= KEEPALIVE_MS) state.value = 'delayed'
      if (state.value !== 'delayed') {
        const fast = (phase === 'queued' || phase === 'preparing' || !snapshot?.nodes.length) && fastReads++ < FAST_READS
        delay = fast ? 250 : Math.max(1000, progress?.next_poll_ms ?? 1000)
      }
    }
    const remaining = pfv.remainingDisplayMs(spaceId.value!)
    if (remaining !== null) delay = Math.min(delay, Math.max(50, remaining - Math.min(1000, remaining / 5)))
    const scheduledGeneration = generation
    timer = setTimeout(() => {
      timer = null
      if (scheduledGeneration === generation) void refresh()
    }, Math.max(50, Math.min(delay, KEEPALIVE_MS)))
  }

  async function read(force: boolean, retry: boolean): Promise<PersonalFamilyViewData | null> {
    if (!canRead()) return null
    if (!navigator.onLine) { state.value = 'offline'; return null }
    stopTimer()
    controller?.abort()
    controller = new AbortController()
    const ownController = controller
    const readGeneration = ++generation
    const sid = spaceId.value!
    state.value = 'updating'
    try {
      const result = retry
        ? await pfv.retry(sid, ownController.signal)
        : await pfv.load(sid, { force, signal: ownController.signal })
      if (!mounted || generation !== readGeneration || ownController.signal.aborted) return null
      failures = 0
      state.value = 'idle'
      return result
    } catch (cause) {
      if (!mounted || generation !== readGeneration || ownController.signal.aborted) return null
      failures += 1
      const transient = cause instanceof ApiError &&
        (cause.code === 'NETWORK_ERROR' || cause.status >= 500 || cause.status === 429)
      state.value = !navigator.onLine ? 'offline' : transient && failures < MAX_FAILURES ? 'retrying' : 'stopped'
      return null
    } finally {
      if (mounted && generation === readGeneration && !ownController.signal.aborted) {
        controller = null
        schedule()
      }
    }
  }

  function refresh(force = false): Promise<PersonalFamilyViewData | null> {
    return read(force, false)
  }

  function retry(): Promise<PersonalFamilyViewData | null> {
    failures = 0
    fastReads = 0
    lastAdvancedAt = Date.now()
    return read(true, true)
  }

  function restart(): void {
    stop()
    failures = 0
    fastReads = 0
    lastProgress = ''
    lastAdvancedAt = Date.now()
    state.value = 'idle'
    if (options.autoLoad !== false) void refresh()
    else schedule()
  }

  function onVisibility(): void {
    if (document.hidden) stop()
    else { failures = 0; void refresh(true) }
  }
  function onOnline(): void { failures = 0; void refresh(true) }
  function onOffline(): void { stop(); state.value = 'offline' }

  watch([spaceId, enabled, () => auth.user?.id ?? null], restart, { flush: 'sync' })
  watch(data, () => { if (controller === null && mounted) schedule() })
  onMounted(() => {
    mounted = true
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('online', onOnline)
    window.addEventListener('offline', onOffline)
    restart()
  })
  onBeforeUnmount(() => {
    mounted = false
    stop()
    document.removeEventListener('visibilitychange', onVisibility)
    window.removeEventListener('online', onOnline)
    window.removeEventListener('offline', onOffline)
  })

  return { data, state, progressMessage, notice, refresh, retry }
}
