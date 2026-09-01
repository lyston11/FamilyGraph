import { describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'
import {
  decodeNotificationsPage,
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from '@/api/notifications'

vi.mock('@/api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

function makeItemFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    space_id: 7,
    kind: 'space_membership',
    payload: {
      title: '空间申请已提交',
      summary: null,
      actor_name: '成员20',
      space_name: '我的家庭',
    },
    domain_status: 'pending',
    action_card: null,
    created_at: '2026-09-01T08:00:00',
    read_at: null,
    ...overrides,
  }
}

function makePageFixture(overrides: Record<string, unknown> = {}) {
  return {
    space_id: 7,
    items: [makeItemFixture()],
    unread_count: 1,
    ...overrides,
  }
}

describe('notifications API（design.md §4.4 合同占位）', () => {
  it('按账号 + space_id 请求约定路径并解码合法列表', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: makePageFixture(), headers: {} })

    const snapshot = await fetchNotifications(7)
    expect(snapshot).toMatchObject({ data: { space_id: 7, unread_count: 1 }, etag: null })
    expect(apiClient.get).toHaveBeenCalledWith(
      '/notifications',
      expect.objectContaining({ params: { space_id: 7 } }),
    )
  })

  it('保留 payload 的 masked 哨兵（不可见 ≠ 明文）', () => {
    const page = decodeNotificationsPage(
      makePageFixture({
        items: [
          makeItemFixture({
            payload: {
              title: '跨空间连接请求',
              summary: { __masked__: true },
              actor_name: { __masked__: true },
              space_name: { __masked__: true },
            },
          }),
        ],
      }),
    )
    expect(page.items[0]?.payload.summary).toEqual({ __masked__: true })
    expect(page.items[0]?.payload.actor_name).toEqual({ __masked__: true })
    expect(page.items[0]?.payload.space_name).toEqual({ __masked__: true })
  })

  it('action_card 通知携带 card_id/revision 引用', () => {
    const page = decodeNotificationsPage(
      makePageFixture({
        items: [
          makeItemFixture({
            kind: 'action_card',
            domain_status: 'pending',
            action_card: { card_id: 55, revision: 2 },
          }),
        ],
      }),
    )
    expect(page.items[0]?.action_card).toEqual({ card_id: 55, revision: 2 })
  })

  it.each([
    ['未知 kind', makeItemFixture({ kind: 'marketing' })],
    ['未知领域状态', makeItemFixture({ domain_status: 'secret' })],
    ['payload 缺 title', makeItemFixture({ payload: { summary: null, actor_name: null, space_name: null } })],
    ['payload 字段既非明文也非 masked', makeItemFixture({ payload: { title: 't', summary: 42, actor_name: null, space_name: null } })],
    ['action_card 通知缺引用', makeItemFixture({ kind: 'action_card', action_card: null })],
    ['action_card 引用损坏', makeItemFixture({ kind: 'action_card', action_card: { card_id: 55 } })],
    ['read_at 类型非法', makeItemFixture({ read_at: 3 })],
    ['缺 created_at', makeItemFixture({ created_at: undefined })],
  ])('坏通知整条丢弃（%s），其余保留', (_reason, badItem) => {
    const page = decodeNotificationsPage(
      makePageFixture({
        items: [makeItemFixture({ id: 2 }), badItem],
        unread_count: 2,
      }),
    )
    expect(page.items.map((item) => item.id)).toEqual([2])
  })

  it('顶层合同破坏时整份拒绝', () => {
    expect(() => decodeNotificationsPage({ space_id: 7, items: 'nope', unread_count: 1 })).toThrow(
      '通知列表响应格式无效',
    )
    expect(() => decodeNotificationsPage({ space_id: 7, items: [] })).toThrow('通知列表响应格式无效')
  })

  it('304 返回 null 并发送 If-None-Match；ETag 写入快照', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ status: 304, data: undefined, headers: {} })
    await expect(fetchNotifications(7, 'W/"n1"')).resolves.toBeNull()
    expect(apiClient.get).toHaveBeenCalledWith(
      '/notifications',
      expect.objectContaining({
        params: { space_id: 7 },
        headers: { 'If-None-Match': 'W/"n1"' },
      }),
    )

    vi.mocked(apiClient.get).mockResolvedValue({
      data: makePageFixture(),
      headers: { etag: 'W/"n2"' },
    })
    await expect(fetchNotifications(7)).resolves.toMatchObject({ etag: 'W/"n2"' })
  })

  it('单条已读：POST /notifications/{id}/read 且解码已读确认', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: { id: 5, read_at: '2026-09-01T09:00:00' },
    })

    await expect(markNotificationRead(5)).resolves.toEqual({
      id: 5,
      read_at: '2026-09-01T09:00:00',
    })
    expect(apiClient.post).toHaveBeenCalledWith('/notifications/5/read')
  })

  it('全部已读：POST /notifications/read-all 携带 space_id', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { space_id: 7, marked_count: 3 } })

    await expect(markAllNotificationsRead(7)).resolves.toEqual({ space_id: 7, marked_count: 3 })
    expect(apiClient.post).toHaveBeenCalledWith('/notifications/read-all', { space_id: 7 })
  })

  it('已读响应格式非法时拒绝', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { id: 5 } })
    await expect(markNotificationRead(5)).rejects.toThrow('通知已读响应格式无效')
  })
})
