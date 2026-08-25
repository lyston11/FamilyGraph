import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as spacesApi from '@/api/spaces'
import { useSpacesStore } from '@/stores/spaces'
import type { FamilySpace } from '@/types/api'

vi.mock('@/api/spaces', () => ({
  fetchSpaces: vi.fn(),
  createSpace: vi.fn(),
  fetchSpaceMembers: vi.fn(),
  inviteToSpace: vi.fn(),
  removeOrWithdrawMembership: vi.fn(),
  resolveMembership: vi.fn(),
}))

const mockedFetch = vi.mocked(spacesApi.fetchSpaces)
const mockedMembers = vi.mocked(spacesApi.fetchSpaceMembers)

function makeSpace(overrides: Partial<FamilySpace> = {}): FamilySpace {
  return {
    id: 1,
    name: '我家',
    owner_id: 1,
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
})
