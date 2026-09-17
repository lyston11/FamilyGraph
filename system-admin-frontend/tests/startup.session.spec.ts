/**
 * 应用启动会话恢复的接线回归（PRD AC1）。
 *
 * 复现场景：硬刷新受保护页面时，`main.ts` 先发起恢复（refresh 轮换尚未返回），
 * 首次路由导航随即进入守卫。
 * - 旧行为：守卫看到 `restoring === true` 直接跳过等待 → 误判未登录 → 跳
 *   /login，且恢复成功后不再重算目标路由（用户落在登录页）。
 * - 期望行为：守卫等待在途恢复；成功留在原深链，失败落登录页；应用在首次
 *   导航解析完成（route.meta 已就绪）之后才挂载，不会因空 meta 误判壳层。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, adminRequest: vi.fn() }
})

// 壳层与登录页用最小 stub：既能断言挂载时机，又避免视图层重组件副作用。
vi.mock('@/components/AdminShell.vue', () => ({
  default: { template: '<div data-testid="admin-shell-stub" />' },
}))
vi.mock('@/views/AdminLoginView.vue', () => ({
  default: { template: '<div data-testid="admin-login-stub" />' },
}))

import { adminRequest } from '@/api/client'
import { ADMIN_REFRESH_TOKEN_STORAGE_KEY } from '@/stores/auth'

const mockedAdminRequest = vi.mocked(adminRequest)

const PAIR = {
  access_token: 'access-1',
  refresh_token: 'refresh-1',
  token_type: 'bearer',
  admin: { id: 1, username: 'admin', password_must_change: false, status: 'claimed' as const },
}

/** 在途恢复的受控 resolve/reject 句柄。 */
let settleRefresh: { resolve: (value: unknown) => void; reject: (error: unknown) => void } | null

function installDelayedRefresh(): void {
  settleRefresh = null
  mockedAdminRequest.mockImplementationOnce(
    () =>
      new Promise((resolve, reject) => {
        settleRefresh = { resolve, reject }
      }),
  )
}

/**
 * 等待已排队的微任务/挂载副作用跑完。次数远大于懒加载路由组件解析所需的
 * 微任务数，确保「旧行为」的守卫链已经落定后再断言。
 */
async function flush(): Promise<void> {
  for (let i = 0; i < 80; i += 1) {
    await Promise.resolve()
  }
}

beforeEach(() => {
  vi.resetModules()
  localStorage.clear()
  mockedAdminRequest.mockReset()
  document.body.innerHTML = '<div id="app"></div>'
  window.history.replaceState({}, '', '/operations')
})

afterEach(() => {
  vi.resetModules()
})

describe('管理员应用启动恢复（main.ts 接线）', () => {
  it('恢复未完成时不挂载；成功后留在原深链并渲染受保护壳层', async () => {
    localStorage.setItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY, 'refresh-1')
    installDelayedRefresh()

    const { adminRouter } = await import('@/router')
    await import('@/main')
    await flush()

    // 恢复在途：首次导航未落定，应用未挂载（不得先渲染/落到登录页）
    expect(adminRouter.currentRoute.value.name).toBeUndefined()
    expect(document.querySelector('[data-testid="admin-shell-stub"]')).toBeNull()
    expect(document.querySelector('[data-testid="admin-login-stub"]')).toBeNull()

    settleRefresh?.resolve(PAIR)
    await adminRouter.isReady()
    await flush()

    expect(adminRouter.currentRoute.value.name).toBe('operations')
    expect(adminRouter.currentRoute.value.fullPath).toBe('/operations')
    // route.meta 已解析（requiresAuth）才会渲染后台壳层
    expect(document.querySelector('[data-testid="admin-shell-stub"]')).not.toBeNull()
    expect(document.querySelector('[data-testid="admin-login-stub"]')).toBeNull()
  })

  it('恢复失败时挂载登录页且清空 refresh key', async () => {
    localStorage.setItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY, 'expired-refresh')
    installDelayedRefresh()

    const { adminRouter } = await import('@/router')
    await import('@/main')
    await flush()

    expect(adminRouter.currentRoute.value.name).toBeUndefined()

    settleRefresh?.reject({ response: { status: 401 } })
    await adminRouter.isReady()
    await flush()

    expect(adminRouter.currentRoute.value.name).toBe('login')
    expect(document.querySelector('[data-testid="admin-login-stub"]')).not.toBeNull()
    expect(document.querySelector('[data-testid="admin-shell-stub"]')).toBeNull()
    expect(localStorage.getItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY)).toBeNull()
  })

  it('首登强制改密会话：恢复成功后门禁把深链改派到改密页', async () => {
    localStorage.setItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY, 'refresh-1')
    installDelayedRefresh()

    const { adminRouter } = await import('@/router')
    await import('@/main')
    await flush()

    settleRefresh?.resolve({
      ...PAIR,
      admin: { ...PAIR.admin, password_must_change: true },
    })
    await adminRouter.isReady()
    await flush()

    expect(adminRouter.currentRoute.value.name).toBe('force-change-password')
  })

  it('首次导航失败时仍挂载应用（isReady 拒绝不得让应用停在未挂载状态）', async () => {
    // 部署后懒加载 chunk 失效等会让首次导航 reject，isReady() 随之 reject。
    // 应用必须仍然挂载（旧实现挂载在 restoreSession 的 finally 里，任何首次
    // 导航结果都会挂载）；否则应用永久停在未挂载状态。
    const { adminRouter } = await import('@/router')
    adminRouter.beforeEach(() => {
      throw new Error('Failed to fetch dynamically imported module')
    })

    await import('@/main')
    await flush()
    await flush()

    // 导航已失败但仍完成挂载（非空壳层容器）
    expect(document.querySelector('#app')?.innerHTML).not.toBe('')
  })

  it('启动恢复与守卫共用一笔轮换：整条启动链只请求一次 /auth/refresh', async () => {
    localStorage.setItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY, 'refresh-1')
    installDelayedRefresh()

    const { adminRouter } = await import('@/router')
    await import('@/main')
    await flush()

    settleRefresh?.resolve(PAIR)
    await adminRouter.isReady()
    await flush()

    const refreshCalls = mockedAdminRequest.mock.calls.filter(
      (call) => (call[0] as Record<string, unknown>)['url'] === '/auth/refresh',
    )
    expect(refreshCalls).toHaveLength(1)
  })
})
