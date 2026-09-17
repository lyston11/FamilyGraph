/**
 * admin router guard 测试（design §2 / PRD FE-F2）：
 * - 未登录访问业务页 → /login（带 redirect query）；
 * - 未登录访问登录页 → 放行；
 * - 已登录 + password_must_change → 强制改密页之外一律拦截；
 * - 已登录访问登录页/强制改密页 → 回概览；
 * - session expired（清会话）后回 /login，绝不出现家庭路径；
 * - redirect 只接受内部路径（resolveSafeRedirect 白名单）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, adminRequest: vi.fn() }
})

import { adminRequest } from '@/api/client'
import { createAdminRouter, setupAdminRouterGuards } from '@/router'
import { resolveSafeRedirect } from '@/router/safeRedirect'
import { ADMIN_REFRESH_TOKEN_STORAGE_KEY, useAdminAuthStore } from '@/stores/auth'

const mockedAdminRequest = vi.mocked(adminRequest)

const PAIR = {
  access_token: 'access-1',
  refresh_token: 'refresh-1',
  token_type: 'bearer',
  admin: { id: 1, username: 'admin', password_must_change: false, status: 'claimed' as const },
}

async function loginAs(passwordMustChange: boolean): Promise<void> {
  mockedAdminRequest.mockResolvedValueOnce({
    ...PAIR,
    admin: { ...PAIR.admin, password_must_change: passwordMustChange },
  })
  const auth = useAdminAuthStore()
  await auth.login('admin', 'Sup3rSecret!pass')
}

/** 让已排队的微任务（含懒加载路由组件的解析链）跑完。 */
async function flushMicrotasks(): Promise<void> {
  for (let i = 0; i < 40; i += 1) {
    await Promise.resolve()
  }
}

/**
 * 探针守卫先于真实守卫注册：真实守卫在等恢复时不会执行完，探针仍能先给出
 * 「导航已开始」信号，用于断言恢复在途时导航未落定（不改导航结果）。
 */
function guardEnteredSignal(): { entered: Promise<void>; markEntered: () => void } {
  let markEntered: () => void = () => undefined
  const entered = new Promise<void>((resolve) => {
    markEntered = resolve
  })
  return { entered, markEntered: () => markEntered() }
}

/** 在途恢复响应的手动控制句柄（模拟未立即 resolve 的 refresh）。 */
function deferredRefresh(): {
  pending: () => Promise<unknown>
  resolve: (value: unknown) => void
  reject: (error: unknown) => void
} {
  let resolvePending: ((value: unknown) => void) | null = null
  let rejectPending: ((error: unknown) => void) | null = null
  return {
    pending: () =>
      new Promise((resolve, reject) => {
        resolvePending = resolve
        rejectPending = reject
      }),
    resolve: (value) => resolvePending?.(value),
    reject: (error) => rejectPending?.(error),
  }
}

function freshRouter(onGuardEntered?: () => void) {
  const router = createAdminRouter()
  if (onGuardEntered) {
    router.beforeEach(() => {
      onGuardEntered()
      return true
    })
  }
  setupAdminRouterGuards(router)
  return router
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  mockedAdminRequest.mockReset()
})

