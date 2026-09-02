import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as authApi from '@/api/auth'
import type { TokenPairResponse } from '@/types/api'

/**
 * auth store（09-01 Phase 6 审计结论落测试）：
 * - isSystemAdmin 判定唯一来源 = 服务端签名会话返回的
 *   `principal_type === 'system_admin'`；`is_admin`（v2 兼容显示字段）与
 *   `platform_role`（家庭端平台运营标记）都不参与主体互斥判定
 *   （architecture.md §0.8：JWT 必须携带 principal_type）；
 * - 主体互斥的会话切换：system_admin 主体登录时家庭敏感 stores 全量清空，
 *   family_user 登录不做家庭缓存清理（清理点 = 登出/401/token 失效 + 系统主体换入）。
 */

vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  selectCandidate: vi.fn(),
  refreshTokens: vi.fn(),
  logout: vi.fn().mockResolvedValue(undefined),
  fetchMe: vi.fn(),
  changePin: vi.fn(),
  changeName: vi.fn(),
  fetchBootstrapStatus: vi.fn().mockResolvedValue({ initialized: true }),
  initializeAdmin: vi.fn(),
}))

// 捕获 wireAuthInterceptors 注册的会话失效回调（避免为触发真实 axios 401 拦截器），
// 其余 client 能力在 store 测试中不触达。
vi.mock('@/api/client', () => {
  let lastSessionExpiredHandler: (() => void) | null = null
  return {
    registerTokenReader: vi.fn(),
    registerRefreshExecutor: vi.fn(),
    registerSessionExpiredHandler: (handler: () => void) => {
      lastSessionExpiredHandler = handler
    },
    __getLastSessionExpiredHandler: (): (() => void) | null => lastSessionExpiredHandler,
  }
})

const mockedLogin = vi.mocked(authApi.login)
const mockedSelectCandidate = vi.mocked(authApi.selectCandidate)
const mockedLogout = vi.mocked(authApi.logout)

const { useAuthStore } = await import('@/stores/auth')
const { useSpacesStore } = await import('@/stores/spaces')

function makeUser(overrides: Partial<TokenPairResponse['user']> = {}): TokenPairResponse['user'] {
  return {
    id: 1,
    name: '张三',
    is_admin: false,
    pin_must_change: false,
    claim_status: 'claimed',
    profile_status: 'identity_confirmed',
    ...overrides,
  }
}

function makePair(user: TokenPairResponse['user']): TokenPairResponse {
  return {
    access_token: 'access-abc',
    refresh_token: 'refresh-xyz',
    token_type: 'bearer',
    user,
  }
}

/** 模拟上一段家庭会话的敏感缓存残留（未经 logout 的极端场景） */
function seedFamilySession(auth: ReturnType<typeof useAuthStore>): void {
  const spaces = useSpacesStore()
  spaces.spaces = [
    {
      id: 7,
      name: '王家空间',
      owner_id: 1,
      kind: 'household',
      created_at: '2026-09-01T00:00:00',
      pending_count: 0,
      member_count: 2,
    },
  ]
  spaces.currentSpaceId = 7
  spaces.members = [
    {
      id: 1,
      space_id: 7,
      user_id: 1,
      user_name: '张三',
      added_by: 1,
      role: 'space_admin',
      status: 'active',
      updated_at: '2026-09-01T00:00:00',
    },
  ]
  auth.accessToken = 'stale-access'
  auth.refreshToken = 'stale-refresh'
  auth.user = makeUser()
}

describe('auth store: isSystemAdmin 判定来源（principal_type 语义固定）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('principal_type=system_admin 才是系统主体；platform_operator 标记不改变判定', () => {
    const auth = useAuthStore()

    // 家庭端平台运营标记（is_admin/platform_role）不是系统管理员主体
    auth.user = makeUser({ is_admin: true, platform_role: 'platform_operator' })
    expect(auth.isSystemAdmin).toBe(false)

    auth.user = makeUser({ principal_type: 'family_user' })
    expect(auth.isSystemAdmin).toBe(false)

    // 唯一判定字段：principal_type（服务端签名会话返回）
    auth.user = makeUser({ is_admin: true, principal_type: 'system_admin' })
    expect(auth.isSystemAdmin).toBe(true)

    auth.user = null
    expect(auth.isSystemAdmin).toBe(false)
  })
})

