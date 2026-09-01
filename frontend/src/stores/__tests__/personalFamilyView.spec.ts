import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as personalFamilyViewApi from '@/api/personalFamilyView'
import { usePersonalFamilyViewStore } from '@/stores/personalFamilyView'
import type {
  PersonalFamilyViewData,
  PersonalFamilyViewDisplay,
  PersonalFamilyViewSnapshot,
} from '@/types/api'

vi.mock('@/api/personalFamilyView', () => ({
  fetchPersonalFamilyView: vi.fn(),
}))

const mockedFetch = vi.mocked(personalFamilyViewApi.fetchPersonalFamilyView)

function makeDisplay(id: number, name = '张三'): PersonalFamilyViewDisplay {
  return {
    id,
    name,
    gender: 'm',
    birth: null,
    death: null,
    bio: null,
    avatar_path: null,
    privacy_mode: 'perpetual',
    claim_status: 'claimed',
  }
}

function makeData(spaceId: number, overrides: Partial<PersonalFamilyViewData> = {}): PersonalFamilyViewData {
  return {
    space_id: spaceId,
    status: 'current',
    view_version: 1,
    computed_at: '2026-09-01T08:00:00',
    nodes: [],
    edges: [],
    truncated: false,
    next_cursor: null,
    stale_reason: null,
    ...overrides,
  }
}

function makeSnapshot(
  spaceId: number,
  etag: string | null = 'W/"v1"',
  overrides: Partial<PersonalFamilyViewData> = {},
): PersonalFamilyViewSnapshot {
  return { data: makeData(spaceId, overrides), etag }
}

