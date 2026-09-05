/**
 * 敏感访问会话 store 测试（PRD FE-F5）：
 * - 票据只存 Pinia 内存，绝不写 localStorage/sessionStorage；
 * - 票据绑定单 user/space 目标，不可跨用；
 * - 30 分钟 TTL：本地过期（带 5 秒余量）自动失效并重新申请；
 * - 403 拒绝后 revoke 清票据，要求重新提交理由。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, adminRequest: vi.fn() }
})

import { adminRequest } from '@/api/client'
import { accessTicketKey, useAdminAccessSessionStore } from '@/stores/accessSession'

const mockedAdminRequest = vi.mocked(adminRequest)

function futureIso(msFromNow = 30 * 60 * 1000): string {
  return new Date(Date.now() + msFromNow).toISOString()
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  sessionStorage.clear()
  mockedAdminRequest.mockReset()
})

describe('adminAccessSessionStore', () => {
  it('提交理由签发票据：只存内存，绝不持久化', async () => {
    mockedAdminRequest.mockResolvedValueOnce({
      session_id: 'opaque-ticket-1',
      target_type: 'user',
      target_id: 1,
      allowed_scopes: ['profile.detail', 'avatar.thumbnail', 'attachment.metadata'],
      issued_at: new Date().toISOString(),
      expires_at: futureIso(),
    })
    const access = useAdminAccessSessionStore()

    const token = await access.ensureTicket('user', 1, '处理异常工单')

    expect(token).toBe('opaque-ticket-1')
    expect(access.peekValid('user', 1)).toBe('opaque-ticket-1')
    // 红线：任何持久化存储中不得出现明文票据
    for (const store of [localStorage, sessionStorage]) {
      for (let i = 0; i < store.length; i += 1) {
        const key = store.key(i) ?? ''
        expect(store.getItem(key)).not.toContain('opaque-ticket')
        void key
      }
      expect(store.length).toBe(0)
    }
  })

  it('有效票据存在时复用，不重复签发', async () => {
    mockedAdminRequest.mockResolvedValueOnce({
      session_id: 'opaque-ticket-1',
      target_type: 'space',
      target_id: 7,
      allowed_scopes: ['relation.detail'],
      issued_at: new Date().toISOString(),
      expires_at: futureIso(),
    })
    const access = useAdminAccessSessionStore()

    const first = await access.ensureTicket('space', 7, '查看空间关系')
    const second = await access.ensureTicket('space', 7, '查看空间关系')

    expect(first).toBe(second)
    expect(mockedAdminRequest).toHaveBeenCalledTimes(1)
  })

  it('票据绑定单目标：user:1 与 space:1、user:2 互不通用', async () => {
    mockedAdminRequest.mockImplementation(async (config) => {
      const payload = config['data'] as { target_type: string; target_id: number }
      return {
        session_id: `ticket-${payload.target_type}-${payload.target_id}`,
        target_type: payload.target_type,
        target_id: payload.target_id,
        allowed_scopes: [],
        issued_at: new Date().toISOString(),
        expires_at: futureIso(),
      }
    })
    const access = useAdminAccessSessionStore()

    await access.ensureTicket('user', 1, 'reason')
    await access.ensureTicket('space', 1, 'reason')
    await access.ensureTicket('user', 2, 'reason')

    expect(access.peekValid('user', 1)).toBe('ticket-user-1')
    expect(access.peekValid('space', 1)).toBe('ticket-space-1')
    expect(access.peekValid('user', 2)).toBe('ticket-user-2')
    expect(access.peekValid('user', 3)).toBeNull()
    expect(mockedAdminRequest).toHaveBeenCalledTimes(3)
  })

  it('过期票据：peekValid 返回 null 并清理；ensureTicket 重新申请', async () => {
    // 过期时间在 4 秒后 → 落入 5 秒本地余量，视为过期
    mockedAdminRequest.mockResolvedValueOnce({
      session_id: 'near-expiry',
      target_type: 'user',
      target_id: 9,
      allowed_scopes: [],
      issued_at: new Date().toISOString(),
      expires_at: futureIso(4000),
    })
    mockedAdminRequest.mockResolvedValueOnce({
      session_id: 'fresh',
      target_type: 'user',
      target_id: 9,
      allowed_scopes: [],
      issued_at: new Date().toISOString(),
      expires_at: futureIso(30 * 60 * 1000),
    })
    const access = useAdminAccessSessionStore()

    await access.ensureTicket('user', 9, 'first')
    expect(access.peekValid('user', 9)).toBeNull() // 过期即丢弃

    const renewed = await access.ensureTicket('user', 9, 'second')
    expect(renewed).toBe('fresh')
    expect(mockedAdminRequest).toHaveBeenCalledTimes(2)
  })

  it('revoke 清掉单目标票据；clearAll 清空全部内存票据', async () => {
    mockedAdminRequest.mockImplementation(async (config) => {
      const payload = config['data'] as { target_type: string; target_id: number }
      return {
        session_id: `ticket-${payload.target_type}-${payload.target_id}`,
        target_type: payload.target_type,
        target_id: payload.target_id,
        allowed_scopes: [],
        issued_at: new Date().toISOString(),
        expires_at: futureIso(),
      }
    })
    const access = useAdminAccessSessionStore()

    await access.ensureTicket('user', 1, 'reason')
    await access.ensureTicket('space', 2, 'reason')

    access.revoke('user', 1)
    expect(access.peekValid('user', 1)).toBeNull()
    expect(access.peekValid('space', 2)).not.toBeNull()
    expect(access.tickets.has(accessTicketKey('user', 1))).toBe(false)

    access.clearAll()
    expect(access.tickets.size).toBe(0)
  })

  it('签发失败不落任何票据', async () => {
    mockedAdminRequest.mockRejectedValueOnce(
      new (await import('@/api/client')).AdminApiError(404, 'ADMIN_TARGET_NOT_FOUND', '目标不存在或不可访问'),
    )
    const access = useAdminAccessSessionStore()

    await expect(access.ensureTicket('user', 1, 'reason')).rejects.toThrow()
    expect(access.peekValid('user', 1)).toBeNull()
    expect(access.tickets.size).toBe(0)
  })
})
