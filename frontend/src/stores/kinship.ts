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
    myTermsLoading.value = true
    try {
      myTerms.value = await fetchMyTerms(spaceId)
      myTermsSpaceId.value = spaceId
      available.value = true
      return myTerms.value
    } catch (error) {
      if (markIfDisabled(error)) return null
      throw error
    } finally {
      myTermsLoading.value = false
    }
  }

  /**
   * 个人称谓纠正：只写 personal term。成功后失效该空间的 resolve 缓存
   * （概念→目标对未知，整空间重算最保守），本地词条列表同步替换。
   */
  async function correctTerm(
    spaceId: number,
    conceptCode: string,
    term: string,
  ): Promise<MyTerm | null> {
    try {
      const saved = await updateMyTerm({ spaceId, conceptCode, term })
      if (myTermsSpaceId.value === spaceId) {
        const index = myTerms.value.findIndex((t) => t.concept_code === conceptCode)
        if (index >= 0) myTerms.value.splice(index, 1, saved)
        else myTerms.value.push(saved)
      }
      dropResolvesOfSpace(spaceId)
      available.value = true
      return saved
    } catch (error) {
      if (markIfDisabled(error)) return null
      throw error
    }
  }

  // ---- resolve ----

  function cachedResolve(spaceId: number, viewerId: number, targetId: number): KinshipResolve | null {
    return resolveCache.value.get(resolveKey(spaceId, viewerId, targetId)) ?? null
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
    try {
      const result = await resolveKinship(spaceId, viewerId, targetId)
      resolveCache.value.set(resolveKey(spaceId, viewerId, targetId), result)
      available.value = true
      return result
    } catch (error) {
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
    try {
      const result = await recordTermUsage({
        spaceId,
        conceptCode,
        term,
        sourceEvent: 'manual_select',
      })
      available.value = true
      return result
    } catch (error) {
      if (markIfDisabled(error)) return null
      throw error
    }
  }

  // ---- 自由文本解析 ----

  async function parseText(spaceId: number, text: string): Promise<ParseResult | null> {
    parseLoading.value = true
    parseError.value = null
    try {
      const result = await parseRelationText(spaceId, text)
      parseResult.value = result
      parseSpaceId.value = spaceId
      available.value = true
      return result
    } catch (error) {
      if (markIfDisabled(error)) return null
      parseError.value =
        error instanceof ApiError && error.message ? error.message : '解析失败，请稍后重试'
      return null
    } finally {
      parseLoading.value = false
    }
  }

  // ---- 失效边界 ----

  /** 空间切换：清该空间缓存与临时解析态（对齐 state-management.md） */
  function resetForSpace(spaceId: number): void {
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

  /** 登出 / 账号切换 / 撤权（auth.clearSession 调用）：全量清理 */
  function clear(): void {
    available.value = null
    myTerms.value = []
    myTermsSpaceId.value = null
    myTermsLoading.value = false
    resolveCache.value.clear()
    parseResult.value = null
    parseLoading.value = false
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
    resolvePair,
    submitUsage,
    parseText,
    resetForSpace,
    clear,
  }
})
