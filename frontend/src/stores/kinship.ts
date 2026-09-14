import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import {
  KINSHIP_FLAG_DISABLED,
  fetchMyTerms,
  parseRelationText,
  recordTermUsage,
  resolveKinship,
  updateMyTerm,
} from '@/api/kinship'
import { ApiError } from '@/api/errors'
import { usePersonalFamilyViewStore } from '@/stores/personalFamilyView'
import { useSpacesStore } from '@/stores/spaces'
import type { KinshipResolve, MyTerm, ParseResult, UsageCreated } from '@/types/kinship'

/**
 * Kinship 称谓状态（V2.3 Block E4c，spec/frontend/state-management.md）。
 *
 * - 服务端数据唯一来源是 store；resolve 按「space:viewer:target」缓存，
 *   个人纠正/空间切换后立即失效重算（KI-5：不得用旧称谓继续回答）；
 * - feature flag 关闭（503 KINSHIP_FLAG_DISABLED）时 available=false，
 *   所有入口组件据此隐藏；available=null 表示尚未探测；
 * - 登出 / token 失效经 auth.clearSession() → clear() 全量清空；
 * - 不做乐观更新。
 */

function resolveKey(spaceId: number, viewerId: number, targetId: number): string {
  return `${spaceId}:${viewerId}:${targetId}`
}

