import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/errors'
import * as kinshipApi from '@/api/kinship'
import { useKinshipStore } from '@/stores/kinship'
import type { KinshipResolve, MyTerm, ParseResult } from '@/types/kinship'

/**
 * stores/kinship 合同测试（V2.3 Block E4c）：
 * - resolve 缓存 key = space:viewer:target；force 绕过；
 * - 503 KINSHIP_FLAG_DISABLED → available=false（UI 隐藏入口的唯一信号）；
 * - 空间切换 resetForSpace 只清该空间；clear() 全量清（auth 登出联动）；
 * - 个人纠正后该空间 resolve 缓存全部失效（旧称谓不得继续展示，KI-5）。
 */

vi.mock('@/api/kinship', () => ({
  KINSHIP_FLAG_DISABLED: 'KINSHIP_FLAG_DISABLED',
  fetchMyTerms: vi.fn(),
  updateMyTerm: vi.fn(),
  resolveKinship: vi.fn(),
  recordTermUsage: vi.fn(),
  parseRelationText: vi.fn(),
}))

const mockedFetchMyTerms = vi.mocked(kinshipApi.fetchMyTerms)
const mockedUpdateMyTerm = vi.mocked(kinshipApi.updateMyTerm)
const mockedResolve = vi.mocked(kinshipApi.resolveKinship)
const mockedUsage = vi.mocked(kinshipApi.recordTermUsage)
const mockedParse = vi.mocked(kinshipApi.parseRelationText)

function makeResolve(overrides: Partial<KinshipResolve> = {}): KinshipResolve {
  return {
    found: true,
    viewer_user_id: 1,
    target_user_id: 2,
    space_id: 10,
    path_class: 'parent_child',
    concept_code: 'F_PARENT',
    explanation_structural: '她是你的母亲',
    term: '妈妈',
    term_source_level: 'personal',
    term_entry_id: 5,
    main_path: [],
    alt_paths: [],
    fact_state: {
      confirmed: 1,
      proposed: 0,
      disputed: 0,
      revoked: 0,
      evidence_fact_ids: [3],
    },
    cache_hit: false,
    algorithm_version: 'v1',
    ...overrides,
  }
}

function flagDisabledError(): ApiError {
  return new ApiError(503, 'KINSHIP_FLAG_DISABLED', '关系智能能力未启用')
}