describe('auth store: 主体互斥的会话切换（system_admin 登录清空家庭 stores）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('system_admin 登录：家庭敏感 stores 全量清空，token 正常落位', async () => {
    const auth = useAuthStore()
    seedFamilySession(auth)
    mockedLogin.mockResolvedValue(makePair(makeUser({ is_admin: true, principal_type: 'system_admin' })))

    await auth.login('系统管理员', '123456')
    await flushPromises()

    expect(auth.isSystemAdmin).toBe(true)
    expect(auth.accessToken).toBe('access-abc')
    const spaces = useSpacesStore()
    expect(spaces.spaces).toEqual([])
    expect(spaces.members).toEqual([])
    expect(spaces.currentSpaceId).toBeNull()
  })

  it('system_admin 经同名消歧 selectCandidate 登录：同样清空家庭 stores', async () => {
    const auth = useAuthStore()
    seedFamilySession(auth)
    mockedSelectCandidate.mockResolvedValue(
      makePair(makeUser({ is_admin: true, principal_type: 'system_admin' })),
    )

    await auth.selectCandidate('challenge-1', 1)
    await flushPromises()

    expect(auth.isSystemAdmin).toBe(true)
    const spaces = useSpacesStore()
    expect(spaces.spaces).toEqual([])
    expect(spaces.currentSpaceId).toBeNull()
  })

  it('family_user 登录：不做家庭缓存清理（清理点=登出/失效/系统主体换入）', async () => {
    const auth = useAuthStore()
    seedFamilySession(auth)
    mockedLogin.mockResolvedValue(makePair(makeUser({ principal_type: 'family_user' })))

    await auth.login('张三', '123456')
    await flushPromises()

    expect(auth.isSystemAdmin).toBe(false)
    const spaces = useSpacesStore()
    expect(spaces.spaces).toHaveLength(1)
    expect(spaces.members).toHaveLength(1)
  })

  it('登出回归：clearSession 全量清空凭据与家庭 stores', async () => {
    const auth = useAuthStore()
    seedFamilySession(auth)
    localStorage.setItem('fg.refresh_token', 'stale-refresh')
    mockedLogout.mockResolvedValue(undefined)

    await auth.logout()
    await flushPromises()

    expect(auth.accessToken).toBeNull()
    expect(auth.user).toBeNull()
    expect(localStorage.getItem('fg.refresh_token')).toBeNull()
    const spaces = useSpacesStore()
    expect(spaces.spaces).toEqual([])
    expect(spaces.currentSpaceId).toBeNull()
  })
})

describe('auth store: 会话过期跳转守卫（主体感知，/login 重载循环回归）', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    window.history.pushState({}, '', '/')
  })

  function makeRedirectProbe(): { assign: ReturnType<typeof vi.fn> } {
    return { assign: vi.fn() }
  }

  it('family_user 会话失效（默认）：非 /login 路径整页跳转到 /login', async () => {
    const { sessionExpiredNavigator, sessionExpiredRedirect } = await import('@/stores/auth')
    const probe = makeRedirectProbe()
    const spy = vi.spyOn(sessionExpiredNavigator, 'assign').mockImplementation(probe.assign)
    window.history.pushState({}, '', '/stats')

    sessionExpiredRedirect()

    expect(spy).toHaveBeenCalledWith('/login')
  })

  it('已在 /login：不再 assign（初始导航窗口 401 → 重复 assign 曾形成 /login 无限重载）', async () => {
    const { sessionExpiredNavigator, sessionExpiredRedirect } = await import('@/stores/auth')
    const spy = vi.spyOn(sessionExpiredNavigator, 'assign').mockImplementation(() => undefined)
    window.history.pushState({}, '', '/login')

    sessionExpiredRedirect()

    expect(spy).not.toHaveBeenCalled()
  })

  it('system_admin 会话失效：跳 /system-admin/login，绝不降级到家庭 /login', async () => {
    const { sessionExpiredNavigator, sessionExpiredRedirect } = await import('@/stores/auth')
    const probe = makeRedirectProbe()
    const spy = vi.spyOn(sessionExpiredNavigator, 'assign').mockImplementation(probe.assign)
    window.history.pushState({}, '', '/system-admin')

    sessionExpiredRedirect(true)

    expect(spy).toHaveBeenCalledWith('/system-admin/login')
  })

  it('system_admin 已在 /system-admin/login：不再 assign（防重载循环）', async () => {
    const { sessionExpiredNavigator, sessionExpiredRedirect } = await import('@/stores/auth')
    const spy = vi.spyOn(sessionExpiredNavigator, 'assign').mockImplementation(() => undefined)
    window.history.pushState({}, '', '/system-admin/login')

    sessionExpiredRedirect(true)

    expect(spy).not.toHaveBeenCalled()
  })

  it('wireAuthInterceptors 的会话失效回调：主体快照先于 clearSession 读取', async () => {
    const { sessionExpiredNavigator, wireAuthInterceptors } = await import('@/stores/auth')
    const client = (await import('@/api/client')) as unknown as {
      __getLastSessionExpiredHandler: () => (() => void) | null
    }
    const auth = useAuthStore()
    auth.user = makeUser({ principal_type: 'system_admin' })
    auth.accessToken = 'access-sa'
    const spy = vi.spyOn(sessionExpiredNavigator, 'assign').mockImplementation(() => undefined)

    wireAuthInterceptors()
    const handler = client.__getLastSessionExpiredHandler()
    expect(handler).toBeTypeOf('function')
    handler?.()

    expect(spy).toHaveBeenCalledWith('/system-admin/login')
    expect(auth.user).toBeNull()
  })
})

