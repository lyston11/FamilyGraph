import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as authApi from '@/api/auth'
import { ApiError } from '@/api/errors'
import SystemAdminLoginView from '@/views/SystemAdminLoginView.vue'
import type { TokenPairResponse } from '@/types/api'

/**
 * 系统管理员真实登录页（SAR-F1）：
 * - 只接受 `principal_type === 'system_admin'` 的登录响应；family_user /
 *   未知主体不建立系统管理员会话，显示统一拒绝并清理临时 auth 状态；
 * - 凭据错误沿用后端统一文案，不泄露账号是否存在；
 * - 回跳只接受站内已知 system-admin 路由；
 * - 提交中/已成功状态不可重复提交。
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

/**
 * 拒绝主体时 clearSession 会 fire-and-forget 动态 import 11 个家庭业务 store。
 * 本 spec 关注登录主体校验与回跳（缓存清理语义由 auth.spec.ts 覆盖）；vitest
 * runner 下并发动态 import 可能产生部分初始化命名空间（浏览器模块地图会去重），
 * 打桩全部动态导入使用例与执行器竞态隔离。
 */
vi.mock('@/stores/spaces', () => ({ useSpacesStore: () => ({ clear: vi.fn() }) }))
vi.mock('@/stores/graph', () => ({ useGraphStore: () => ({ clear: vi.fn() }) }))
vi.mock('@/stores/governance', () => ({ useGovernanceStore: () => ({ clear: vi.fn() }) }))
vi.mock('@/stores/agent', () => ({ useAgentStore: () => ({ clear: vi.fn() }) }))
vi.mock('@/stores/memory', () => ({ useMemoryStore: () => ({ clear: vi.fn() }) }))
vi.mock('@/stores/kinship', () => ({ useKinshipStore: () => ({ clear: vi.fn() }) }))
vi.mock('@/stores/actionCards', () => ({ useActionCardsStore: () => ({ clear: vi.fn() }) }))
vi.mock('@/stores/personalFamilyView', () => ({
  usePersonalFamilyViewStore: () => ({ clear: vi.fn() }),
}))
vi.mock('@/stores/household', () => ({ useHouseholdCardStore: () => ({ clear: vi.fn() }) }))
vi.mock('@/stores/notifications', () => ({ useNotificationsStore: () => ({ clear: vi.fn() }) }))
vi.mock('@/stores/spaceStats', () => ({ useSpaceStatsStore: () => ({ clear: vi.fn() }) }))

const mockedLogin = vi.mocked(authApi.login)

function makePair(overrides: Partial<TokenPairResponse['user']> = {}): TokenPairResponse {
  return {
    access_token: 'access-sa',
    refresh_token: 'refresh-sa',
    token_type: 'bearer',
    user: {
      id: 1,
      name: '平台管理员',
      is_admin: true,
      pin_must_change: false,
      claim_status: 'claimed',
      profile_status: 'identity_confirmed',
      principal_type: 'system_admin',
      ...overrides,
    },
  }
}

async function mountView(query: Record<string, string> = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/system-admin/login', name: 'system-admin-login', component: SystemAdminLoginView },
      { path: '/system-admin', name: 'system-admin', component: { template: '<div />' } },
      { path: '/force-change-pin', name: 'force-change-pin', component: { template: '<div />' } },
      { path: '/login', name: 'login', component: { template: '<div />' } },
    ],
  })
  await router.push({ path: '/system-admin/login', query })
  await router.isReady()
  const pinia = createPinia()
  const wrapper = mount(SystemAdminLoginView, {
    global: { plugins: [pinia, router] },
  })
  return { wrapper, router, pinia }
}

async function submitForm(wrapper: ReturnType<typeof mount>): Promise<void> {
  await wrapper.find('[data-test="system-admin-login-name"] input').setValue('平台管理员')
  await wrapper.find('[data-test="system-admin-login-pin"] input').setValue('123456')
  await wrapper.find('[data-test="system-admin-login-submit"]').trigger('click')
}

