import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as spaceStatsApi from '@/api/spaceStats'
import { useSpaceStatsStore } from '@/stores/spaceStats'
import type { SpaceStatsSnapshot } from '@/types/api'

vi.mock('@/api/spaceStats', () => ({
  fetchSpaceStats: vi.fn(),
}))

const mockedFetch = vi.mocked(spaceStatsApi.fetchSpaceStats)

function makeSnapshot(spaceId: number, etag: string | null = 'W/"s1"'): SpaceStatsSnapshot {
  return {
    data: {
      space_id: spaceId,
      space_kind: 'household',
      status: 'current',
      view_version: 2,
      node_count: 5,
      edge_count: 3,
      member_count: 2,
      relation_distribution: [{ dir_class: 'spouse', count: 1 }],
      pending_action_cards: 0,
      pending_memberships: 1,
      computed_at: '2026-09-01T08:00:00',
      stale_reason: null,
    },
    etag,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

describe('spaceStats store（design.md §4.4）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('load：按明确 space_id 拉取并缓存授权聚合', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot(7))
    const store = useSpaceStatsStore()

    const data = await store.load(7)
    expect(mockedFetch).toHaveBeenCalledWith(7, null)
    expect(data?.node_count).toBe(5)
    expect(store.forSpace(7)?.member_count).toBe(2)
    expect(store.forSpace(8)).toBeNull()
  })

  it('304：保留同一快照对象', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot(7))
    const store = useSpaceStatsStore()
    await store.load(7)
    const cached = store.bySpace.get(7)

    mockedFetch.mockResolvedValueOnce(null)
    const second = await store.load(7)
    expect(mockedFetch).toHaveBeenLastCalledWith(7, 'W/"s1"')
    expect(second).toBe(cached?.data)
    expect(store.bySpace.get(7)).toBe(cached)
  })

  it('refresh（force）：跳过 ETag 并替换快照', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot(7))
    const store = useSpaceStatsStore()
    await store.load(7)

    mockedFetch.mockResolvedValue(makeSnapshot(7, 'W/"s2"'))
    await store.refresh(7)
    expect(mockedFetch).toHaveBeenLastCalledWith(7, null)
    expect(store.bySpace.get(7)?.etag).toBe('W/"s2"')
  })

  it('epoch：clearSpace 后才 resolve 的旧响应不回写', async () => {
    const pending = deferred<SpaceStatsSnapshot | null>()
    mockedFetch.mockReturnValue(pending.promise)
    const store = useSpaceStatsStore()

    const inflight = store.load(7)
    store.clearSpace(7)
    pending.resolve(makeSnapshot(7))

    await expect(inflight).resolves.toBeNull()
    expect(store.forSpace(7)).toBeNull()
    expect(store.errorFor(7)).toBeNull()
  })

  it('load 失败：错误记录在该空间并向上抛出', async () => {
    mockedFetch.mockRejectedValue(new Error('stats down'))
    const store = useSpaceStatsStore()

    await expect(store.load(7)).rejects.toThrow('stats down')
    expect(store.errorFor(7)).toMatchObject({ message: 'stats down' })
    expect(store.isLoading(7)).toBe(false)
  })

  it('clearSpace 只清理目标空间；clear 清空全部', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot(7))
    const store = useSpaceStatsStore()
    await store.load(7)
    mockedFetch.mockResolvedValue(makeSnapshot(8))
    await store.load(8)

    store.clearSpace(7)
    expect(store.forSpace(7)).toBeNull()
    expect(store.forSpace(8)).not.toBeNull()

    store.clear()
    expect(store.forSpace(8)).toBeNull()
  })
})
