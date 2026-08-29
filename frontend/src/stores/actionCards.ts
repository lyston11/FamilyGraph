import { defineStore } from 'pinia'
import { ref } from 'vue'

import {
  ACTION_CARD_ERRORS,
  acceptActionCard,
  dismissActionCard,
  executeActionCard,
  fetchActionCards,
  viewActionCard,
} from '@/api/actionCards'
import { ApiError } from '@/api/errors'
import type { ActionCard, ActionCardState } from '@/types/actionCard'

/**
 * ActionCard 状态（V2.4 Block S3，spec/frontend/state-management.md）。
 *
 * - 状态真源在服务端：列表按 space 分区加载，不做乐观更新；
 * - view/dismiss/accept 以响应回填本地，并触发同空间列表刷新
 *   （supersede/dedupe 可能影响同空间其他卡片）；
 * - execute 可重试失败（409 CARD_EXECUTE_REJECTED）服务端保持 accepted，
 *   本地不改状态、仅刷新对齐；410 过期时本地对齐为终态并刷新；
 * - 入口降级：403 SPACE_FORBIDDEN_ACTOR 或 503（flag 关闭）→ hidden=true，
 *   Inbox 入口据此隐藏；
 * - 切换空间 resetForSpace 清空该空间分区（state-management.md 失效边界）；
 *   登出 / 账号切换 / 撤权经 auth.clearSession() → clear() 全量清理。
 */

interface CardPartition {
  cards: ActionCard[]
  loaded: boolean
  loading: boolean
  /** 入口降级标记：403/503 后为 true，UI 隐藏「管家建议」入口 */
  hidden: boolean
  error: { code: string; message: string } | null
}

function emptyPartition(): CardPartition {
  return { cards: [], loaded: false, loading: false, hidden: false, error: null }
}

