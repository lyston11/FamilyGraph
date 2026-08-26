import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ACTION_CARD_ERRORS } from '@/api/actionCards'
import * as actionCardsApi from '@/api/actionCards'
import { ApiError } from '@/api/errors'
import { useActionCardsStore } from '@/stores/actionCards'
import type { ActionCard } from '@/types/actionCard'

// 注意：factory 内不可引用本模块顶层导入（vitest 提升），常量内联保持字面量一致
vi.mock('@/api/actionCards', () => ({
  ACTION_CARD_ERRORS: {
    CARD_STATE_CONFLICT: 'CARD_STATE_CONFLICT',
    CARD_EXPIRED: 'CARD_EXPIRED',
    CARD_EXECUTE_REJECTED: 'CARD_EXECUTE_REJECTED',
    SPACE_FORBIDDEN_ACTOR: 'SPACE_FORBIDDEN_ACTOR',
  },
  fetchActionCards: vi.fn(),
  viewActionCard: vi.fn(),
  dismissActionCard: vi.fn(),
  acceptActionCard: vi.fn(),
  executeActionCard: vi.fn(),
  friendlyActionCardError: (code: string) => code,
}))

const mockedFetch = vi.mocked(actionCardsApi.fetchActionCards)
const mockedAccept = vi.mocked(actionCardsApi.acceptActionCard)
const mockedExecute = vi.mocked(actionCardsApi.executeActionCard)

function makeCard(overrides: Partial<ActionCard> = {}): ActionCard {
  return {
    id: 1,
    kind: 'lineage_request',
    space_id: 7,
    subject_user: { id: 10, name: '张三' },
    object_user: { id: 20, name: '李四' },
    reason_text: '你们是堂兄弟，且都已确认身份',
    evidence: { fact_ids: [3, 4], path_summary: '张三 → 祖父 → 李四', evidence_version: 2 },
    proposed_action: { type: 'request_lineage', params: { space_id: 9 } },
    privacy_effect: '对方将看到你的名字与称谓',
    state: 'pending',
    expires_at: '2026-09-30T00:00:00',
    created_at: '2026-08-26T00:00:00',
    revision: 1,
    ...overrides,
  }
}

