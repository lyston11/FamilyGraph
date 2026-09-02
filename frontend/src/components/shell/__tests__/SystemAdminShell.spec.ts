import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SystemAdminShell from '@/components/shell/SystemAdminShell.vue'
import shellSource from '@/components/shell/SystemAdminShell.vue?raw'
import * as authApi from '@/api/auth'
import type { TokenPairResponse } from '@/types/api'

/**
 * 系统后台壳（SAR-F3）：登出控件撤销 system_admin 主体的 refresh session、
 * 清空系统管理员会话并回 `/system-admin/login`；壳不渲染家庭导航、关系图、
 * Memory、Session 或任何家庭数据入口。
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
 * 登出触发 clearSession → clearFamilyCaches 的 fire-and-forget 动态 import。
 * 本 spec 关注壳层登出接线（缓存清理语义由 auth.spec.ts 覆盖）；vitest runner
 * 下并发动态 import 可能产生部分初始化命名空间（浏览器模块地图会去重），
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

const mockedLogout = vi.mocked(authApi.logout)

function makeSystemAdminPair(): TokenPairResponse {
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
    },
  }
}

async function mountShell() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/system-admin', name: 'system-admin', component: { template: '<div />' } },
      {
        path: '/system-admin/login',
        name: 'system-admin-login',
        component: { template: '<div />' },
      },
    ],
  })
  await router.push({ name: 'system-admin' })
  await router.isReady()
  const pinia = createPinia()
  const { useAuthStore } = await import('@/stores/auth')
  const auth = useAuthStore(pinia)
  auth.applySystemAdminSession(makeSystemAdminPair())
  const wrapper = mount(SystemAdminShell, {
    global: { plugins: [pinia, router] },
  })
  await flushPromises()
  return { wrapper, router, auth }
}

describe('SystemAdminShell（SAR-F3 登出与家庭隔离）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('登出：撤销主体 refresh session、清空会话并回系统管理员登录页', async () => {
    const { wrapper, router, auth } = await mountShell()
    expect(auth.isLoggedIn).toBe(true)
    expect(auth.isSystemAdmin).toBe(true)

    await wrapper.find('[data-test="system-admin-logout"]').trigger('click')
    await flushPromises()

    expect(mockedLogout).toHaveBeenCalledTimes(1)
    expect(auth.accessToken).toBeNull()
    expect(auth.refreshToken).toBeNull()
    expect(auth.user).toBeNull()
    expect(localStorage.getItem('fg.refresh_token')).toBeNull()
    expect(router.currentRoute.value.name).toBe('system-admin-login')
    wrapper.unmount()
  })

  it('壳导航只有治理概览：无家庭导航/关系图/Memory/Session 入口', async () => {
    const { wrapper } = await mountShell()

    const links = wrapper.findAll('a').map((a) => a.text().trim())
    expect(links).toContain('治理概览')
    for (const familyLabel of ['我的家庭', '家族树', '记忆与知识', '统计', '设置']) {
      expect(links).not.toContain(familyLabel)
    }
    expect(wrapper.find('a[href="/memory"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('壳源码不引入家庭 stores 或家庭数据 API（静态断言）', () => {
    for (const forbidden of [
      'stores/members',
      'stores/graph',
      'stores/memory',
      'stores/spaces',
      'api/members',
      'api/graph',
      'api/memory',
      'api/spaces',
    ]) {
      expect(shellSource).not.toContain(forbidden)
    }
  })
})
