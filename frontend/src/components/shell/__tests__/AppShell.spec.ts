import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AppShell from '@/components/shell/AppShell.vue'
import shellSource from '@/components/shell/AppShell.vue?raw'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'

vi.mock('@/components/common/GlobalSearch.vue', () => ({
  default: defineComponent({
    template: '<div data-test="global-search-stub" />',
  }),
}))

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'family-space', component: { template: '<div />' } },
      { path: '/home', name: 'home', component: { template: '<div />' } },
      { path: '/stats', name: 'stats', component: { template: '<div />' } },
      { path: '/memory', name: 'memory', component: { template: '<div />' } },
      { path: '/settings', name: 'settings', component: { template: '<div />' } },
      { path: '/admin', name: 'admin', component: { template: '<div />' } },
      { path: '/spaces/:spaceId/manage', name: 'space-management', component: { template: '<div />' } },
    ],
  })
}

function setLoggedIn(auth: ReturnType<typeof useAuthStore>, isAdmin = false): void {
  auth.user = {
    id: 1,
    name: '张三',
    is_admin: isAdmin,
    pin_must_change: false,
    claim_status: 'claimed',
    profile_status: 'identity_confirmed',
  }
  auth.accessToken = 'access-token'
}

describe('AppShell navigation', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    localStorage.clear()
  })

  it('renders stable primary links, active state, and accessible current page', async () => {
    const pinia = createPinia()
    const router = makeRouter()
    await router.push('/stats')
    await router.isReady()
    setLoggedIn(useAuthStore(pinia))

    const wrapper = mount(AppShell, {
      global: { plugins: [pinia, router] },
    })

    const nav = wrapper.find('nav[aria-label="主导航"]')
    expect(nav.exists()).toBe(true)
    expect(nav.findAll('a')).toHaveLength(5)
    expect(nav.text()).toContain('家庭空间')
    expect(nav.text()).toContain('成员')
    expect(nav.text()).toContain('统计')
    expect(nav.text()).toContain('记忆与知识')
    expect(nav.text()).toContain('设置')

    const statsLink = nav.find('a[href="/stats"]')
    expect(statsLink.classes()).toContain('nav-link--active')
    expect(statsLink.attributes('aria-current')).toBe('page')
    expect(nav.find('a[href="/"]').attributes('aria-current')).toBeUndefined()

    const brand = wrapper.find('a.shell-brand')
    expect(brand.attributes('href')).toBe('/')
    expect(brand.attributes('aria-label')).toBe('FamilyGraph 家庭空间')
    expect(wrapper.find('[data-test="global-search-stub"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('家庭应用壳不含任何平台后台入口（系统管理员走独立 shell）', async () => {
    const pinia = createPinia()
    const router = makeRouter()
    await router.push('/')
    await router.isReady()
    const auth = useAuthStore(pinia)
    setLoggedIn(auth, true)

    const wrapper = mount(AppShell, {
      global: { plugins: [pinia, router] },
    })

    // 平台后台由 SystemAdminShell 承载；家庭导航不得出现后台链接，
    // 即使账号带旧 is_admin 兼容投影也不例外。
    const nav = wrapper.find('nav[aria-label="主导航"]')
    expect(nav.find('a[href="/admin"]').exists()).toBe(false)
    expect(nav.find('a[href="/system-admin"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows space management only for the current space admin', async () => {
    const pinia = createPinia()
    const router = makeRouter()
    await router.push('/')
    await router.isReady()
    const auth = useAuthStore(pinia)
    setLoggedIn(auth, true)
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [{
      id: 7,
      name: '我的空间',
      owner_id: 1,
      kind: 'household',
      created_at: '2026-08-25T00:00:00',
      pending_count: 0,
      member_count: 1,
    }]
    spaces.currentSpaceId = 7
    spaces.members = [{
      id: 1,
      space_id: 7,
      user_id: 1,
      added_by: 1,
      role: 'space_admin',
      status: 'active',
      updated_at: '2026-08-25T00:00:00',
    }]

    const wrapper = mount(AppShell, { global: { plugins: [pinia, router] } })
    expect(wrapper.find('[data-test="space-management-link"]').exists()).toBe(true)

    spaces.members[0]!.role = 'member'
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="space-management-link"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('普通成员和访客不显示平台或空间管理入口', async () => {
    const pinia = createPinia()
    const router = makeRouter()
    await router.push('/')
    await router.isReady()
    const auth = useAuthStore(pinia)
    setLoggedIn(auth)
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [{
      id: 7,
      name: '共享空间',
      owner_id: 9,
      kind: 'household',
      created_at: '2026-08-25T00:00:00',
      pending_count: 0,
      member_count: 2,
    }]
    spaces.currentSpaceId = 7
    spaces.members = [{
      id: 1,
      space_id: 7,
      user_id: 1,
      added_by: 9,
      role: 'guest',
      status: 'active',
      updated_at: '2026-08-25T00:00:00',
    }]

    const wrapper = mount(AppShell, { global: { plugins: [pinia, router] } })
    expect(wrapper.find('[data-test="space-management-link"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/admin"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('has a narrow viewport overflow contract for the header and nav', async () => {
    const pinia = createPinia()
    const router = makeRouter()
    await router.push('/')
    await router.isReady()
    setLoggedIn(useAuthStore(pinia))

    const wrapper = mount(AppShell, {
      global: { plugins: [pinia, router] },
    })

    expect(wrapper.find('.shell-nav').exists()).toBe(true)
    expect(shellSource).toContain('overflow-x: auto')
    expect(shellSource).toContain('min-width: 0')
    expect(shellSource).toContain('@media (max-width: 480px)')
    wrapper.unmount()
  })
})
