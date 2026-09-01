import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as householdApi from '@/api/household'
import { useHouseholdCardStore } from '@/stores/household'
import type { HouseholdCardSnapshot } from '@/types/api'

vi.mock('@/api/household', () => ({
  fetchHouseholdCard: vi.fn(),
}))

const mockedFetch = vi.mocked(householdApi.fetchHouseholdCard)

function makeSnapshot(spaceId: number, etag: string | null = 'W/"v1"'): HouseholdCardSnapshot {
  return {
    data: {
      space_id: spaceId,
      space_kind: 'household',
      space_name: '我的家庭',
      view_version: 3,
      computed_at: '2026-09-01T08:00:00',
      viewer: {
        id: 10,
        name: '本人',
        gender: 'm',
        birth: null,
        death: null,
        bio: null,
        avatar_path: null,
        privacy_mode: 'perpetual',
        claim_status: 'claimed',
      },
      members: [],
      allowed_actions: { can_invite_members: true, can_create_household: false, empty_state_hint: null },
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

describe('householdCard store（design.md §4.2）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('load：按明确 space_id 拉取并缓存快照', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot(7))
    const store = useHouseholdCardStore()

    const data = await store.load(7)
    expect(mockedFetch).toHaveBeenCalledWith(7, null)
    expect(data?.space_kind).toBe('household')
    expect(store.forSpace(7)?.space_name).toBe('我的家庭')
    expect(store.forSpace(8)).toBeNull()
  })

  it('304：保留同一快照对象，不重建引用', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot(7))
    const store = useHouseholdCardStore()
    await store.load(7)
    const cached = store.bySpace.get(7)

    mockedFetch.mockResolvedValueOnce(null)
    const second = await store.load(7)
    expect(mockedFetch).toHaveBeenLastCalledWith(7, 'W/"v1"')
    expect(second).toBe(cached?.data)
    expect(store.bySpace.get(7)).toBe(cached)
  })

  it('refresh（force）：跳过 ETag 并替换快照', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot(7))
    const store = useHouseholdCardStore()
    await store.load(7)

    mockedFetch.mockResolvedValue(makeSnapshot(7, 'W/"v2"'))
    await store.refresh(7)
    expect(mockedFetch).toHaveBeenLastCalledWith(7, null)
    expect(store.bySpace.get(7)?.etag).toBe('W/"v2"')
  })

  it('epoch：clearSpace 后才 resolve 的旧响应不回写', async () => {
    const pending = deferred<HouseholdCardSnapshot | null>()
    mockedFetch.mockReturnValue(pending.promise)
    const store = useHouseholdCardStore()

    const inflight = store.load(7)
    store.clearSpace(7)
    pending.resolve(makeSnapshot(7))

    await expect(inflight).resolves.toBeNull()
    expect(store.forSpace(7)).toBeNull()
    expect(store.errorFor(7)).toBeNull()
  })

  it('load 失败：错误记录在该空间并向上抛出', async () => {
    mockedFetch.mockRejectedValue(new Error('family card down'))
    const store = useHouseholdCardStore()

    await expect(store.load(7)).rejects.toThrow('family card down')
    expect(store.errorFor(7)).toMatchObject({ message: 'family card down' })
    expect(store.isLoading(7)).toBe(false)
  })

  it('clearSpace 只清理目标空间；clear 清空全部', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot(7))
    const store = useHouseholdCardStore()
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