describe('kinship store（V2.3 Block E4c）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('resolvePair 按 space:viewer:target 缓存：命中不发请求，force 绕过缓存', async () => {
    const store = useKinshipStore()
    const result = makeResolve()
    mockedResolve.mockResolvedValue(result)

    const first = await store.resolvePair(10, 1, 2)
    expect(first?.term).toBe('妈妈')
    expect(mockedResolve).toHaveBeenCalledTimes(1)

    // 同 key 命中缓存
    const cached = await store.resolvePair(10, 1, 2)
    expect(cached?.cache_hit).toBe(false) // 返回的是缓存对象本身
    expect(mockedResolve).toHaveBeenCalledTimes(1)
    expect(store.cachedResolve(10, 1, 2)?.concept_code).toBe('F_PARENT')

    // 不同 target 不共用缓存
    mockedResolve.mockResolvedValue(makeResolve({ target_user_id: 3 }))
    await store.resolvePair(10, 1, 3)
    expect(mockedResolve).toHaveBeenCalledTimes(2)

    // force 强制重算
    await store.resolvePair(10, 1, 2, { force: true })
    expect(mockedResolve).toHaveBeenCalledTimes(3)
  })

  it('flag 关闭（503）：available=false 且动作返回 null，UI 据此隐藏入口', async () => {
    const store = useKinshipStore()
    mockedResolve.mockRejectedValue(flagDisabledError())

    expect(await store.resolvePair(10, 1, 2)).toBeNull()
    expect(store.available).toBe(false)
    expect(store.isDisabled).toBe(true)
  })

  it('非降级错误原样抛出，不改变 available', async () => {
    const store = useKinshipStore()
    mockedResolve.mockRejectedValue(new ApiError(403, 'SPACE_FORBIDDEN_ACTOR', '仅空间成员可解析'))

    await expect(store.resolvePair(10, 1, 2)).rejects.toThrow('仅空间成员可解析')
    expect(store.available).toBeNull()
  })

  it('resetForSpace 只清该空间缓存与词条；其他空间保留', async () => {
    const store = useKinshipStore()
    mockedResolve
      .mockResolvedValueOnce(makeResolve({ space_id: 10 }))
      .mockResolvedValueOnce(makeResolve({ space_id: 11 }))
    await store.resolvePair(10, 1, 2)
    await store.resolvePair(11, 1, 2)

    mockedFetchMyTerms.mockResolvedValue([
      { entry_id: 5, concept_code: 'F_PARENT', term: '老妈', revision: 1, updated_at: '2026-08-26T00:00:00' },
    ])
    await store.loadMyTerms(10)

    store.resetForSpace(10)
    expect(store.cachedResolve(10, 1, 2)).toBeNull()
    expect(store.cachedResolve(11, 1, 2)).not.toBeNull()
    expect(store.myTerms).toEqual([])
    expect(store.myTermsSpaceId).toBeNull()
    // available 不受空间切换影响（能力仍在）
    expect(store.available).toBe(true)
  })

  it('correctTerm 更新本地词条、失效该空间全部 resolve 缓存', async () => {
    const store = useKinshipStore()
    mockedResolve
      .mockResolvedValueOnce(makeResolve())
      .mockResolvedValueOnce(makeResolve({ term_source_level: 'personal' }))
    await store.resolvePair(10, 1, 2)
    expect(store.cachedResolve(10, 1, 2)).not.toBeNull()

    mockedUpdateMyTerm.mockResolvedValue({
      entry_id: 9,
      concept_code: 'F_PARENT',
      term: '老妈',
      revision: 2,
      updated_at: '2026-08-26T01:00:00',
    })
    const saved = await store.correctTerm(10, 'F_PARENT', '老妈')

    expect(mockedUpdateMyTerm).toHaveBeenCalledWith({ spaceId: 10, conceptCode: 'F_PARENT', term: '老妈' })
    expect(saved?.term).toBe('老妈')
    // 该空间缓存已失效，下次读取会重新请求
    expect(store.cachedResolve(10, 1, 2)).toBeNull()
    await store.resolvePair(10, 1, 2)
    expect(mockedResolve).toHaveBeenCalledTimes(2)
  })

  it('loadMyTerms 带 space_id 拉取并记录空间；submitUsage 固定 manual_select', async () => {
    const store = useKinshipStore()
    const terms: MyTerm[] = [
      {
        entry_id: 5,
        concept_code: 'F_PARENT',
        term: '老妈',
        revision: 1,
        updated_at: '2026-08-26T00:00:00',
        resolved: { term: '老妈', source_level: 'personal', entry_id: 5 },
      },
    ]
    mockedFetchMyTerms.mockResolvedValue(terms)
    expect(await store.loadMyTerms(10)).toEqual(terms)
    expect(mockedFetchMyTerms).toHaveBeenCalledWith(10)
    expect(store.myTermsSpaceId).toBe(10)

    mockedUsage.mockResolvedValue({
      usage_id: 1,
      entry_id: 7,
      created: true,
      promotion: { promoted: true, demoted: false, eligible_accounts: 2 },
    })
    const usage = await store.submitUsage(10, 'F_PARENT', '老妈')
    expect(usage?.promotion.promoted).toBe(true)
    expect(mockedUsage).toHaveBeenCalledWith({
      spaceId: 10,
      conceptCode: 'F_PARENT',
      term: '老妈',
      sourceEvent: 'manual_select',
    })
  })

  it('parseText 成功存结果；失败写 parseError 并返回 null', async () => {
    const store = useKinshipStore()
    const parsed: ParseResult = {
      raw_text_id: 42,
      normalized_text: '舅爷爷',
      resolution_class: 'determined',
      candidate: { concept_code: 'E_GRANDPATERNAL_UNCLE', term: '舅爷爷', term_source_level: 'system' },
      graph_proof: { found: true, explanation_structural: '奶奶的兄弟' },
      proposals: [],
      conflicts: [],
      clarifying_question: null,
      evidence_morphemes: ['舅', '爷'],
    }
    mockedParse.mockResolvedValue(parsed)
    expect(await store.parseText(10, '舅爷爷')).toEqual(parsed)
    expect(store.parseResult?.resolution_class).toBe('determined')
    expect(store.parseError).toBeNull()

    mockedParse.mockRejectedValue(new ApiError(422, 'TERM_INVALID', '叫法文本非法'))
    expect(await store.parseText(10, '!!!')).toBeNull()
    expect(store.parseError).toContain('非法')
  })

  it('parseText 遇 503 返回 null 并置 disabled；clear() 全量清理（auth 登出联动）', async () => {
    const store = useKinshipStore()
    mockedParse.mockRejectedValue(flagDisabledError())
    expect(await store.parseText(10, '妈妈')).toBeNull()
    expect(store.isDisabled).toBe(true)

    // 先填充状态再清空
    mockedResolve.mockResolvedValue(makeResolve())
    await store.resolvePair(10, 1, 2)
    mockedParse.mockResolvedValue({
      raw_text_id: 1,
      normalized_text: 'x',
      resolution_class: 'ambiguous',
      candidate: null,
      graph_proof: { found: false, explanation_structural: null },
      proposals: [],
      conflicts: [],
      clarifying_question: 'q?',
      evidence_morphemes: [],
    })
    await store.parseText(10, 'x')

    store.clear()
    expect(store.available).toBeNull()
    expect(store.resolveCache.size).toBe(0)
    expect(store.myTerms).toEqual([])
    expect(store.parseResult).toBeNull()
    expect(store.parseError).toBeNull()
  })
})
