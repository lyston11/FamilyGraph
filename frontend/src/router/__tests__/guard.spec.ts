import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as authApi from '@/api/auth'
import type { FactReview, TokenPairResponse } from '@/types/api'

vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  selectCandidate: vi.fn(),
  refreshTokens: vi.fn(),
  logout: vi.fn(),
  fetchMe: vi.fn(),
  changePin: vi.fn(),
  changeName: vi.fn(),
  fetchBootstrapStatus: vi.fn(),
}))

vi.mock('@/api/spaces', () => ({
  fetchSpaces: vi.fn().mockResolvedValue([]),
  createSpace: vi.fn(),
  fetchSpaceMembers: vi.fn().mockResolvedValue([]),
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

vi.mock('@/api/governance', () => ({
  confirmIdentity: vi.fn(),
  fetchFactReviews: vi.fn().mockResolvedValue([]),
  decideFactReview: vi.fn(),
  fetchDataRights: vi.fn().mockResolvedValue([]),
  requestExport: vi.fn(),
  requestCorrection: vi.fn(),
  requestDeletion: vi.fn(),
  executeDelete: vi.fn(),
  downloadExport: vi.fn(),
  raiseClaimDispute: vi.fn(),
  fetchMyClaimDisputes: vi.fn().mockResolvedValue([]),
  withdrawClaimDispute: vi.fn(),
}))

const mockedStatus = vi.mocked(authApi.fetchBootstrapStatus)
const mockedLogin = vi.mocked(authApi.login)
const mockedRefresh = vi.mocked(authApi.refreshTokens)
const mockedFetchMe = vi.mocked(authApi.fetchMe)
const governanceApi = await import('@/api/governance')
const mockedFactReviews = vi.mocked(governanceApi.fetchFactReviews)
const spacesApi = await import('@/api/spaces')
const mockedFetchSpaces = vi.mocked(spacesApi.fetchSpaces)
const mockedFetchSpaceMembers = vi.mocked(spacesApi.fetchSpaceMembers)

// 动态引入真实路由（守卫逻辑是被测对象）；路由为模块单例，跨测试需重置位置
const { default: router } = await import('@/router')
const { useAuthStore } = await import('@/stores/auth')
const { useSpacesStore } = await import('@/stores/spaces')

function makePair(overrides: Partial<TokenPairResponse['user']> = {}): TokenPairResponse {
  return {
    access_token: 'access-abc',
    refresh_token: 'refresh-xyz',
    token_type: 'bearer',
    user: {
      id: 1,
      name: '张三',
      pin_must_change: false,
      claim_status: 'claimed',
      profile_status: 'identity_confirmed',
      ...overrides,
    },
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
    mockedStatus.mockResolvedValue({ initialized: true, registration_enabled: true })
  })

  it('首启未初始化：一律重定向到引导页', async () => {
    mockedStatus.mockResolvedValue({ initialized: false, registration_enabled: true })
    await resetToOnboarding()

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

  it('未注册深链 /admin 与 /system-admin：统一普通 404，不跳转、不提示其他产品面', async () => {
    expect(await navigate('/admin')).toBe('not-found')
    expect(await navigate('/system-admin')).toBe('not-found')
    expect(await navigate('/admin-api/health')).toBe('not-found')
  })

  it('空间管理路由切换不等待成员接口，页面负责完成管理权限加载', async () => {
    const auth = useAuthStore()
    mockedLogin.mockResolvedValue(makePair())
    await auth.login('张三', '123456')

    await resetToOnboarding()
    expect(await navigate('/spaces/7/manage')).toBe('space-management')
    expect(mockedFetchSpaces).not.toHaveBeenCalled()
    expect(mockedFetchSpaceMembers).not.toHaveBeenCalled()
  })

  it('跨空间进入管理页不在守卫阶段切换当前上下文', async () => {
    const auth = useAuthStore()
    mockedLogin.mockResolvedValue(makePair())
    await auth.login('张三', '123456')
    const spaces = useSpacesStore()
    spaces.currentSpaceId = 1

    await resetToOnboarding()
    expect(await navigate('/spaces/7/manage')).toBe('space-management')
    expect(spaces.currentSpaceId).toBe(1)
    expect(mockedFetchSpaceMembers).not.toHaveBeenCalled()
  })

  it('成员也可进入管理页路由，由页面 Bootstrap 返回安全拒绝态', async () => {
    const auth = useAuthStore()
    mockedLogin.mockResolvedValue(makePair())
    await auth.login('张三', '123456')

    await resetToOnboarding()
    expect(await navigate('/spaces/7/manage')).toBe('space-management')
    expect(mockedFetchSpaceMembers).not.toHaveBeenCalled()
  })


  it('未登录空间管理深链保留安全 redirect', async () => {
    useAuthStore().clearSession()
    await resetToOnboarding()
    expect(await navigate('/spaces/7/manage')).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/spaces/7/manage')
  })
  it('refresh 失败：受保护深链进入登录页并清理失效凭据', async () => {
    localStorage.setItem('fg.refresh_token', 'dead-token')
    useAuthStore()
    mockedRefresh.mockRejectedValue(new Error('invalid refresh'))

    expect(await navigate('/settings')).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/settings')
    expect(localStorage.getItem('fg.refresh_token')).toBeNull()
  })

  it('强制改 PIN 重定向保留原始 redirect', async () => {
    const auth = useAuthStore()
    mockedLogin.mockResolvedValue(makePair({ pin_must_change: true }))
    await auth.login('张三', '123456')

    await resetToOnboarding()
    expect(await navigate('/settings')).toBe('force-change-pin')
    expect(router.currentRoute.value.query.redirect).toBe('/settings')
  })
})

describe('router guards: v2 identity setup（F-1，判定源 = /me profile_status）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setActivePinia(createPinia())
    mockedStatus.mockResolvedValue({ initialized: true, registration_enabled: true })
    mockedFactReviews.mockResolvedValue([])
  })

  function makeReview(status: FactReview['status']) {
    return {
      id: 1,
      item_type: 'name',
      item_ref_json: { field: 'name', value: '张三' },
      status,
      decided_at: null,
      created_at: '2026-08-26T00:00:00',
    }
  }

  it('profile_status=provisional：登录后访问主界面被引导到确档向导', async () => {
    const auth = useAuthStore()
    mockedLogin.mockResolvedValue(makePair({ profile_status: 'provisional', claim_status: 'claimed' }))
    await auth.login('张三', '123456')

    expect(await navigate('/settings')).toBe('identity-setup')
    // 向导自身可停留
    expect(await navigate('/identity-setup')).toBe('identity-setup')
  })

  it('profile_status=provisional：确档重定向保留原始 redirect', async () => {
    const auth = useAuthStore()
    mockedLogin.mockResolvedValue(makePair({ profile_status: 'provisional' }))
    await auth.login('张三', '123456')

    await resetToOnboarding()
    expect(await navigate('/stats')).toBe('identity-setup')
    expect(router.currentRoute.value.query.redirect).toBe('/stats')
  })

  it('profile_status=provisional 且清单拉取失败：仍拦截（不再 fail-open）', async () => {
    const auth = useAuthStore()
    mockedLogin.mockResolvedValue(makePair({ profile_status: 'provisional' }))
    await auth.login('张三', '123456')
    mockedFactReviews.mockRejectedValue(new Error('network down'))

    expect(await navigate('/settings')).toBe('identity-setup')
  })

  it('profile_status=identity_confirmed：即使清单仍有 pending 项也不拦截（守卫只看身份状态）', async () => {
    const auth = useAuthStore()
    mockedLogin.mockResolvedValue(makePair({ profile_status: 'identity_confirmed' }))
    await auth.login('张三', '123456')
    mockedFactReviews.mockResolvedValue([makeReview('proposed')])

    expect(await navigate('/settings')).toBe('settings')
  })
})

