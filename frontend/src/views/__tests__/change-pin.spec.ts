import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import * as authApi from '@/api/auth'
import ChangePinView from '@/views/ChangePinView.vue'
import type { UserOut } from '@/types/api'

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

/**
 * clearSession 会 fire-and-forget 动态 import 11 个家庭业务 store 逐一 clear()。
 * 本 spec 关注的是改 PIN 后的主体感知回跳（缓存清理语义由 auth.spec.ts 覆盖）；
 * vitest runner 下两次并发触发同一批动态 import 可能产生部分初始化的模块
 * 命名空间（浏览器模块地图会去重，无此问题），因此这里打桩全部动态导入，
 * 让用例完全隔离于该执行器竞态。
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

const ProvidedChangePin = defineComponent({
  render() {
    return h(NMessageProvider, () => h(ChangePinView))
  },
})

function makeChangedUser(overrides: Partial<UserOut> = {}): UserOut {
  return {
    id: 1,
    name: '张三',
    pin_must_change: false,
    claim_status: 'claimed',
    profile_status: 'identity_confirmed',
    ...overrides,
  }
}

async function makeTestRouter(): Promise<Router> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', name: 'login', component: { template: '<div />' } },
      { path: '/force-change-pin', name: 'force-change-pin', component: ChangePinView },
      { path: '/settings', name: 'settings', component: { template: '<div />' } },
    ],
  })
  await router.push({ name: 'force-change-pin', query: { redirect: '/settings' } })
  await router.isReady()
  return router
}

/** 在 auth store 中预置改 PIN 前的主体（必须在视图挂载前完成：挂载时快照主体） */
async function seedPrincipal(pinia: Pinia, overrides: Partial<UserOut>): Promise<void> {
  const { useAuthStore } = await import('@/stores/auth')
  const auth = useAuthStore(pinia)
  auth.user = makeChangedUser({ pin_must_change: true, ...overrides })
  auth.accessToken = 'access-before-pin-change'
}

/**
 * clearSession 会以 fire-and-forget 动态 import 清空 13+ 业务 stores；
 * 等待若干宏任务让该链路在测试环境拆除前完全落定，否则拆环境后的悬挂
 * promise 会产生 unhandled rejection（change-pin + 其它 spec 同 worker 时复现）。
 */
async function settleImportChains(): Promise<void> {
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 50))
  await flushPromises()
}

describe('ChangePinView redirect（SAR-F2 主体感知回跳）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    localStorage.clear()
  })

  async function fillAndSubmit(wrapper: Awaited<ReturnType<typeof mount>>): Promise<void> {
    await wrapper.find('[data-test="old-pin"] input').setValue('123456')
    await wrapper.find('[data-test="new-pin"] input').setValue('654321')
    await wrapper.find('[data-test="confirm-pin"] input').setValue('654321')
    await wrapper.find('[data-test="change-pin-submit"]').trigger('click')
  }

  it('family_user 改 PIN 完成后回家庭登录页并保留安全 redirect（既有路径不变）', async () => {
    vi.mocked(authApi.changePin).mockResolvedValue(makeChangedUser())
    const router = await makeTestRouter()
    const pinia = createPinia()
    await seedPrincipal(pinia, { principal_type: 'family_user' })
    const wrapper = mount(ProvidedChangePin, {
      global: { plugins: [pinia, router] },
      attachTo: document.body,
    })
    await fillAndSubmit(wrapper)

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('login'))
    expect(router.currentRoute.value.query.redirect).toBe('/settings')
    await settleImportChains()
    wrapper.unmount()
  })

})
