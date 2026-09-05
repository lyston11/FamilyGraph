import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as authApi from '@/api/auth'
import type { TokenPairResponse } from '@/types/api'

/**
 * auth store（09-04 家庭端零后台痕迹）：
 * - 家庭端只有 family_user 主体；系统管理员在独立前端应用与会话域，
 *   本 store 不包含任何后台主体分支（architecture.md §12）；
 * - 会话清理点 = 登出 / 401 / token 失效（clearSession 全量清空）。
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
const mockedLogout = vi.mocked(authApi.logout)

const { useAuthStore } = await import('@/stores/auth')
const { useSpacesStore } = await import('@/stores/spaces')

function makeUser(overrides: Partial<TokenPairResponse['user']> = {}): TokenPairResponse['user'] {
  return {
    id: 1,
    name: '张三',
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

describe('auth store: 家庭主体登录与缓存清理', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('family_user 登录：token 正常落位，保留既有家庭会话数据', async () => {
    const auth = useAuthStore()
    seedFamilySession(auth)
    mockedLogin.mockResolvedValue(makePair(makeUser({ principal_type: 'family_user' })))

    await auth.login('张三', '123456')
    await flushPromises()

    expect(auth.isLoggedIn).toBe(true)
    expect(auth.accessToken).toBe('access-abc')
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

describe('auth store: 会话过期跳转守卫（/login 重载循环回归）', () => {
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

  it('wireAuthInterceptors 的会话失效回调：清空会话后整页回 /login', async () => {
    const { sessionExpiredNavigator, wireAuthInterceptors } = await import('@/stores/auth')
    const client = (await import('@/api/client')) as unknown as {
      __getLastSessionExpiredHandler: () => (() => void) | null
    }
    const auth = useAuthStore()
    auth.user = makeUser()
    auth.accessToken = 'access-family'
    const spy = vi.spyOn(sessionExpiredNavigator, 'assign').mockImplementation(() => undefined)

    wireAuthInterceptors()
    const handler = client.__getLastSessionExpiredHandler()
    expect(handler).toBeTypeOf('function')
    handler?.()

    expect(spy).toHaveBeenCalledWith('/login')
    expect(auth.user).toBeNull()
  })
})