describe('actionCards store（V2.4 Block S3）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loadForSpace：按空间拉取列表并派生 pending 数', async () => {
    mockedFetch.mockResolvedValue([makeCard(), makeCard({ id: 2, state: 'accepted' })])
    const store = useActionCardsStore()

    await store.loadForSpace(7)
    expect(mockedFetch).toHaveBeenCalledWith(7)
    expect(store.cardsOf(7)).toHaveLength(2)
    expect(store.pendingCountOf(7)).toBe(1)
    expect(store.partitionOf(7)?.hidden).toBe(false)
  })

  it('loadForSpace：403 SPACE_FORBIDDEN_ACTOR → 入口降级 hidden=true', async () => {
    mockedFetch.mockRejectedValue(
      new ApiError(403, ACTION_CARD_ERRORS.SPACE_FORBIDDEN_ACTOR, '无权限'),
    )
    const store = useActionCardsStore()

    await store.loadForSpace(7)
    expect(store.partitionOf(7)?.hidden).toBe(true)
    // 降级后不再重复请求
    await store.loadForSpace(7)
    expect(mockedFetch).toHaveBeenCalledTimes(1)
  })

  it('loadForSpace：503（flag 关闭）→ hidden=true', async () => {
    mockedFetch.mockRejectedValue(new ApiError(503, 'KINSHIP_FLAG_DISABLED', '未启用'))
    const store = useActionCardsStore()

    await store.loadForSpace(7)
    expect(store.partitionOf(7)?.hidden).toBe(true)
  })

  it('loadForSpace：其他错误保留入口并记录 error（不隐藏）', async () => {
    mockedFetch.mockRejectedValue(new ApiError(500, 'HTTP_ERROR', '服务器错误'))
    const store = useActionCardsStore()

    await store.loadForSpace(7)
    expect(store.partitionOf(7)?.hidden).toBe(false)
    expect(store.partitionOf(7)?.error?.code).toBe('HTTP_ERROR')
  })

  it.each([
    ['markViewed', 'viewActionCard'],
    ['dismiss', 'dismissActionCard'],
    ['accept', 'acceptActionCard'],
  ] as const)('%s：响应回填本地并触发同空间列表刷新', async (action, apiName) => {
    mockedFetch
      .mockResolvedValueOnce([makeCard()])
      .mockResolvedValue([makeCard({ state: 'viewed' })])
    vi.mocked(actionCardsApi[apiName]).mockResolvedValue({ id: 1, state: 'viewed', revision: 2 })
    const store = useActionCardsStore()
    await store.loadForSpace(7)
    mockedFetch.mockClear()

    await store[action](7, 1)
    expect(actionCardsApi[apiName]).toHaveBeenCalledWith(1)
    // 动作后触发一次列表刷新
    expect(mockedFetch).toHaveBeenCalledTimes(1)
    expect(store.cardsOf(7)[0]?.state).toBe('viewed')
  })

  it('并发 accept 竞争：409 CARD_STATE_CONFLICT 刷新对齐后抛错，状态以服务端为准', async () => {
    mockedFetch
      .mockResolvedValueOnce([makeCard()])
      .mockResolvedValue([makeCard({ state: 'viewed' })])
    mockedAccept.mockRejectedValue(
      new ApiError(409, ACTION_CARD_ERRORS.CARD_STATE_CONFLICT, '已被其他操作更新'),
    )
    const store = useActionCardsStore()
    await store.loadForSpace(7)
    mockedFetch.mockClear()

    await expect(store.accept(7, 1)).rejects.toBeInstanceOf(ApiError)
    // 冲突后刷新对齐服务端状态
    expect(mockedFetch).toHaveBeenCalledTimes(1)
    expect(store.cardsOf(7)[0]?.state).toBe('viewed')
  })

  it('execute 成功：本地置 executed 并刷新列表', async () => {
    mockedFetch.mockResolvedValue([makeCard({ state: 'executed' })])
    mockedExecute.mockResolvedValue({ id: 1, state: 'executed' })
    const store = useActionCardsStore()
    await store.loadForSpace(7)

    await store.execute(7, 1)
    expect(store.cardsOf(7)[0]?.state).toBe('executed')
    expect(store.pendingCountOf(7)).toBe(0)
  })

  it('execute 可重试失败：409 CARD_EXECUTE_REJECTED 保持 accepted，仅刷新不改状态', async () => {
    const detail = { reason: '目标成员资格已撤销' }
    mockedFetch.mockResolvedValue([makeCard({ state: 'accepted' })])
    mockedExecute.mockRejectedValue(
      new ApiError(409, ACTION_CARD_ERRORS.CARD_EXECUTE_REJECTED, '无法执行', detail),
    )
    const store = useActionCardsStore()
    await store.loadForSpace(7)
    mockedFetch.mockClear()

    await expect(store.execute(7, 1)).rejects.toMatchObject({ detail })
    expect(store.cardsOf(7)[0]?.state).toBe('accepted')
    expect(mockedFetch).toHaveBeenCalledTimes(1)
  })

  it('execute 时卡片已过期：410 对齐为终态并刷新', async () => {
    mockedFetch.mockResolvedValue([makeCard({ state: 'expired' })])
    mockedExecute.mockRejectedValue(new ApiError(410, ACTION_CARD_ERRORS.CARD_EXPIRED, '已过期'))
    const store = useActionCardsStore()
    await store.loadForSpace(7)

    await expect(store.execute(7, 1)).rejects.toBeInstanceOf(ApiError)
    expect(store.cardsOf(7)[0]?.state).toBe('expired')
  })

  it('resetForSpace / clear：失效边界清理分区', async () => {
    mockedFetch.mockResolvedValue([makeCard()])
    const store = useActionCardsStore()
    await store.loadForSpace(7)
    await store.loadForSpace(8)
    expect(store.cardsOf(7)).toHaveLength(1)

    store.resetForSpace(7)
    expect(store.cardsOf(7)).toHaveLength(0)
    expect(store.cardsOf(8)).toHaveLength(1)

    store.clear()
    expect(store.cardsOf(8)).toHaveLength(0)
  })
})
