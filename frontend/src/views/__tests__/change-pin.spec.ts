import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import * as authApi from '@/api/auth'
import ChangePinView from '@/views/ChangePinView.vue'

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

const ProvidedChangePin = defineComponent({
  render() {
    return h(NMessageProvider, () => h(ChangePinView))
  },
})

async function mountView() {
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
  const pinia = createPinia()
  const wrapper = mount(ProvidedChangePin, {
    global: { plugins: [pinia, router] },
    attachTo: document.body,
  })
  return { wrapper, router }
}

describe('ChangePinView redirect', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    localStorage.clear()
  })

  it('改 PIN 完成后回登录页并保留安全 redirect', async () => {
    vi.mocked(authApi.changePin).mockResolvedValue({
      id: 1,
      name: '张三',
      is_admin: false,
      pin_must_change: false,
      claim_status: 'claimed',
      profile_status: 'identity_confirmed',
    })
    const { wrapper, router } = await mountView()
    await wrapper.find('[data-test="old-pin"] input').setValue('123456')
    await wrapper.find('[data-test="new-pin"] input').setValue('654321')
    await wrapper.find('[data-test="confirm-pin"] input').setValue('654321')
    await wrapper.find('[data-test="change-pin-submit"]').trigger('click')

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('login'))
    expect(router.currentRoute.value.query.redirect).toBe('/settings')
    wrapper.unmount()
  })
})