describe('admin router guard', () => {
  it('未登录访问业务页 → 登录页并携带内部 redirect', async () => {
    const router = freshRouter()
    await router.push('/spaces/3')
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query['redirect']).toBe('/spaces/3')
  })

  it('未登录访问登录页 → 放行', async () => {
    const router = freshRouter()
    await router.push('/login')
    expect(router.currentRoute.value.name).toBe('login')
  })

  it('已登录普通会话访问业务页 → 放行', async () => {
    await loginAs(false)
    const router = freshRouter()
    await router.push('/')
    expect(router.currentRoute.value.name).toBe('overview')
  })

  it('password_must_change 会话访问业务页 → 强制改密页', async () => {
    await loginAs(true)
    const router = freshRouter()
    await router.push('/operations')
    expect(router.currentRoute.value.name).toBe('force-change-password')
  })

  it('password_must_change 会话访问强制改密页 → 放行', async () => {
    await loginAs(true)
    const router = freshRouter()
    await router.push('/force-change-password')
    expect(router.currentRoute.value.name).toBe('force-change-password')
  })

  it('已改密会话访问强制改密页 → 回概览', async () => {
    await loginAs(false)
    const router = freshRouter()
    await router.push('/force-change-password')
    expect(router.currentRoute.value.name).toBe('overview')
  })

  it('已登录访问登录页 → 回概览', async () => {
    await loginAs(false)
    const router = freshRouter()
    await router.push('/login')
    expect(router.currentRoute.value.name).toBe('overview')
  })

  it('session expired（clearSession）后访问业务页 → 登录页，绝无家庭路径', async () => {
    await loginAs(false)
    const auth = useAdminAuthStore()
    const router = freshRouter()
    await router.push('/')

    auth.clearSession()
    localStorage.removeItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY)
    await router.push('/agent')
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.path).toBe('/login')
  })

  it('未知路径（已登录）→ NotFound 安全空态', async () => {
    await loginAs(false)
    const router = freshRouter()
    await router.push('/totally-unknown/path')
    expect(router.currentRoute.value.name).toBe('not-found')
  })

  it('刷新页面后从 localStorage 恢复会话并放行业务页', async () => {
    // 模拟已登录后刷新页面的场景：内存为空，但 localStorage 有 refresh token
    localStorage.setItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY, 'refresh-1')
    mockedAdminRequest.mockResolvedValueOnce(PAIR)

    const router = freshRouter()
    await router.push('/operations')

    // 应该成功恢复会话并访问业务页，而不是跳转到登录页
    expect(router.currentRoute.value.name).toBe('operations')
    expect(mockedAdminRequest).toHaveBeenCalledWith({
      method: 'post',
      url: '/auth/refresh',
      data: { refresh_token: 'refresh-1' },
    })
  })

  it('刷新页面但 refresh token 已失效时跳转登录页', async () => {
    localStorage.setItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY, 'expired-refresh')
    mockedAdminRequest.mockRejectedValueOnce({ response: { status: 401 } })

    const router = freshRouter()
    await router.push('/operations')

    // refresh 失败后应该清空会话并跳转到登录页
    expect(router.currentRoute.value.name).toBe('login')
    expect(localStorage.getItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY)).toBeNull()
  })

  it('启动恢复未完成时导航受保护页：等待在途恢复并留在原深链', async () => {
    // 硬刷新场景：main.ts 先发起 restoreSession（未立即完成），
    // 随后首次导航到达守卫。守卫必须等待在途恢复，而不是跳过等待后
    // 把未登录态重定向到 /login（恢复成功也不再重算目标路由）。
    localStorage.setItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY, 'refresh-1')
    const deferred = deferredRefresh()
    mockedAdminRequest.mockImplementationOnce(deferred.pending)

    const auth = useAdminAuthStore()
    const restore = auth.restoreSession()
    const signal = guardEnteredSignal()
    const router = freshRouter(signal.markEntered)
    const navigation = router.push('/spaces/3')

    // 守卫已在跑、恢复仍在途：导航必须尚未落定（不得先跳登录页）
    await signal.entered
    await flushMicrotasks()
    expect(router.currentRoute.value.name).toBeUndefined()

    deferred.resolve(PAIR)
    expect(await restore).toBe(true)
    await navigation

    expect(router.currentRoute.value.name).toBe('space-detail')
    expect(router.currentRoute.value.fullPath).toBe('/spaces/3')
    expect(auth.isLoggedIn).toBe(true)
  })

  it('启动恢复在途且失败：导航落到登录页', async () => {
    localStorage.setItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY, 'refresh-1')
    const deferred = deferredRefresh()
    mockedAdminRequest.mockImplementationOnce(deferred.pending)

    const auth = useAdminAuthStore()
    const restore = auth.restoreSession()
    const signal = guardEnteredSignal()
    const router = freshRouter(signal.markEntered)
    const navigation = router.push('/operations')

    await signal.entered
    await flushMicrotasks()
    expect(router.currentRoute.value.name).toBeUndefined()

    deferred.reject({ response: { status: 401 } })
    expect(await restore).toBe(false)
    await navigation

    expect(router.currentRoute.value.name).toBe('login')
    expect(localStorage.getItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY)).toBeNull()
  })

  it('启动恢复在途且首改密：导航仍被强制改密门禁拦截', async () => {
    localStorage.setItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY, 'refresh-1')
    const deferred = deferredRefresh()
    mockedAdminRequest.mockImplementationOnce(deferred.pending)

    const auth = useAdminAuthStore()
    const restore = auth.restoreSession()
    const signal = guardEnteredSignal()
    const router = freshRouter(signal.markEntered)
    const navigation = router.push('/operations')

    await signal.entered
    await flushMicrotasks()
    expect(router.currentRoute.value.name).toBeUndefined()

    deferred.resolve({ ...PAIR, admin: { ...PAIR.admin, password_must_change: true } })
    expect(await restore).toBe(true)
    await navigation

    expect(router.currentRoute.value.name).toBe('force-change-password')
  })

  it('启动恢复在途：并发导航只触发一笔轮换请求', async () => {
    localStorage.setItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY, 'refresh-1')
    const deferred = deferredRefresh()
    mockedAdminRequest.mockImplementationOnce(deferred.pending)

    const auth = useAdminAuthStore()
    const restore = auth.restoreSession()
    const signal = guardEnteredSignal()
    const router = freshRouter(signal.markEntered)
    const navigation = router.push('/operations')

    await signal.entered
    deferred.resolve(PAIR)
    expect(await restore).toBe(true)
    await navigation

    expect(router.currentRoute.value.name).toBe('operations')
    const refreshCalls = mockedAdminRequest.mock.calls.filter(
      (call) => (call[0] as Record<string, unknown>)['url'] === '/auth/refresh',
    )
    expect(refreshCalls).toHaveLength(1)
  })

  it('redirect 白名单：拒绝外部/协议相对/家庭路径', () => {
    expect(resolveSafeRedirect('/spaces/1')).toBe('/spaces/1')
    expect(resolveSafeRedirect('https://evil.example.com/x')).toBeNull()
    expect(resolveSafeRedirect('//evil.example.com')).toBeNull()
    expect(resolveSafeRedirect('http://localhost:5174/login')).toBeNull()
    expect(resolveSafeRedirect('/login')).toBeNull()
    expect(resolveSafeRedirect('spaces/1')).toBeNull()
    expect(resolveSafeRedirect(undefined)).toBeNull()
  })
})