describe('router guards: forced pin change across hard refresh', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setActivePinia(createPinia())
    mockedStatus.mockResolvedValue({ initialized: true, registration_enabled: true })
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

describe('router guards: 09-01 Phase 2 路由语义（统一家庭壳）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setActivePinia(createPinia())
    mockedStatus.mockResolvedValue({ initialized: true, registration_enabled: true })
  })

  it('旧 /home 显式重定向到 /（name home = 我的家庭）', async () => {
    const auth = useAuthStore()
    mockedLogin.mockResolvedValue(makePair())
    await auth.login('张三', '123456')

    await resetToOnboarding()
    expect(await navigate('/home')).toBe('home')
    expect(router.currentRoute.value.path).toBe('/')
  })

  it('登录默认目标 name home 指向 /（我的家庭）', async () => {
    const auth = useAuthStore()
    mockedLogin.mockResolvedValue(makePair())
    await auth.login('张三', '123456')

    await resetToOnboarding()
    expect(await navigate('/login')).toBe('home')
    expect(router.currentRoute.value.path).toBe('/')
  })

  it('未登录访问新路由（/notifications、/people/5）重定向登录页并保留回跳地址', async () => {
    expect(await navigate('/notifications')).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/notifications')

    expect(await navigate('/people/5')).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/people/5')
  })

  it('未登录访问未注册深链：直接普通 404（公开页，不带家庭壳）', async () => {
    expect(await navigate('/system-admin/login')).toBe('not-found')
  })
})

describe('router guards: /register 自助注册（09-05）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setActivePinia(createPinia())
    mockedStatus.mockResolvedValue({ initialized: true, registration_enabled: true })
  })

  it('注册开关开：guest 可达 /register', async () => {
    await resetToOnboarding()
    expect(await navigate('/register')).toBe('register')
    expect(router.currentRoute.value.path).toBe('/register')
  })

  it('注册开关关：/register 与未注册深链同形呈现 404，URL 保留', async () => {
    mockedStatus.mockResolvedValue({ initialized: true, registration_enabled: false })
    await resetToOnboarding()
    expect(await navigate('/register')).toBe('not-found')
    expect(router.currentRoute.value.path).toBe('/register')
  })

  it('已登录访问 /register：弹回首页（防误覆盖当前会话）', async () => {
    const auth = useAuthStore()
    mockedLogin.mockResolvedValue(makePair())
    await auth.login('张三', '123456')

    await resetToOnboarding()
    expect(await navigate('/register')).toBe('home')
  })

  it('开关关且未初始化：/register 一律先进引导页（首启判定优先）', async () => {
    mockedStatus.mockResolvedValue({ initialized: false, registration_enabled: false })
    expect(await navigate('/register')).toBe('onboarding')
  })
})