describe('SystemAdminLoginView（SAR-F1 真实登录）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('system_admin 登录成功：会话落位并回系统后台', async () => {
    mockedLogin.mockResolvedValue(makePair())
    const { wrapper, router, pinia } = await mountView()
    await submitForm(wrapper)
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('system-admin'))

    const auth = (await import('@/stores/auth')).useAuthStore(pinia)
    expect(auth.isLoggedIn).toBe(true)
    expect(auth.isSystemAdmin).toBe(true)
    expect(localStorage.getItem('fg.refresh_token')).toBe('refresh-sa')
    wrapper.unmount()
  })

  it('system_admin 首登必改 PIN：登录成功进 PIN 首改页', async () => {
    mockedLogin.mockResolvedValue(makePair({ pin_must_change: true }))
    const { wrapper, router } = await mountView()
    await submitForm(wrapper)
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('force-change-pin'))
    wrapper.unmount()
  })

  it('family_user 凭据：不建立系统管理员会话，统一拒绝并引导家庭入口', async () => {
    mockedLogin.mockResolvedValue(makePair({ principal_type: 'family_user', is_admin: false }))
    const { wrapper, router, pinia } = await mountView()
    await submitForm(wrapper)

    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="system-admin-login-error"]').exists()).toBe(true),
    )
    const auth = (await import('@/stores/auth')).useAuthStore(pinia)
    expect(auth.isLoggedIn).toBe(false)
    expect(auth.accessToken).toBeNull()
    expect(localStorage.getItem('fg.refresh_token')).toBeNull()
    expect(router.currentRoute.value.name).toBe('system-admin-login')
    wrapper.unmount()
  })

  it('未知/缺失 principal_type：整体拒绝并清理任何临时 auth 状态', async () => {
    // 模拟主体字段缺失（未知主体投影）
    mockedLogin.mockResolvedValue({
      ...makePair(),
      user: { ...makePair().user, principal_type: undefined },
    })
    const { wrapper, pinia } = await mountView()
    await submitForm(wrapper)

    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="system-admin-login-error"]').exists()).toBe(true),
    )
    const auth = (await import('@/stores/auth')).useAuthStore(pinia)
    expect(auth.isLoggedIn).toBe(false)
    expect(auth.accessToken).toBeNull()
    wrapper.unmount()
  })

  it('凭据错误：沿用后端统一文案，不泄露账号是否存在', async () => {
    mockedLogin.mockRejectedValue(new ApiError(401, 'AUTH_CREDENTIAL_INVALID', '用户名或 PIN 码错误'))
    const { wrapper } = await mountView()
    await submitForm(wrapper)

    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="system-admin-login-error"]').text()).toBe(
        '用户名或 PIN 码错误',
      ),
    )
    wrapper.unmount()
  })

  it('同名同 PIN 409 消歧属于家庭凭据：按统一拒绝处理，不进入家庭消歧流程', async () => {
    mockedLogin.mockRejectedValue(
      new ApiError(409, 'LOGIN_CHALLENGE', '存在多个同名账号', {
        challenge_id: 'c1',
        candidates: [{ id: 1, name: '张三' }],
      }),
    )
    const { wrapper } = await mountView()
    await submitForm(wrapper)

    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="system-admin-login-error"]').text()).toContain(
        '仅限系统管理员',
      ),
    )
    // 不渲染家庭候选选择弹窗
    expect(wrapper.find('[data-test="challenge-dialog"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('回跳只接受站内已知 system-admin 路由：?redirect=/system-admin 生效', async () => {
    mockedLogin.mockResolvedValue(makePair())
    const { wrapper, router } = await mountView({ redirect: '/system-admin' })
    await submitForm(wrapper)
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('system-admin'))
    wrapper.unmount()
  })

  it('任意/家庭/跨站回跳地址一律丢弃：回系统后台首页', async () => {
    for (const redirect of ['//evil.com', '/settings', '/family-tree', 'https://evil.com']) {
      mockedLogin.mockResolvedValue(makePair())
      const { wrapper, router } = await mountView({ redirect })
      await submitForm(wrapper)
      await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('system-admin'))
      expect(router.currentRoute.value.fullPath).toBe('/system-admin')
      wrapper.unmount()
    }
  })

  it('提交中不可重复提交：再次点击不触发第二次登录请求', async () => {
    let resolveLogin: ((pair: TokenPairResponse) => void) | undefined
    mockedLogin.mockReturnValue(
      new Promise<TokenPairResponse>((resolve) => {
        resolveLogin = resolve
      }),
    )
    const { wrapper } = await mountView()
    await wrapper.find('[data-test="system-admin-login-name"] input').setValue('平台管理员')
    await wrapper.find('[data-test="system-admin-login-pin"] input').setValue('123456')
    await wrapper.find('[data-test="system-admin-login-submit"]').trigger('click')
    await wrapper.find('[data-test="system-admin-login-submit"]').trigger('click')

    expect(mockedLogin).toHaveBeenCalledTimes(1)
    resolveLogin?.(makePair())
    await vi.waitFor(() => expect(mockedLogin).toHaveBeenCalledTimes(1))
    wrapper.unmount()
  })

  it('必改 PIN 且带安全回跳：redirect 透传给 PIN 首改页', async () => {
    mockedLogin.mockResolvedValue(makePair({ pin_must_change: true }))
    const { wrapper, router } = await mountView({ redirect: '/system-admin' })
    await submitForm(wrapper)
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('force-change-pin'))
    expect(router.currentRoute.value.query.redirect).toBe('/system-admin')
    wrapper.unmount()
  })
})
