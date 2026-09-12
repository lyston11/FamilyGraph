import { describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
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

  it('keeps the real shell on a horizontal desktop axis', () => {
    const css = readFileSync(join(process.cwd(), 'src/styles/main.css'), 'utf8')
    expect(css).toMatch(/\.admin-shell\s*\{[\s\S]*?flex-direction:\s*row;/)
    expect(css).toMatch(/\.admin-shell-body\s*\{[\s\S]*?flex-direction:\s*column;/)
  })
})
