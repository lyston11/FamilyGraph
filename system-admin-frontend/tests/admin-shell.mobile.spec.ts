/**
 * AdminShell 移动导航测试（R4，09-11 整改）：
 * - 菜单按钮始终渲染且带 aria-expanded / aria-controls；
 * - 点击切换导航面板展开类；路由跳转后自动收起；
 * - Escape 收起面板并解锁 body 滚动（ag-nav-locked 类）；
 * - 桌面/移动显隐由 CSS media query 承担（jsdom 不评估媒体查询），
 *   这里只验证行为状态机。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { NMessageProvider } from 'naive-ui'

import AdminShell from '@/components/AdminShell.vue'

vi.mock('@/stores/auth', () => ({
  useAdminAuthStore: () => ({
    admin: { username: 'admin' },
    logout: vi.fn().mockResolvedValue(undefined),
  }),
}))

function mountShell() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: ['overview', 'space-admins', 'anomaly-queue', 'operations', 'agent-monitor', 'agent-providers', 'platform-features', 'access-audit'].map(
      (name) => ({ path: `/${name}`, name, component: { template: '<div />' } }),
    ),
  })
  const wrapper = mount(NMessageProvider, {
    slots: { default: () => '' },
    global: { plugins: [router] },
  })
  // 直接挂 AdminShell（NDropdown 需要的 provider 由外壳消息层近似；
  // Shell 本身不使用 useMessage）
  const shell = mount(AdminShell, {
    global: { plugins: [router], stubs: { RouterView: true } },
    attachTo: document.body,
  })
  void wrapper
  return { shell, router }
}

beforeEach(() => {
  setActivePinia(createPinia())
  document.body.className = ''
})

describe('AdminShell 移动导航（375px 响应式 R4）', () => {
  it('菜单按钮带 aria-expanded/aria-controls，点击切换展开态', async () => {
    const { shell } = mountShell()
    const toggle = shell.find('[data-testid="mobile-nav-toggle"]')
    expect(toggle.exists()).toBe(true)
    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(toggle.attributes('aria-controls')).toBe('admin-nav-panel')

    await toggle.trigger('click')
    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect(shell.find('nav.admin-nav').classes()).toContain('admin-nav--open')

    await toggle.trigger('click')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(shell.find('nav.admin-nav').classes()).not.toContain('admin-nav--open')
    shell.unmount()
  })

  it('面板开启时锁定 body 滚动，Escape 关闭并解锁', async () => {
    const { shell } = mountShell()
    const toggle = shell.find('[data-testid="mobile-nav-toggle"]')
    await toggle.trigger('click')
    expect(document.body.classList.contains('ag-nav-locked')).toBe(true)

    await shell.find('.admin-shell').trigger('keydown', { key: 'Escape' })
    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(document.body.classList.contains('ag-nav-locked')).toBe(false)
    shell.unmount()
  })

  it('路由跳转后导航面板自动收起', async () => {
    const { shell, router } = mountShell()
    await router.push({ name: 'overview' })
    await shell.vm.$nextTick()
    const toggle = shell.find('[data-testid="mobile-nav-toggle"]')
    await toggle.trigger('click')
    expect(shell.find('nav.admin-nav').classes()).toContain('admin-nav--open')

    await router.push({ name: 'operations' })
    await shell.vm.$nextTick()
    expect(shell.find('nav.admin-nav').classes()).not.toContain('admin-nav--open')
    expect(document.body.classList.contains('ag-nav-locked')).toBe(false)
    shell.unmount()
  })
})