/** 可控 promise：驱动 epoch 竞态（late response 在 clear 之后才 resolve） */
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (cause: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('personalFamilyView store（design.md §4.1）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('load：按明确 space_id 拉取并缓存快照，forSpace 返回已解码数据', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot(7))
    const store = usePersonalFamilyViewStore()

    const data = await store.load(7)
    expect(mockedFetch).toHaveBeenCalledWith(7, null)
    expect(data?.space_id).toBe(7)
    expect(store.forSpace(7)?.space_id).toBe(7)
    expect(store.forSpace(8)).toBeNull()
    expect(store.isLoading(7)).toBe(false)
  })

  it('load：带缓存的非 force 请求复用 etag，304 保留同一快照对象', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot(7))
    const store = usePersonalFamilyViewStore()
    await store.load(7)
    const cachedSnapshot = store.bySpace.get(7)

    mockedFetch.mockResolvedValueOnce(null) // 304 Not Modified
    const second = await store.load(7)
    expect(mockedFetch).toHaveBeenLastCalledWith(7, 'W/"v1"')
    // 同一对象引用：不允许 304 时重建快照
    expect(second).toBe(cachedSnapshot?.data)
    expect(store.bySpace.get(7)).toBe(cachedSnapshot)
  })

  it('refresh（force）：跳过 ETag 条件请求并用新快照替换', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot(7))
    const store = usePersonalFamilyViewStore()
    await store.load(7)
    const cachedSnapshot = store.bySpace.get(7)

    mockedFetch.mockResolvedValue(makeSnapshot(7, 'W/"v2"', { view_version: 2 }))
    const refreshed = await store.refresh(7)
    expect(mockedFetch).toHaveBeenLastCalledWith(7, null)
    expect(refreshed?.view_version).toBe(2)
    expect(store.bySpace.get(7)?.etag).toBe('W/"v2"')
    expect(store.bySpace.get(7)).not.toBe(cachedSnapshot)
  })

  it('reloadAfterBridgeChange：Bridge 状态变化后强制 refresh（跳过 ETag），只读重载无任何写操作', async () => {
    mockedFetch.mockResolvedValue(
      makeSnapshot(7, 'W/"v1"', {
        nodes: [
          {
            user_id: 10,
            display: makeDisplay(10),
            visibility_level: 'lineage_summary',
            inclusion_reason_code: 'confirmed_path',
          },
        ],
      }),
    )
    const store = usePersonalFamilyViewStore()
    await store.load(7)

    // Bridge pending → active：通知/待办接线（Phase 5）调用本入口重载授权投影
    mockedFetch.mockResolvedValue(makeSnapshot(7, 'W/"v2"', { view_version: 2 }))
    const reloaded = await store.reloadAfterBridgeChange(7)
    expect(mockedFetch).toHaveBeenLastCalledWith(7, null)
    expect(reloaded?.view_version).toBe(2)
    expect(store.bySpace.get(7)?.etag).toBe('W/"v2"')
    // 只读重载：不产生任何领域写请求（本测试仅 mock 了 GET 投影端点）
    expect(mockedFetch).toHaveBeenCalledTimes(2)
  })

  it('epoch：clearSpace 后才 resolve 的旧响应不回写', async () => {
    const pending = deferred<PersonalFamilyViewSnapshot | null>()
    mockedFetch.mockReturnValue(pending.promise)
    const store = usePersonalFamilyViewStore()

    const inflight = store.load(7)
    store.clearSpace(7)
    pending.resolve(makeSnapshot(7))

    await expect(inflight).resolves.toBeNull()
    expect(store.forSpace(7)).toBeNull()
    expect(store.isLoading(7)).toBe(false)
  })

  it('epoch：clear 后才 reject 的旧请求不写入错误状态', async () => {
    const pending = deferred<PersonalFamilyViewSnapshot | null>()
    mockedFetch.mockReturnValue(pending.promise)
    const store = usePersonalFamilyViewStore()

    const inflight = store.load(7)
    store.clear()
    pending.reject(new Error('late failure'))

    await expect(inflight).rejects.toThrow('late failure')
    expect(store.errorFor(7)).toBeNull()
  })

  it('load 失败：错误记录在该空间并向上抛出', async () => {
    mockedFetch.mockRejectedValue(new Error('network down'))
    const store = usePersonalFamilyViewStore()

    await expect(store.load(7)).rejects.toThrow('network down')
    expect(store.errorFor(7)).toMatchObject({ message: 'network down' })
    expect(store.isLoading(7)).toBe(false)
  })

  it('clearSpace 只清理目标空间；clear 清空全部并失效在途响应', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot(7))
    const store = usePersonalFamilyViewStore()
    await store.load(7)
    mockedFetch.mockResolvedValue(makeSnapshot(8))
    await store.load(8)

    store.clearSpace(7)
    expect(store.forSpace(7)).toBeNull()
    expect(store.forSpace(8)?.space_id).toBe(8)

    store.clear()
    expect(store.forSpace(8)).toBeNull()
    expect(store.errorFor(8)).toBeNull()
  })

  it('getVisiblePerson：只在当前快照内命中，找不到返回 null（安全不可见）', async () => {
    mockedFetch.mockResolvedValue(
      makeSnapshot(7, 'W/"v1"', {
        nodes: [
          {
            user_id: 10,
            display: makeDisplay(10),
            visibility_level: 'household_detail',
            inclusion_reason_code: 'space_member',
          },
        ],
      }),
    )
    const store = usePersonalFamilyViewStore()
    await store.load(7)

    expect(store.getVisiblePerson(7, 10)?.display.name).toBe('张三')
    expect(store.getVisiblePerson(7, 99)).toBeNull()
    // 无快照的空间一律 null，不做跨空间查找
    expect(store.getVisiblePerson(8, 10)).toBeNull()
  })

  it('getRelationshipDetail：两端任一顺序命中同一条边', async () => {
    mockedFetch.mockResolvedValue(
      makeSnapshot(7, 'W/"v1"', {
        edges: [
          {
            from_user_id: 10,
            to_user_id: 20,
            edge_kind: 'structure',
            path: [],
            alternative_paths: [],
            path_class: 'direct',
            concept_code: null,
            term: '父子',
            inclusion_reason_code: 'confirmed_relation',
          },
        ],
      }),
    )
    const store = usePersonalFamilyViewStore()
    await store.load(7)

    expect(store.getRelationshipDetail(7, 20)?.term).toBe('父子')
    expect(store.getRelationshipDetail(7, 10)?.to_user_id).toBe(20)
    expect(store.getRelationshipDetail(7, 99)).toBeNull()
  })
})