export const useActionCardsStore = defineStore('actionCards', () => {
  // ---- 状态：按 space_id 分区 ----
  const partitions = ref<Map<number, CardPartition>>(new Map())

  function partitionOf(spaceId: number): CardPartition | null {
    return partitions.value.get(spaceId) ?? null
  }

  function requirePartition(spaceId: number): CardPartition {
    const existing = partitions.value.get(spaceId)
    if (existing) return existing
    partitions.value.set(spaceId, emptyPartition())
    // 必须从 Map 重读（拿到响应式代理对象）：直接持有原始对象跨 await 赋值
    // 不会触发订阅者（Vue 响应式只拦截代理上的操作）
    return partitions.value.get(spaceId) as CardPartition
  }

  function cardsOf(spaceId: number): ActionCard[] {
    return partitions.value.get(spaceId)?.cards ?? []
  }

  /** Inbox badge：仅统计 pending 新建议 */
  function pendingCountOf(spaceId: number): number {
    return cardsOf(spaceId).filter((c) => c.state === 'pending').length
  }

  function isEntryHiddenError(error: ApiError): boolean {
    return (
      (error.status === 403 && error.code === ACTION_CARD_ERRORS.SPACE_FORBIDDEN_ACTOR) ||
      error.status === 503
    )
  }

  // ---- 列表 ----

  /** 会话代际：clear / resetForSpace 递增，迟到响应不得回写新分区状态（P2 隔离） */
  let storeGeneration = 0

  async function loadForSpace(spaceId: number): Promise<void> {
    const generation = storeGeneration
    const partition = requirePartition(spaceId)
    if (partition.hidden) return
    partition.loading = true
    partition.error = null
    try {
      const cards = await fetchActionCards(spaceId)
      // 响应期间分区被清/重置（clear、resetForSpace）或分区对象已被替换则丢弃
      if (generation !== storeGeneration || partitionOf(spaceId) !== partition) return
      partition.cards = cards
      partition.loaded = true
      partition.hidden = false
    } catch (error) {
      if (generation !== storeGeneration || partitionOf(spaceId) !== partition) return
      if (error instanceof ApiError) {
        if (isEntryHiddenError(error)) {
          partition.hidden = true
        } else {
          partition.error = { code: error.code, message: error.message }
        }
      } else {
        partition.error = { code: 'NETWORK_ERROR', message: '' }
      }
    } finally {
      if (generation === storeGeneration && partitionOf(spaceId) === partition) {
        partition.loading = false
      }
    }
  }

  /** 动作成功后的公共收尾：响应回填本地 + 同空间列表后台刷新 */
  async function applyAndRefresh(
    spaceId: number,
    cardId: number,
    state: ActionCardState,
  ): Promise<void> {
    const card = partitions.value.get(spaceId)?.cards.find((c) => c.id === cardId)
    if (card) card.state = state
    await loadForSpace(spaceId)
  }

  /** 仅在消息引用需要卡片时使用：已加载/加载中/已隐藏分区不重复请求。 */
  async function ensureLoaded(spaceId: number): Promise<void> {
    const partition = requirePartition(spaceId)
    if (partition.loaded || partition.loading || partition.hidden) return
    await loadForSpace(spaceId)
  }

  // ---- 单卡状态转换 ----

  async function transition(
    spaceId: number,
    cardId: number,
    action: 'view' | 'dismiss' | 'accept',
  ): Promise<void> {
    try {
      const response =
        action === 'view'
          ? await viewActionCard(cardId)
          : action === 'dismiss'
            ? await dismissActionCard(cardId)
            : await acceptActionCard(cardId)
      await applyAndRefresh(spaceId, cardId, response.state)
    } catch (error) {
      if (error instanceof ApiError) {
        const expired =
          error.status === 410 || error.code === ACTION_CARD_ERRORS.CARD_EXPIRED
        if (expired || error.code === ACTION_CARD_ERRORS.CARD_STATE_CONFLICT) {
          // 并发竞争 / 已过期：状态以服务端为真源，刷新对齐后把错误交给 UI 提示
          await loadForSpace(spaceId)
        }
      }
      throw error
    }
  }

  /**
   * 两步发送的第二步：执行卡片动作。可重试失败（409 CARD_EXECUTE_REJECTED）
   * 服务端保持 accepted——本地不改状态仅刷新；410 对齐为过期。
   * 错误原样抛出，由 UI 决定提示文案（execute 时展示 detail.reason）。
   */
  async function execute(spaceId: number, cardId: number): Promise<void> {
    try {
      const response = await executeActionCard(cardId)
      await applyAndRefresh(spaceId, cardId, response.state)
    } catch (error) {
      if (error instanceof ApiError) {
        const expired =
          error.status === 410 || error.code === ACTION_CARD_ERRORS.CARD_EXPIRED
        if (
          expired ||
          error.code === ACTION_CARD_ERRORS.CARD_EXECUTE_REJECTED ||
          error.code === ACTION_CARD_ERRORS.CARD_STATE_CONFLICT
        ) {
          await loadForSpace(spaceId)
        }
      }
      throw error
    }
  }

  // ---- 失效边界 ----

  /** 空间切换：清空该空间分区（下次进入重新拉取） */
  function resetForSpace(spaceId: number): void {
    storeGeneration += 1
    partitions.value.delete(spaceId)
  }

  /** 登出 / 账号切换 / 撤权（auth.clearSession 调用）：全量清理 */
  function clear(): void {
    storeGeneration += 1
    partitions.value.clear()
  }

  return {
    partitions,
    partitionOf,
    cardsOf,
    pendingCountOf,
    loadForSpace,
    ensureLoaded,
    transition,
    markViewed(spaceId: number, cardId: number): Promise<void> {
      return transition(spaceId, cardId, 'view')
    },
    dismiss(spaceId: number, cardId: number): Promise<void> {
      return transition(spaceId, cardId, 'dismiss')
    },
    accept(spaceId: number, cardId: number): Promise<void> {
      return transition(spaceId, cardId, 'accept')
    },
    execute,
    resetForSpace,
    clear,
  }
})