describe('auth store: applySystemAdminSession（SAR-F1 硬校验主体）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('system_admin 响应：落会话并清空家庭敏感 stores', async () => {
    const auth = useAuthStore()
    seedFamilySession(auth)

    auth.applySystemAdminSession(makePair(makeUser({ principal_type: 'system_admin' })))
    // clearFamilyCaches 经动态 import 异步清空业务 stores
    await flushPromises()

    expect(auth.isSystemAdmin).toBe(true)
    expect(auth.accessToken).toBe('access-abc')
    const spaces = useSpacesStore()
    expect(spaces.spaces).toEqual([])
  })

  it('family_user 响应：抛错且不落任何凭据，清理临时 auth 状态', async () => {
    const auth = useAuthStore()
    seedFamilySession(auth)
    localStorage.setItem('fg.refresh_token', 'temp-refresh')

    expect(() =>
      auth.applySystemAdminSession(makePair(makeUser({ principal_type: 'family_user' }))),
    ).toThrow()

    expect(auth.isLoggedIn).toBe(false)
    expect(auth.accessToken).toBeNull()
    expect(auth.user).toBeNull()
    expect(localStorage.getItem('fg.refresh_token')).toBeNull()
  })

  it('缺失 principal_type（未知主体）：同样硬拒绝', () => {
    const auth = useAuthStore()
    const user = makeUser()
    delete (user as Partial<typeof user>).principal_type

    expect(() => auth.applySystemAdminSession(makePair(user))).toThrow()
    expect(auth.isLoggedIn).toBe(false)
  })

  it('refresh 不改变主体：system_admin 会话经 refreshSession 后仍是系统主体', async () => {
    const auth = useAuthStore()
    auth.applySystemAdminSession(makePair(makeUser({ principal_type: 'system_admin' })))
    const mockedRefreshTokens = vi.mocked(authApi.refreshTokens)
    mockedRefreshTokens.mockResolvedValue(
      makePair(makeUser({ principal_type: 'system_admin' })),
    )

    await auth.refreshSession()

    expect(auth.isSystemAdmin).toBe(true)
    expect(auth.isLoggedIn).toBe(true)
  })

  it('refresh 响应若降级为 family_user：主体判定立即翻转为家庭主体（服务端权威投影）', async () => {
    // 防御性回归锚点：refresh 响应的 principal_type 是唯一权威来源；
    // 服务端按设计永不为 system_admin refresh 签发 family_user（后端契约测试覆盖），
    // 此处仅验证 store 忠实投影响应、不做任何本地主体改写。
    const auth = useAuthStore()
    auth.applySystemAdminSession(makePair(makeUser({ principal_type: 'system_admin' })))
    const mockedRefreshTokens = vi.mocked(authApi.refreshTokens)
    mockedRefreshTokens.mockResolvedValue(makePair(makeUser({ principal_type: 'family_user' })))

    await auth.refreshSession()

    expect(auth.isSystemAdmin).toBe(false)
  })
})
