import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as notificationsApi from '@/api/notifications'
import { useNotificationsStore } from '@/stores/notifications'
import type { NotificationsSnapshot } from '@/types/api'

vi.mock('@/api/notifications', () => ({
  fetchNotifications: vi.fn(),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
}))

const mockedFetch = vi.mocked(notificationsApi.fetchNotifications)
const mockedMarkRead = vi.mocked(notificationsApi.markNotificationRead)
const mockedMarkAllRead = vi.mocked(notificationsApi.markAllNotificationsRead)

function makeItem(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    space_id: 7,
    kind: 'action_card' as const,
    payload: { title: '族谱连接请求', summary: null, actor_name: '成员20', space_name: null },
    domain_status: 'pending' as const,
    action_card: { card_id: 55, revision: 2 },
    created_at: '2026-09-01T08:00:00',
    read_at: null,
    ...overrides,
  }
}

function makeSnapshot(
  items: ReturnType<typeof makeItem>[],
  unreadCount: number,
  etag: string | null = 'W/"n1"',
): NotificationsSnapshot {
  return { data: { space_id: 7, items, unread_count: unreadCount }, etag }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

describe('notifications store（design.md §4.4）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('load：按明确 space_id 拉取，未读数来自服务端载荷', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot([makeItem()], 1))
    const store = useNotificationsStore()

    const page = await store.load(7)
    expect(mockedFetch).toHaveBeenCalledWith(7, null)
    expect(page?.items).toHaveLength(1)
    expect(store.unreadCountOf(7)).toBe(1)
    expect(store.unreadCountOf(8)).toBe(0)
  })

  it('304：保留同一快照对象', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot([makeItem()], 1))
    const store = useNotificationsStore()
    await store.load(7)
    const cached = store.bySpace.get(7)

    mockedFetch.mockResolvedValueOnce(null)
    await store.load(7)
    expect(mockedFetch).toHaveBeenLastCalledWith(7, 'W/"n1"')
    expect(store.bySpace.get(7)).toBe(cached)
  })

  it('markRead：仅服务端已读 + 重读列表；重读前不做乐观更新', async () => {
    const unreadItem = makeItem()
    const readItem = makeItem({ read_at: '2026-09-01T09:00:00' })
    mockedFetch.mockResolvedValueOnce(makeSnapshot([unreadItem], 1))
    const store = useNotificationsStore()
    await store.load(7)

    mockedMarkRead.mockResolvedValue({ id: 1, read_at: '2026-09-01T09:00:00' })
    const reload = deferred<NotificationsSnapshot | null>()
    mockedFetch.mockReturnValueOnce(reload.promise)
    const marking = store.markRead(7, 1)
    expect(mockedMarkRead).toHaveBeenCalledWith(1)

    // POST 成功但列表尚未重读：本地不得乐观改写 read_at/unread_count
    await Promise.resolve()
    await Promise.resolve()
    expect(store.forSpace(7)?.items[0]?.read_at).toBeNull()
    expect(store.unreadCountOf(7)).toBe(1)

    // 重读返回的是服务端已读态：read_at 变化，domain_status 与 ActionCard 引用不变
    reload.resolve(makeSnapshot([readItem], 0))
    await marking
    const item = store.forSpace(7)?.items[0]
    expect(item?.read_at).toBe('2026-09-01T09:00:00')
    expect(item?.domain_status).toBe('pending')
    expect(item?.action_card).toEqual({ card_id: 55, revision: 2 })
    expect(store.unreadCountOf(7)).toBe(0)
  })

  it('markRead 与 ActionCard 状态严格分离：已读后 ActionCard 引用仅随服务端载荷变化', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot([makeItem()], 1))
    const store = useNotificationsStore()
    await store.load(7)

    mockedMarkRead.mockResolvedValue({ id: 1, read_at: '2026-09-01T09:00:00' })
    // 服务端重读：领域仍 pending，ActionCard revision 未被已读操作改写
    mockedFetch.mockResolvedValue(
      makeSnapshot([makeItem({ read_at: '2026-09-01T09:00:00' })], 0),
    )
    await store.markRead(7, 1)

    const item = store.forSpace(7)?.items[0]
    expect(item?.read_at).not.toBeNull()
    expect(item?.domain_status).toBe('pending')
    expect(item?.action_card?.revision).toBe(2)
  })

  it('markRead 期间空间被清理：不再为旧上下文回读列表', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot([makeItem()], 1))
    const store = useNotificationsStore()
    await store.load(7)
    mockedFetch.mockClear()

    mockedMarkRead.mockResolvedValue({ id: 1, read_at: '2026-09-01T09:00:00' })
    const marking = store.markRead(7, 1)
    store.clearSpace(7)
    await marking

    expect(mockedFetch).not.toHaveBeenCalled()
    expect(store.forSpace(7)).toBeNull()
  })

  it('markRead 失败：错误向上抛出，列表保持服务端原状', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot([makeItem()], 1))
    const store = useNotificationsStore()
    await store.load(7)
    mockedFetch.mockClear()

    mockedMarkRead.mockRejectedValue(new Error('read endpoint down'))
    await expect(store.markRead(7, 1)).rejects.toThrow('read endpoint down')
    expect(mockedFetch).not.toHaveBeenCalled()
    expect(store.forSpace(7)?.items[0]?.read_at).toBeNull()
    expect(store.unreadCountOf(7)).toBe(1)
  })

  it('markAllRead：POST 全部已读后重读列表', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot([makeItem(), makeItem({ id: 2 })], 2))
    const store = useNotificationsStore()
    await store.load(7)

    mockedMarkAllRead.mockResolvedValue({ space_id: 7, marked_count: 2 })
    mockedFetch.mockResolvedValue(
      makeSnapshot(
        [
          makeItem({ read_at: '2026-09-01T09:30:00' }),
          makeItem({ id: 2, read_at: '2026-09-01T09:30:00' }),
        ],
        0,
      ),
    )
    await store.markAllRead(7)
    expect(mockedMarkAllRead).toHaveBeenCalledWith(7)
    expect(mockedFetch).toHaveBeenCalledWith(7, 'W/"n1"')
    expect(store.unreadCountOf(7)).toBe(0)
  })

  it('epoch：clear 后才 resolve 的旧列表响应不回写', async () => {
    const pending = deferred<NotificationsSnapshot | null>()
    mockedFetch.mockReturnValue(pending.promise)
    const store = useNotificationsStore()

    const inflight = store.load(7)
    store.clear()
    pending.resolve(makeSnapshot([makeItem()], 1))

    await expect(inflight).resolves.toBeNull()
    expect(store.forSpace(7)).toBeNull()
    expect(store.unreadCountOf(7)).toBe(0)
  })

  it('load 失败：错误记录在该空间并向上抛出', async () => {
    mockedFetch.mockRejectedValue(new Error('notifications down'))
    const store = useNotificationsStore()

    await expect(store.load(7)).rejects.toThrow('notifications down')
    expect(store.errorFor(7)).toMatchObject({ message: 'notifications down' })
  })

  it('clearSpace 只清理目标空间；clear 清空全部', async () => {
    mockedFetch.mockResolvedValue(makeSnapshot([makeItem()], 1))
    const store = useNotificationsStore()
    await store.load(7)
    mockedFetch.mockResolvedValue({ data: { space_id: 8, items: [], unread_count: 0 }, etag: null })
    await store.load(8)

    store.clearSpace(7)
    expect(store.forSpace(7)).toBeNull()
    expect(store.forSpace(8)?.space_id).toBe(8)

    store.clear()
    expect(store.forSpace(8)).toBeNull()
  })
})
