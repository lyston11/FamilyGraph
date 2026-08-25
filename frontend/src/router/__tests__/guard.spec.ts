import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as authApi from '@/api/auth'
import type { TokenPairResponse } from '@/types/api'

vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  selectCandidate: vi.fn(),
  refreshTokens: vi.fn(),
  logout: vi.fn(),
  fetchMe: vi.fn(),
  changePin: vi.fn(),
  changeName: vi.fn(),
  fetchBootstrapStatus: vi.fn(),
  initializeAdmin: vi.fn(),
}))

const mockedStatus = vi.mocked(authApi.fetchBootstrapStatus)
const mockedLogin = vi.mocked(authApi.login)
const mockedRefresh = vi.mocked(authApi.refreshTokens)
const mockedFetchMe = vi.mocked(authApi.fetchMe)

// 动态引入真实路由（守卫逻辑是被测对象）；路由为模块单例，跨测试需重置位置
const { default: router } = await import('@/router')
const { useAuthStore } = await import('@/stores/auth')

function makePair(overrides: Partial<TokenPairResponse['user']> = {}): TokenPairResponse {
  return {
    access_token: 'access-abc',
    refresh_token: 'refresh-xyz',
    token_type: 'bearer',
    user: { id: 1, name: '张三', is_admin: false, pin_must_change: false, ...overrides },
  }
}

async function navigate(path: string): Promise<string> {
  await router.push(path)
  await router.isReady()
  return String(router.currentRoute.value.name)
}

/** 回到公开中立路由，避免重复导航短路守卫 */
async function resetToOnboarding(): Promise<void> {
  if (router.currentRoute.value.name !== 'onboarding') {
    await router.push('/onboarding')
    await router.isReady()
  }
}

describe('router guards', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setActivePinia(createPinia())
    mockedStatus.mockResolvedValue({ initialized: true })
  })

  it('首启未初始化：一律重定向到引导页', async () => {
    mockedStatus.mockResolvedValue({ initialized: false })

    expect(await navigate('/')).toBe('onboarding')
    expect(await navigate('/login')).toBe('onboarding')
    expect(await navigate('/settings')).toBe('onboarding')
  })

  it('未登录访问受保护页：重定向登录页并携带回跳地址', async () => {
    const name = await navigate('/settings')

    expect(name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/settings')
  })

  it('已登录正常用户可停留在设置页；访问登录页被弹回首页', async () => {
    const auth = useAuthStore()
    mockedLogin.mockResolvedValue(makePair({ pin_must_change: false }))
    await auth.login('张三', '123456')

    expect(await navigate('/settings')).toBe('settings')
    expect(await navigate('/login')).toBe('home')
  })

  it('pin_must_change=true：白名单外强制跳改 PIN 页', async () => {
    const auth = useAuthStore()
    mockedLogin.mockResolvedValue(makePair({ pin_must_change: true }))
    await auth.login('张三', '123456')

    await resetToOnboarding()
    expect(await navigate('/')).toBe('force-change-pin')

    await resetToOnboarding()
    expect(await navigate('/settings')).toBe('force-change-pin')
  })

  it('pin_must_change=true：仍可停留在改 PIN 页本身', async () => {
    const auth = useAuthStore()
    mockedLogin.mockResolvedValue(makePair({ pin_must_change: true }))
    await auth.login('张三', '123456')

    expect(await navigate('/force-change-pin')).toBe('force-change-pin')
  })

  it('硬刷新恢复会话：有 refresh token 时经 resume 恢复后放行', async () => {
    // 模拟内存丢失但 localStorage 仍有凭据：必须在 store 首次实例化前写入
    localStorage.setItem('fg.refresh_token', 'stored-refresh')
    useAuthStore()

    const pair = makePair()
    mockedRefresh.mockResolvedValue(pair)
    mockedFetchMe.mockResolvedValue(pair.user)

    expect(await navigate('/settings')).toBe('settings')
    expect(useAuthStore().isLoggedIn).toBe(true)
  })

  it('resume 失败（refresh 已失效）：清空状态跳登录页', async () => {
    localStorage.setItem('fg.refresh_token', 'dead-token')
    useAuthStore()

    mockedRefresh.mockRejectedValue(new Error('invalid refresh'))

    await resetToOnboarding()
    expect(await navigate('/settings')).toBe('login')
    expect(localStorage.getItem('fg.refresh_token')).toBeNull()
  })
})

describe('router guards: forced pin change across hard refresh', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setActivePinia(createPinia())
    mockedStatus.mockResolvedValue({ initialized: true })
  })

  it('强制改 PIN 用户硬刷新：经 resume 恢复后仍被守卫送回改 PIN 页，不调 fetchMe', async () => {
    localStorage.setItem('fg.refresh_token', 'stored-refresh')
    useAuthStore()

    mockedRefresh.mockResolvedValue(makePair({ pin_must_change: true }))

    await resetToOnboarding()
    expect(await navigate('/settings')).toBe('force-change-pin')
    expect(mockedFetchMe).not.toHaveBeenCalled()
  })
})
