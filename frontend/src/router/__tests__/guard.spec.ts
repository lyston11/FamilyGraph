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
  initializeAdmin: vi.fn(),
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

function makePair(overrides: Partial<TokenPairResponse['user']> = {}): TokenPairResponse {
  return {
    access_token: 'access-abc',
    refresh_token: 'refresh-xyz',
    token_type: 'bearer',
    user: {
      id: 1,
      name: '张三',
      is_admin: false,
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

  it('系统管理员硬刷新 /system-admin：不调用家庭 /me，会话保留并放行', async () => {
    localStorage.setItem('fg.refresh_token', 'stored-refresh')
    useAuthStore()

    const pair = makePair({ is_admin: true })
    pair.user.principal_type = 'system_admin'
    mockedRefresh.mockResolvedValue(pair)
    // 家庭 /me 对系统主体按设计 401；resume 一旦调用它就会清会话把人踢回登录页。
    mockedFetchMe.mockRejectedValue(new Error('family endpoint rejects system admin'))

    expect(await navigate('/system-admin')).toBe('system-admin')
    expect(mockedRefresh).toHaveBeenCalledWith('stored-refresh')
    expect(mockedFetchMe).not.toHaveBeenCalled()
    expect(useAuthStore().isSystemAdmin).toBe(true)
    expect(useAuthStore().isLoggedIn).toBe(true)
  })

  it('旧 /admin 深链在系统管理员硬刷新后重定向到独立后台', async () => {
    localStorage.setItem('fg.refresh_token', 'stored-refresh')
    useAuthStore()

    const pair = makePair({ is_admin: true })
    pair.user.principal_type = 'system_admin'
    mockedRefresh.mockResolvedValue(pair)
    mockedFetchMe.mockRejectedValue(new Error('family endpoint rejects system admin'))

    expect(await navigate('/admin')).toBe('system-admin')
  })

  it('普通用户访问 /admin：权限校验后返回家庭空间', async () => {
    const auth = useAuthStore()
    mockedLogin.mockResolvedValue(makePair({ is_admin: false }))
    await auth.login('张三', '123456')

    await resetToOnboarding()
    expect(await navigate('/admin')).toBe('family-space')
  })

  it('平台运营者不因平台角色获得空间管理权限', async () => {
    const auth = useAuthStore()
    mockedLogin.mockResolvedValue(makePair({ is_admin: true }))
    await auth.login('张三', '123456')
    mockedFetchSpaces.mockResolvedValue([
      {
        id: 7,
        name: '他人空间',
        owner_id: 99,
        kind: 'household',
        created_at: '2026-08-25T00:00:00',
        pending_count: 0,
        member_count: 1,
      },
    ])
    mockedFetchSpaceMembers.mockResolvedValue([])

    await resetToOnboarding()
    expect(await navigate('/spaces/7/manage')).toBe('family-space')
  })

  it('当前空间管理员可访问目标空间管理页', async () => {
    const auth = useAuthStore()
    mockedLogin.mockResolvedValue(makePair())
    await auth.login('张三', '123456')
    mockedFetchSpaces.mockResolvedValue([
      {
        id: 7,
        name: '我的空间',
        owner_id: 1,
        kind: 'household',
        created_at: '2026-08-25T00:00:00',
        pending_count: 0,
        member_count: 2,
      },
    ])
    mockedFetchSpaceMembers.mockResolvedValue([
      {
        id: 1,
        space_id: 7,
        user_id: 1,
        added_by: 1,
        role: 'space_admin',
        status: 'active',
        updated_at: '2026-08-25T00:00:00',
      },
    ])

    await resetToOnboarding()
    expect(await navigate('/spaces/7/manage')).toBe('space-management')
  })

  it('member、guest 和无 membership 拒绝进入空间管理页', async () => {
    const roles = ['member', 'guest'] as const
    for (const role of roles) {
      const auth = useAuthStore()
      mockedLogin.mockResolvedValue(makePair())
      await auth.login('张三', '123456')
      mockedFetchSpaces.mockResolvedValue([
        {
          id: 7,
          name: '成员空间',
          owner_id: 99,
          kind: 'household',
          created_at: '2026-08-25T00:00:00',
          pending_count: 0,
          member_count: 2,
        },
      ])
      mockedFetchSpaceMembers.mockResolvedValue([
        {
          id: 1,
          space_id: 7,
          user_id: 1,
          added_by: 99,
          role,
          status: 'active',
          updated_at: '2026-08-25T00:00:00',
        },
      ])
      await resetToOnboarding()
      expect(await navigate('/spaces/7/manage')).toBe('family-space')
      auth.clearSession()
      await resetToOnboarding()
    }
  })

  it('未登录空间管理深链保留安全 redirect', async () => {
    expect(await navigate('/spaces/7/manage')).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/spaces/7/manage')
  })
  it('无凭据访问 /admin：进入登录页并保留独立后台 redirect', async () => {
    // /admin 只是旧路径重定向；守卫看到的目标已是 /system-admin，回跳地址同此。
    expect(await navigate('/admin')).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/system-admin')
  })

  it('refresh 失败：后台深链进入登录页并清理失效凭据', async () => {
    localStorage.setItem('fg.refresh_token', 'dead-token')
    useAuthStore()
    mockedRefresh.mockRejectedValue(new Error('invalid refresh'))

    expect(await navigate('/admin')).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/system-admin')
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
    mockedStatus.mockResolvedValue({ initialized: true })
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