export const useKinshipStore = defineStore('kinship', () => {
  let sessionEpoch = 0
  let termsEpoch = 0
  const spaceEpochs = new Map<number, number>()
  const resolveRequests = new Map<string, number>()
  let nextRequestId = 0
  let myTermsRequestId = 0
  let parseRequestId = 0
  let myTermsRequestSpaceId: number | null = null
  let parseRequestSpaceId: number | null = null

  function versionOf(spaceId: number): string {
    return `${sessionEpoch}:${termsEpoch}:${spaceEpochs.get(spaceId) ?? 0}`
  }
  /** null=未探测；false=flag 关闭（UI 全部隐藏）；true=可用 */
  const available = ref<boolean | null>(null)

  // ---- 个人词条 ----
  const myTerms = ref<MyTerm[]>([])
  const myTermsSpaceId = ref<number | null>(null)
  const myTermsLoading = ref(false)

  // ---- resolve 缓存：key = space:viewer:target ----
  const resolveCache = ref<Map<string, KinshipResolve>>(new Map())

  // ---- 自由文本解析状态 ----
  const parseResult = ref<ParseResult | null>(null)
  const parseLoading = ref(false)
  const parseError = ref<string | null>(null)
  const parseSpaceId = ref<number | null>(null)

  const isDisabled = computed(() => available.value === false)

  /** flag 关闭识别：仅 503 + KINSHIP_FLAG_DISABLED 视为能力关闭并隐藏入口 */
  function markIfDisabled(error: unknown): boolean {
    if (error instanceof ApiError && error.status === 503 && error.code === KINSHIP_FLAG_DISABLED) {
      available.value = false
      return true
    }
    return false
  }

  // ---- 个人词条 ----

  async function loadMyTerms(spaceId: number): Promise<MyTerm[] | null> {
    const requestVersion = versionOf(spaceId)
    const requestId = ++myTermsRequestId
    const isCurrent = () => requestVersion === versionOf(spaceId) && requestId === myTermsRequestId
    myTermsRequestSpaceId = spaceId
    myTermsLoading.value = true
    try {
      const terms = await fetchMyTerms(spaceId)
      if (!isCurrent()) return null
      myTerms.value = terms
      myTermsSpaceId.value = spaceId
      available.value = true
      return myTerms.value
    } catch (error) {
      if (!isCurrent()) return null
      if (markIfDisabled(error)) return null
      throw error
    } finally {
      if (isCurrent()) {
        myTermsLoading.value = false
        myTermsRequestSpaceId = null
      }
    }
  }

  /**
   * 个人称谓纠正：只写 personal term；本人同概念跨空间生效，成功后
   * 统一失效所有称谓/PFV 缓存，并重新加载当前授权视图。
   */
  async function correctTerm(
    spaceId: number,
    conceptCode: string,
    term: string,
  ): Promise<MyTerm | null> {
    const requestEpoch = sessionEpoch
    try {
      const saved = await updateMyTerm({ spaceId, conceptCode, term })
      if (requestEpoch !== sessionEpoch) return null
      await refreshAfterTermChange(spaceId, 'personal')
      if (requestEpoch !== sessionEpoch) return null
      available.value = true
      return saved
    } catch (error) {
      if (requestEpoch !== sessionEpoch) return null
      if (markIfDisabled(error)) return null
      throw error
    }
  }

  // ---- resolve ----

  function cachedResolve(spaceId: number, viewerId: number, targetId: number): KinshipResolve | null {
    return resolveCache.value.get(resolveKey(spaceId, viewerId, targetId)) ?? null
  }

  /** A new authorized view invalidates both cached evidence and earlier requests for this pair. */
  function invalidateResolve(spaceId: number, viewerId: number, targetId: number): void {
    const key = resolveKey(spaceId, viewerId, targetId)
    resolveRequests.delete(key)
    resolveCache.value.delete(key)
  }

  async function resolvePair(
    spaceId: number,
    viewerId: number,
    targetId: number,
    options: { force?: boolean } = {},
  ): Promise<KinshipResolve | null> {
    if (!options.force) {
      const hit = cachedResolve(spaceId, viewerId, targetId)
      if (hit) return hit
    }
    const requestVersion = versionOf(spaceId)
    const key = resolveKey(spaceId, viewerId, targetId)
    const requestId = ++nextRequestId
    resolveRequests.set(key, requestId)
    const isCurrent = () =>
      requestVersion === versionOf(spaceId) && resolveRequests.get(key) === requestId
    try {
      const result = await resolveKinship(spaceId, viewerId, targetId)
      if (!isCurrent()) return null
      resolveCache.value.set(key, result)
      available.value = true
      return result
    } catch (error) {
      if (!isCurrent()) return null
      if (markIfDisabled(error)) return null
      throw error
    }
  }

  function dropResolvesOfSpace(spaceId: number): void {
    for (const key of [...resolveCache.value.keys()]) {
      if (key.startsWith(`${spaceId}:`)) resolveCache.value.delete(key)
    }
  }

  // ---- 使用证据（我就这么叫）----

  async function submitUsage(
    spaceId: number,
    conceptCode: string,
    term: string,
  ): Promise<UsageCreated | null> {
    const requestEpoch = sessionEpoch
    try {
      const result = await recordTermUsage({
        spaceId,
        conceptCode,
        term,
        sourceEvent: 'manual_select',
      })
      if (requestEpoch !== sessionEpoch) return null
      if (result.promotion.promoted || result.promotion.demoted) {
        await refreshAfterTermChange(spaceId, 'space')
        if (requestEpoch !== sessionEpoch) return null
      }
      available.value = true
      return result
    } catch (error) {
      if (requestEpoch !== sessionEpoch) return null
      if (markIfDisabled(error)) return null
      throw error
    }
  }

  // ---- 自由文本解析 ----

  async function parseText(spaceId: number, text: string): Promise<ParseResult | null> {
    const requestVersion = versionOf(spaceId)
    const requestId = ++parseRequestId
    const isCurrent = () => requestVersion === versionOf(spaceId) && requestId === parseRequestId
    parseRequestSpaceId = spaceId
    parseLoading.value = true
    parseError.value = null
    try {
      const result = await parseRelationText(spaceId, text)
      if (!isCurrent()) return null
      parseResult.value = result
      parseSpaceId.value = spaceId
      available.value = true
      return result
    } catch (error) {
      if (!isCurrent()) return null
      if (markIfDisabled(error)) return null
      parseError.value =
        error instanceof ApiError && error.message ? error.message : '解析失败，请稍后重试'
      return null
    } finally {
      if (isCurrent()) {
        parseLoading.value = false
        parseRequestSpaceId = null
      }
    }
  }

  // ---- 失效边界 ----

  /** 空间切换：清该空间缓存与临时解析态（对齐 state-management.md） */
  function resetForSpace(spaceId: number): void {
    spaceEpochs.set(spaceId, (spaceEpochs.get(spaceId) ?? 0) + 1)
    if (myTermsRequestSpaceId === spaceId) {
      myTermsLoading.value = false
      myTermsRequestSpaceId = null
    }
    if (parseRequestSpaceId === spaceId) {
      parseLoading.value = false
      parseRequestSpaceId = null
    }
    dropResolvesOfSpace(spaceId)
    if (myTermsSpaceId.value === spaceId) {
      myTerms.value = []
      myTermsSpaceId.value = null
    }
    if (parseSpaceId.value === spaceId) {
      parseResult.value = null
      parseError.value = null
      parseSpaceId.value = null
    }
  }

  /** 个人偏好影响本人所有空间；自动称谓恢复/空间晋升仅影响对应空间。 */
  async function refreshAfterTermChange(
    spaceId: number,
    scope: 'personal' | 'space',
  ): Promise<void> {
    const pfv = usePersonalFamilyViewStore()
    if (scope === 'personal') {
      termsEpoch += 1
      resolveCache.value.clear()
      resolveRequests.clear()
      myTerms.value = []
      myTermsSpaceId.value = null
      myTermsLoading.value = false
      myTermsRequestSpaceId = null
      parseResult.value = null
      parseError.value = null
      parseSpaceId.value = null
      parseLoading.value = false
      parseRequestSpaceId = null
      pfv.invalidateTermInputs()
    } else {
      resetForSpace(spaceId)
      pfv.invalidateTermInputs(spaceId)
    }
    const currentSpaceId = useSpacesStore().currentSpaceId
    if (currentSpaceId !== null && (scope === 'personal' || currentSpaceId === spaceId)) {
      await pfv.refresh(currentSpaceId).catch(() => undefined)
    }
  }

  /** 登出 / 账号切换 / 撤权（auth.clearSession 调用）：全量清理 */
  function clear(): void {
    sessionEpoch += 1
    spaceEpochs.clear()
    resolveRequests.clear()
    available.value = null
    myTerms.value = []
    myTermsSpaceId.value = null
    myTermsLoading.value = false
    myTermsRequestSpaceId = null
    resolveCache.value.clear()
    parseResult.value = null
    parseLoading.value = false
    parseRequestSpaceId = null
    parseError.value = null
    parseSpaceId.value = null
  }

  return {
    available,
    isDisabled,
    myTerms,
    myTermsSpaceId,
    myTermsLoading,
    resolveCache,
    parseResult,
    parseLoading,
    parseError,
    parseSpaceId,
    loadMyTerms,
    correctTerm,
    cachedResolve,
    invalidateResolve,
    resolvePair,
    submitUsage,
    parseText,
    resetForSpace,
    refreshAfterTermChange,
    clear,
  }
})
