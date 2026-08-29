import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as spacesApi from '@/api/spaces'
import { useSpacesStore } from '@/stores/spaces'
import type { FamilySpace } from '@/types/api'

vi.mock('@/api/spaces', () => ({
  fetchSpaces: vi.fn(),
  createSpace: vi.fn(),
  fetchSpaceMembers: vi.fn(),
  fetchSpaceProfileRefs: vi.fn().mockResolvedValue([]),
  inviteToSpace: vi.fn(),
  removeOrWithdrawMembership: vi.fn(),
  resolveMembership: vi.fn(),
  joinByUser: vi.fn(),
  getSpacePositions: vi.fn(),
  putSpacePositions: vi.fn(),
  createOwnershipTransfer: vi.fn(),
  fetchOwnershipTransfers: vi.fn().mockResolvedValue([]),
  respondOwnershipTransfer: vi.fn(),
}))

const mockedFetch = vi.mocked(spacesApi.fetchSpaces)
const mockedMembers = vi.mocked(spacesApi.fetchSpaceMembers)
const mockedProfileRefs = vi.mocked(spacesApi.fetchSpaceProfileRefs)

function makeSpace(overrides: Partial<FamilySpace> = {}): FamilySpace {
  return {
    id: 1,
    name: '我家',
    owner_id: 1,
    kind: 'household',
    created_at: '2026-08-25T00:00:00',
    pending_count: 0,
    member_count: 1,
    ...overrides,
  }
}

describe('spaces store（AD-3）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('加载后默认选中第一个空间并拉取成员', async () => {
    mockedFetch.mockResolvedValue([makeSpace(), makeSpace({ id: 2, name: '婚后小家' })])
    mockedMembers.mockResolvedValue([])
    const store = useSpacesStore()
    await store.load()
    expect(store.currentSpaceId).toBe(1)
    expect(store.currentSpace?.name).toBe('我家')
    expect(mockedMembers).toHaveBeenCalledWith(1)
  })

  it('无任何空间 → 空列表（首页引导创建默认空间，AD-3.4）', async () => {
    mockedFetch.mockResolvedValue([])
    const store = useSpacesStore()
    await store.load()
    expect(store.spaces).toHaveLength(0)
    expect(store.currentSpace).toBeNull()
  })

  it('创建空间后立即成为当前空间且成员仅自己', async () => {
    mockedFetch.mockResolvedValue([])
    vi.mocked(spacesApi.createSpace).mockResolvedValue(makeSpace({ id: 5, name: '我们家' }))
    mockedMembers.mockResolvedValue([
      {
        id: 9, space_id: 5, user_id: 1, added_by: 1,
        role: 'owner', status: 'active', updated_at: '2026-08-25T00:00:00',
      },
    ])
    const store = useSpacesStore()
    await store.load()
    await store.create('我们家')
    expect(store.currentSpaceId).toBe(5)
    expect(store.activeMembers).toHaveLength(1)
  })

  it('loadMembers 同步拉取待确档最小引用；失败时置空不阻塞（AC-F2）', async () => {
    mockedFetch.mockResolvedValue([makeSpace({ id: 3, kind: 'lineage' })])
    mockedMembers.mockResolvedValue([])
    mockedProfileRefs.mockResolvedValue([
      { profile_id: 42, name: '先祖', added_at: '2026-08-26T00:00:00' },
    ])
    const store = useSpacesStore()
    await store.load()

    expect(mockedProfileRefs).toHaveBeenCalledWith(3)
    expect(store.profileRefs).toHaveLength(1)
    expect(store.profileRefs[0]?.name).toBe('先祖')

    // 引用端点失败 → 置空但不影响成员加载
    mockedProfileRefs.mockRejectedValue(new Error('404'))
    await store.loadMembers(3)
    expect(store.profileRefs).toEqual([])
  })

  it('clear：清空待确档引用缓存', async () => {
    mockedFetch.mockResolvedValue([makeSpace()])
    mockedMembers.mockResolvedValue([])
    mockedProfileRefs.mockResolvedValue([
      { profile_id: 42, name: '先祖', added_at: '2026-08-26T00:00:00' },
    ])
    const store = useSpacesStore()
    await store.load()
    expect(store.profileRefs).toHaveLength(1)

    store.clear()
    expect(store.profileRefs).toEqual([])
  })
})

describe('spaces store 会话代际隔离（P2）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('clear 后旧 load 的迟到响应不回写新会话状态', async () => {
    let resolveSpaces: (value: FamilySpace[]) => void = () => {}
    mockedFetch.mockImplementation(
      () =>
        new Promise<FamilySpace[]>((resolve) => {
          resolveSpaces = resolve
        }),
    )
    mockedMembers.mockResolvedValue([])
    const store = useSpacesStore()

    const pending = store.load()
    store.clear() // 登出/切换：代际递增
    resolveSpaces([makeSpace()])
    await pending

    expect(store.spaces).toEqual([])
    expect(store.loading).toBe(false)
  })

  it('空间切换期间旧空间的成员响应不回写新空间', async () => {
    const store = useSpacesStore()
    let resolveOld: (value: Awaited<ReturnType<typeof spacesApi.fetchSpaceMembers>>) => void =
      () => {}
    mockedFetch.mockResolvedValue([makeSpace(), makeSpace({ id: 2 })])
    mockedMembers.mockImplementation(() => {
      if (mockedMembers.mock.calls.length === 1) {
        return new Promise((resolve) => {
          resolveOld = resolve
        }) as never
      }
      return Promise.resolve([]) as never
    })
    // 初始 load 挂在空间 1 的成员请求上，不整句 await（其迟到响应稍后到达）
    void store.load()

    const switchToNew = store.loadMembers(2)
    // 新空间先完成；随后旧空间的迟到响应到达
    resolveOld([
      { id: 501, space_id: 1, user_id: 99, user_name: '旧空间成员', added_by: 1, role: 'owner', status: 'active', updated_at: '2026-08-29T00:00:00' },
    ])
    await switchToNew
    expect(store.members).toEqual([])
    expect(store.currentSpaceId).toBe(2)
  })
})
