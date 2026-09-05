/**
 * admin auth store 测试：
 * - access token 只存内存（绝不进 localStorage/sessionStorage）；
 * - refresh token 使用独立 key fg.admin.refresh_token，绝不写家庭 fg.refresh_token；
 * - 登录响应硬校验 admin 主体字段（缺失/类型错 → 拒绝写会话）；
 * - logout / changePassword / changeUsername / refresh 失败均清空全部会话态。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { AdminApiError } from '@/api/client'
import { ADMIN_REFRESH_TOKEN_STORAGE_KEY, useAdminAuthStore } from '@/stores/auth'
import { useAdminAccessSessionStore } from '@/stores/accessSession'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return {
    ...actual,
    adminRequest: vi.fn(),
  }
})

import { adminRequest } from '@/api/client'
import type { AdminTokenPairResponse } from '@/types/api'

const mockedAdminRequest = vi.mocked(adminRequest)

function tokenPair(overrides: Partial<AdminTokenPairResponse> = {}): AdminTokenPairResponse {
  return {
    access_token: 'access-1',
    refresh_token: 'refresh-1',
    token_type: 'bearer',
    admin: {
      id: 1,
      username: 'admin',
      password_must_change: false,
      status: 'claimed',
    },
    ...overrides,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  sessionStorage.clear()
  mockedAdminRequest.mockReset()
})

describe('adminAuthStore', () => {
  it('login 成功：access token 只在内存，refresh token 用独立 key', async () => {
    mockedAdminRequest.mockResolvedValueOnce(tokenPair())
    const auth = useAdminAuthStore()

    await auth.login('admin', 'Sup3rSecret!pass')

    expect(auth.isLoggedIn).toBe(true)
    expect(auth.accessToken).toBe('access-1')
    expect(auth.mustChangePassword).toBe(false)
    // 独立 key 命名空间
    expect(localStorage.getItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY)).toBe('refresh-1')
    // access token 绝不持久化
    for (const store of [localStorage, sessionStorage]) {
      for (let i = 0; i < store.length; i += 1) {
        const key = store.key(i) ?? ''
        expect(store.getItem(key)).not.toBe('access-1')
      }
    }
    // 家庭 key 绝不出现
    expect(localStorage.getItem('fg.refresh_token')).toBeNull()
  })

  it('login 响应 admin 字段缺失：硬校验拒绝且不写任何会话', async () => {
    // 模拟合同破坏：admin 缺 password_must_change
    mockedAdminRequest.mockResolvedValueOnce(
      tokenPair({ admin: { id: 1, username: 'admin', status: 'claimed' } as never }),
    )
    const auth = useAdminAuthStore()

    await expect(auth.login('admin', 'Sup3rSecret!pass')).rejects.toThrow()
    expect(auth.isLoggedIn).toBe(false)
    expect(auth.accessToken).toBeNull()
    expect(localStorage.getItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY)).toBeNull()
  })

  it('password_must_change=true 的登录会话进入强制改密态', async () => {
    mockedAdminRequest.mockResolvedValueOnce(tokenPair({ admin: {
      id: 1,
      username: 'admin',
      password_must_change: true,
      status: 'managed',
    } }))
    const auth = useAdminAuthStore()

    const mustChange = await auth.login('admin', 'Initial!Password')
    expect(mustChange).toBe(true)
    expect(auth.mustChangePassword).toBe(true)
  })

  it('tryRefresh 失败：清空内存与独立 refresh key，不留家庭 key', async () => {
    localStorage.setItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY, 'stale-refresh')
    mockedAdminRequest.mockRejectedValueOnce(
      new AdminApiError(401, 'ADMIN_UNAUTHORIZED', '管理员认证失败，请重新登录'),
    )
    const auth = useAdminAuthStore()

    const ok = await auth.tryRefresh()
    expect(ok).toBe(false)
    expect(auth.isLoggedIn).toBe(false)
    expect(localStorage.getItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY)).toBeNull()
    expect(localStorage.getItem('fg.refresh_token')).toBeNull()
  })

  it('logout：撤销远端会话并清空 store、refresh key 与内存票据', async () => {
    mockedAdminRequest.mockResolvedValueOnce(tokenPair())
    const auth = useAdminAuthStore()
    await auth.login('admin', 'Sup3rSecret!pass')
    useAdminAccessSessionStore().tickets.set('user:1', {
      token: 'ticket',
      expiresAt: Date.now() + 1000,
    })

    mockedAdminRequest.mockResolvedValueOnce({ success: true })
    await auth.logout()

    expect(mockedAdminRequest).toHaveBeenLastCalledWith(
      expect.objectContaining({ method: 'post', url: '/auth/logout' }),
    )
    expect(auth.isLoggedIn).toBe(false)
    expect(localStorage.getItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY)).toBeNull()
    expect(useAdminAccessSessionStore().tickets.size).toBe(0)
  })

  it('changePassword 成功后本地会话清空（后端已撤销全部会话）', async () => {
    mockedAdminRequest.mockResolvedValueOnce(tokenPair())
    const auth = useAdminAuthStore()
    await auth.login('admin', 'Sup3rSecret!pass')

    mockedAdminRequest.mockResolvedValueOnce({
      id: 1,
      username: 'admin',
      password_must_change: false,
      status: 'claimed',
    })
    await auth.changePassword('Sup3rSecret!pass', 'NewPassw0rd!xyz')

    expect(auth.isLoggedIn).toBe(false)
    expect(auth.accessToken).toBeNull()
    expect(localStorage.getItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY)).toBeNull()
  })

  it('restoreSession 无存储 token 时返回 false 且零请求', async () => {
    const auth = useAdminAuthStore()
    const ok = await auth.restoreSession()
    expect(ok).toBe(false)
    expect(mockedAdminRequest).not.toHaveBeenCalled()
  })

  it('restoreSession 有独立 refresh token 时恢复会话并轮换', async () => {
    localStorage.setItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY, 'stored-refresh')
    mockedAdminRequest.mockResolvedValueOnce(tokenPair({ access_token: 'access-2', refresh_token: 'refresh-2' }))
    const auth = useAdminAuthStore()

    const ok = await auth.restoreSession()
    expect(ok).toBe(true)
    expect(mockedAdminRequest).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'post',
        url: '/auth/refresh',
        data: { refresh_token: 'stored-refresh' },
      }),
    )
    expect(auth.accessToken).toBe('access-2')
    expect(localStorage.getItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY)).toBe('refresh-2')
  })

  it('并发 tryRefresh 单飞：同份旧 refresh token 只发一次轮换请求', async () => {
    localStorage.setItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY, 'stale-refresh')
    let resolveRefresh: ((value: AdminTokenPairResponse) => void) | null = null
    mockedAdminRequest.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveRefresh = resolve
        }),
    )
    const auth = useAdminAuthStore()

    const first = auth.tryRefresh()
    const second = auth.tryRefresh()
    // TS 不追踪 Promise executor 回调内的赋值，调用点需显式还原声明类型
    ;(resolveRefresh as ((value: AdminTokenPairResponse) => void) | null)?.(
      tokenPair({ access_token: 'access-2', refresh_token: 'refresh-2' }),
    )
    expect(await first).toBe(true)
    expect(await second).toBe(true)

    // 两笔并发 401 只允许一次轮换：第二笔重复使用旧 token 会被后端判为
    // 重放攻击并撤销全部会话（单飞防线回归测试）
    const refreshCalls = mockedAdminRequest.mock.calls.filter(
      (call) => (call[0] as Record<string, unknown>)['url'] === '/auth/refresh',
    )
    expect(refreshCalls).toHaveLength(1)
    expect(auth.accessToken).toBe('access-2')
  })
})
