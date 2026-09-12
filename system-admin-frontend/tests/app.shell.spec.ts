import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia } from 'pinia'

vi.mock('@/components/AdminShell.vue', () => ({
  default: { template: '<div data-testid="admin-shell-stub" />' },
}))

import App from '@/App.vue'

async function mountAt(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'overview', component: { template: '<div />' }, meta: { requiresAuth: true } },
      { path: '/login', name: 'login', component: { template: '<div />' }, meta: { public: true } },
    ],
  })
  await router.push(path)
  await router.isReady()
  return mount(App, { global: { plugins: [router, createPinia()] } })
}

describe('admin app shell routing', () => {
  it('renders the shared admin shell on protected pages', async () => {
    const wrapper = await mountAt('/')
    expect(wrapper.find('[data-testid="admin-shell-stub"]').exists()).toBe(true)
  })

  it('keeps the login page free of admin navigation', async () => {
    const wrapper = await mountAt('/login')
    expect(wrapper.find('[data-testid="admin-shell-stub"]').exists()).toBe(false)
  })
})
