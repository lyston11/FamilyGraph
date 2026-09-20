import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AppShell from '@/components/shell/AppShell.vue'
import shellSource from '@/components/shell/AppShell.vue?raw'
import type { FamilySpace, SpaceMemberInfo } from '@/types/api'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import { useUiStore } from '@/stores/ui'

// 空间上下文协调器在壳层测试中打桩：壳只负责接线，事务语义由
// composables/__tests__/useSpaceContext.spec.ts 覆盖。
const switchSpaceMock = vi.fn<
  (spaceId: number, options?: { navigate?: boolean }) => Promise<boolean>
>()
const ensureDefaultSpaceMock = vi.fn<() => Promise<string>>()
vi.mock('@/composables/useSpaceContext', () => ({
  useSpaceContext: () => ({
    switchSpace: (spaceId: number, options?: { navigate?: boolean }) => switchSpaceMock(spaceId, options),
    ensureDefaultSpace: () => ensureDefaultSpaceMock(),
    defaultTarget: () => ({ name: 'home' }),
  }),
}))

// 通知端点是 BLOCKER 占位（运行时 404）：壳必须安静降级——无角标、无报错横幅
vi.mock('@/api/notifications', () => ({
  fetchNotifications: vi.fn(),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
}))

// 账号菜单登出走 auth store → /auth/logout；其余 auth API 在壳测试中不触达
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

const notificationsApi = await import('@/api/notifications')
const fetchNotificationsMock = vi.mocked(notificationsApi.fetchNotifications)
const authApi = await import('@/api/auth')

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/family-tree', name: 'family-space', component: { template: '<div />' } },
      { path: '/people/:userId', name: 'person-profile', component: { template: '<div />' } },
      { path: '/notifications', name: 'notifications', component: { template: '<div />' } },
      { path: '/stats', name: 'stats', component: { template: '<div />' } },
      { path: '/memory', name: 'memory', component: { template: '<div />' } },
      { path: '/settings', name: 'settings', component: { template: '<div />' } },
      { path: '/login', name: 'login', component: { template: '<div />' } },
      {
        path: '/spaces/:spaceId/manage',
        name: 'space-management',
        component: { template: '<div />' },
      },
    ],
  })
}

function makeSpace(overrides: Partial<FamilySpace>): FamilySpace {
  return {
    id: 7,
    name: '我的空间',
    owner_id: 1,
    kind: 'household',
    created_at: '2026-08-25T00:00:00',
    pending_count: 0,
    member_count: 1,
    ...overrides,
  }
}

function makeMember(overrides: Partial<SpaceMemberInfo>): SpaceMemberInfo {
  return {
    id: 1,
    space_id: 7,
    user_id: 1,
    added_by: 1,
    role: 'space_admin',
    status: 'active',
    updated_at: '2026-08-25T00:00:00',
    ...overrides,
  }
}

function setLoggedIn(auth: ReturnType<typeof useAuthStore>): void {
  auth.user = {
    id: 1,
    name: '张三',
    pin_must_change: false,
    claim_status: 'claimed',
    profile_status: 'identity_confirmed',
  }
  auth.accessToken = 'access-token'
}

interface MountOptions {
  path?: string
}

async function mountShell(options: MountOptions = {}) {
  const pinia = createPinia()
  const router = makeRouter()
  await router.push(options.path ?? '/')
  await router.isReady()
  setLoggedIn(useAuthStore(pinia))
  const wrapper = mount(AppShell, {
    global: { plugins: [pinia, router] },
    attachTo: document.body,
  })
  await flushPromises()
  return { wrapper, pinia, router }
}

describe('AppShell navigation（统一家庭壳）', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    localStorage.clear()
    vi.clearAllMocks()
    fetchNotificationsMock.mockRejectedValue(new Error('notifications endpoint 404 (BLOCKER)'))
    switchSpaceMock.mockResolvedValue(true)
    ensureDefaultSpaceMock.mockResolvedValue('household')
  })

  it('一级导航顺序固定：我的家庭、家族树、统计；记忆与知识、设置为次级入口（记忆在设置上方）', async () => {
    const { wrapper } = await mountShell({ path: '/stats' })

    const primary = wrapper.find('nav[aria-label="主导航"]')
    expect(primary.exists()).toBe(true)
    const labels = primary.findAll('a').map((a) => a.text().trim())
    expect(labels).toEqual(['我的家庭', '家族树', '统计'])

    const secondary = wrapper.find('nav[aria-label="次级导航"]')
    expect(secondary.exists()).toBe(true)
    expect(secondary.findAll('a').map((a) => a.text().trim())).toEqual(['记忆与知识', '设置'])

    // 当前页高亮与 aria-current（exact 匹配，`/` 不误高亮其它路由）
    const statsLink = primary.find('a[href="/stats"]')
    expect(statsLink.classes()).toContain('nav-link--active')
    expect(statsLink.attributes('aria-current')).toBe('page')
    expect(primary.find('a[href="/"]').attributes('aria-current')).toBeUndefined()

    // 品牌链接回到我的家庭（/）
    const brand = wrapper.find('a.shell-brand')
    expect(brand.attributes('href')).toBe('/')

    // 旧全局搜索不再出现在壳中（不含组件引用/标签）
    expect(shellSource).not.toMatch(/import GlobalSearch|<GlobalSearch/)
    expect(wrapper.find('[data-test="global-search-stub"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('空间管理链接仅当前空间 space_admin 可见', async () => {
    const { wrapper, pinia } = await mountShell()
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [makeSpace({})]
    spaces.currentSpaceId = 7
    spaces.members = [makeMember({ role: 'space_admin' })]
    await flushPromises()

    expect(wrapper.find('[data-test="space-management-link"]').exists()).toBe(true)

    spaces.members = [makeMember({ role: 'member' })]
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="space-management-link"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('普通成员不显示空间管理或任何平台后台入口', async () => {
    const { wrapper, pinia } = await mountShell()
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [makeSpace({ owner_id: 9 })]
    spaces.currentSpaceId = 7
    spaces.members = [makeMember({ role: 'member', added_by: 9 })]
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-test="space-management-link"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/admin"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/system-admin"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('家庭壳不渲染任何系统管理员后台入口（PRD §2.7 主体隔离，Phase 6）', async () => {
    // 即使当前用户是空间管理员，家庭壳也不出现系统后台入口或后台登录占位链接
    const { wrapper, pinia } = await mountShell()
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [makeSpace({})]
    spaces.currentSpaceId = 7
    spaces.members = [makeMember({ role: 'space_admin' })]
    await flushPromises()

    expect(wrapper.find('[data-test="space-management-link"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/system-admin"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/system-admin/login"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/admin"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('系统管理员')
    expect(wrapper.text()).not.toContain('系统治理')
    // 源码级红线：家庭壳模板不引用任何系统后台路由
    expect(shellSource).not.toMatch(/system-admin/)
    wrapper.unmount()
  })

  it('通知入口指向 /notifications；端点 404 时安静降级：无角标、无报错横幅', async () => {
    const { wrapper, pinia } = await mountShell()
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [makeSpace({})]
    spaces.currentSpaceId = 7
    await flushPromises()

    const entry = wrapper.find('[data-test="notifications-entry"]')
    expect(entry.exists()).toBe(true)
    expect(entry.attributes('href')).toBe('/notifications')
    // BLOCKER 端点被请求（有数据就展示），失败被吞掉：无角标、无 alert 横幅
    expect(fetchNotificationsMock).toHaveBeenCalledWith(7, null)
    expect(wrapper.find('[data-test="notifications-unread"]').exists()).toBe(false)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('通知未读数来自服务端载荷并渲染角标', async () => {
    fetchNotificationsMock.mockResolvedValue({
      data: {
        space_id: 7,
        unread_count: 2,
        items: [
          {
            id: 1,
            space_id: 7,
            kind: 'space_membership',
            payload: {
              title: '空间邀请',
              summary: null,
              actor_name: null,
              space_name: null,
            },
            domain_status: 'pending',
            action_card: null,
  suggestion: null,
            created_at: '2026-09-01T00:00:00',
            read_at: null,
          },
        ],
      },
      etag: null,
    })

    const { wrapper, pinia } = await mountShell()
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [makeSpace({})]
    spaces.currentSpaceId = 7
    await flushPromises()

    const badge = wrapper.find('[data-test="notifications-unread"]')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe('2')
    wrapper.unmount()
  })

  it('Assistant 入口由根组件提供，壳内不重复渲染占位', async () => {
    const { wrapper } = await mountShell()
    const placeholder = wrapper.find('[data-test="assistant-placeholder"]')
    expect(placeholder.exists()).toBe(false)
    expect(wrapper.find('[data-test="assistant-panel"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('主题切换保留 paper/modern NSwitch 语义', async () => {
    const { wrapper, pinia } = await mountShell()
    const ui = useUiStore(pinia)
    expect(ui.theme).toBe('paper')

    await wrapper.find('.theme-switch').trigger('click')
    expect(ui.theme).toBe('modern')
    expect(localStorage.getItem('fg-theme')).toBe('modern')
    expect(document.documentElement.dataset.theme).toBe('modern')

    await wrapper.find('.theme-switch').trigger('click')
    expect(ui.theme).toBe('paper')
    wrapper.unmount()
  })

  it('账号菜单提供登出：点击后清理会话并回到登录页', async () => {
    const { wrapper, pinia, router } = await mountShell()
    const auth = useAuthStore(pinia)

    await wrapper.find('[data-test="account-menu-trigger"]').trigger('click')
    await flushPromises()

    const logoutButton = document.querySelector<HTMLButtonElement>(
      '[data-test="account-menu-logout"]',
    )
    expect(logoutButton).not.toBeNull()
    logoutButton!.click()
    await flushPromises()

    expect(authApi.logout).toHaveBeenCalledWith(auth.refreshToken)
    expect(auth.isLoggedIn).toBe(false)
    expect(router.currentRoute.value.name).toBe('login')
    wrapper.unmount()
  })

  it('账号菜单仅当前空间管理员可见空间管理项', async () => {
    const { wrapper, pinia } = await mountShell()
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [makeSpace({})]
    spaces.currentSpaceId = 7
    spaces.members = [makeMember({ role: 'member' })]
    await wrapper.vm.$nextTick()

    await wrapper.find('[data-test="account-menu-trigger"]').trigger('click')
    await flushPromises()
    expect(
      document.querySelector('[data-test="account-menu-space-management"]'),
    ).toBeNull()

    spaces.members = [makeMember({ role: 'space_admin' })]
    await wrapper.vm.$nextTick()
    expect(
      document.querySelector('[data-test="account-menu-space-management"]'),
    ).not.toBeNull()
    wrapper.unmount()
  })

  it('账号菜单提供「收到的邀请」入口，角标只算待我接受且与当前空间无关', async () => {
    const { wrapper, pinia } = await mountShell()
    const spaces = useSpacesStore(pinia)
    // 当前空间是 7；邀请属于空间 3（我尚未加入）——缺陷场景：通知中心照不到它
    spaces.spaces = [makeSpace({})]
    spaces.currentSpaceId = 7
    spaces.invitations = [
      {
        id: 95,
        space_id: 3,
        space_name: '马府',
        space_kind: 'household',
        direction: 'incoming',
        stage: 'awaiting_me',
        counterpart_user_id: 1,
        counterpart_name: '朱元璋',
        relation_label: '女婿',
        owner_approved_at: '2026-09-20T14:09:19',
        updated_at: '2026-09-20T14:09:19',
      },
      // 等房主批准：不是我的待办，不计入角标
      {
        id: 96,
        space_id: 5,
        space_name: '徐达家',
        space_kind: 'household',
        direction: 'incoming',
        stage: 'awaiting_owner',
        counterpart_user_id: 2,
        counterpart_name: '徐达',
        relation_label: null,
        owner_approved_at: null,
        updated_at: '',
      },
      // 我发起的申请：也不是我的待办
      {
        id: 97,
        space_id: 6,
        space_name: '常府',
        space_kind: 'household',
        direction: 'outgoing',
        stage: 'awaiting_owner',
        counterpart_user_id: 3,
        counterpart_name: '常遇春',
        relation_label: null,
        owner_approved_at: null,
        updated_at: '',
      },
    ]
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-test="invitations-unread"]').text()).toBe('1')

    await wrapper.find('[data-test="account-menu-trigger"]').trigger('click')
    await flushPromises()
    const entry = document.querySelector('[data-test="account-menu-invitations"]')
    expect(entry).not.toBeNull()
    expect(entry!.textContent).toContain('收到的邀请')
    wrapper.unmount()
  })

  it('无待我接受的邀请时不显示角标', async () => {
    const { wrapper, pinia } = await mountShell()
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [makeSpace({})]
    spaces.currentSpaceId = 7
    spaces.invitations = []
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-test="invitations-unread"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('空间选择器只展示家族空间，并在家庭页切到对应家庭卡', async () => {
    const { wrapper, pinia } = await mountShell()
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [
      makeSpace({ id: 7, name: '我的家庭', kind: 'household' }),
      makeSpace({ id: 8, name: '父母家', kind: 'household', owner_id: 9 }),
      makeSpace({ id: 12, name: '张氏家族', kind: 'lineage', owner_id: 9 }),
    ]
    spaces.currentSpaceId = 7
    spaces.members = [makeMember({ space_id: 7 })]
    await wrapper.vm.$nextTick()

    // 所有页面的选择器都只展示家族空间，选择后由当前页面决定落到家庭卡或家族树
    const options = (wrapper.vm as unknown as { spacePickerOptions: unknown[] })
      .spacePickerOptions as Array<{
      type: string
      label: string
      children: Array<{ label: string; value: number }>
    }>
    expect(options).toHaveLength(1)
    expect(options[0]!.label).toBe('家族空间')
    expect(options[0]!.children.map((child) => child.value)).toEqual([12, 7])
    // 选择其它空间 → 触发空间切换事务
    await (wrapper.vm as unknown as { onSpaceSelect: (id: number) => Promise<void> })
      .onSpaceSelect(12)
    expect(switchSpaceMock).toHaveBeenCalledWith(8, { navigate: true })
    wrapper.unmount()
  })

  it('显式家族配对（lineage_space_id）优先于 owner 推断驱动选项归属', async () => {
    const { wrapper, pinia } = await mountShell()
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [
      makeSpace({ id: 7, name: '我的家庭', kind: 'household', owner_id: 1, lineage_space_id: 12 }),
      makeSpace({ id: 8, name: '父母家', kind: 'household', owner_id: 9 }),
      makeSpace({ id: 12, name: '张氏家族', kind: 'lineage', owner_id: 5 }),
    ]
    spaces.currentSpaceId = 8
    spaces.members = [makeMember({ space_id: 7 })]
    await wrapper.vm.$nextTick()

    const options = (wrapper.vm as unknown as { spacePickerOptions: unknown[] })
      .spacePickerOptions as Array<{ children: Array<{ value: number }> }>
    // 家族项 12 的家庭卡来自显式配对（我的家庭 7，跨 owner，与家族 owner 无关）；
    // 父母家（8）解析不到所属 lineage，保留为孤立家族项
    expect(options[0]!.children.map((child) => child.value)).toEqual([12, 8])
    // 家庭页（当前在孤立的父母家 8）选择张氏家族 → 落到显式配对的家庭卡 7，不按 owner 猜
    await (wrapper.vm as unknown as { onSpaceSelect: (id: number) => Promise<void> })
      .onSpaceSelect(12)
    expect(switchSpaceMock).toHaveBeenCalledWith(7, { navigate: true })
    wrapper.unmount()
  })

  it('家族树页选择家族空间时落到对应 lineage', async () => {
    const { wrapper, pinia } = await mountShell({ path: '/family-tree' })
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [
      makeSpace({ id: 7, name: '明皇室', kind: 'household', lineage_space_id: 12 }),
      makeSpace({ id: 12, name: '朱氏皇族', kind: 'lineage', owner_id: 1 }),
      makeSpace({ id: 8, name: '徐达家', kind: 'household', owner_id: 9, lineage_space_id: 14 }),
      makeSpace({ id: 14, name: '徐氏家族', kind: 'lineage', owner_id: 9 }),
    ]
    spaces.currentSpaceId = 12
    spaces.members = [makeMember({ space_id: 12 })]
    await wrapper.vm.$nextTick()

    await (wrapper.vm as unknown as { onSpaceSelect: (id: number) => Promise<void> })
      .onSpaceSelect(14)
    expect(switchSpaceMock).toHaveBeenCalledWith(14, { navigate: true })
    wrapper.unmount()
  })

  it('启动落点不由壳二次切换：上下文变化不再触发家族对齐（09-20 跳变回归）', async () => {
    // 回归背景：壳曾监听 currentSpaceId 并把当前家族对齐到路由所需类型，于是启动
    // 决策（按优先级选 household）的产物立刻又触发一次切到 lineage，选择器先显示
    // 一个空间再跳到另一个。现在路由落点由 ensureDefaultSpace 一次决定，壳只在
    // **用户显式导航**（route.name 变化）时对齐。
    const { wrapper, pinia } = await mountShell({ path: '/family-tree' })
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [
      makeSpace({ id: 7, name: '明皇室', kind: 'household', lineage_space_id: 12 }),
      makeSpace({ id: 12, name: '朱氏皇族', kind: 'lineage', owner_id: 1 }),
    ]
    spaces.members = [makeMember({ space_id: 7 })]
    // 模拟启动决策把上下文落在配对 household（路由已是家族树，无 route.name 变化）
    spaces.currentSpaceId = 7
    await flushPromises()

    expect(switchSpaceMock).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('用户显式导航到家族树时仍对齐到该家族的 lineage（route.name 触发）', async () => {
    const { wrapper, pinia, router } = await mountShell({ path: '/' })
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [
      makeSpace({ id: 7, name: '明皇室', kind: 'household', lineage_space_id: 12 }),
      makeSpace({ id: 12, name: '朱氏皇族', kind: 'lineage', owner_id: 1 }),
    ]
    spaces.currentSpaceId = 7
    spaces.members = [makeMember({ space_id: 7 })]
    await flushPromises()

    await router.push({ name: 'family-space' })
    await flushPromises()

    expect(switchSpaceMock).toHaveBeenCalledWith(12, undefined)
    wrapper.unmount()
  })

  it('设置等空间页切换家族时保留当前页面和当前空间类型', async () => {
    const { wrapper, pinia, router } = await mountShell({ path: '/settings' })
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [
      makeSpace({ id: 7, name: '明皇室', kind: 'household', lineage_space_id: 12 }),
      makeSpace({ id: 12, name: '朱氏皇族', kind: 'lineage', owner_id: 1 }),
      makeSpace({ id: 8, name: '徐达家', kind: 'household', owner_id: 9, lineage_space_id: 14 }),
      makeSpace({ id: 14, name: '徐氏家族', kind: 'lineage', owner_id: 9 }),
    ]
    spaces.currentSpaceId = 12
    spaces.members = [makeMember({ space_id: 12 })]
    await wrapper.vm.$nextTick()

    await (wrapper.vm as unknown as { onSpaceSelect: (id: number) => Promise<void> })
      .onSpaceSelect(14)
    expect(switchSpaceMock).toHaveBeenCalledWith(14, { navigate: false })
    expect(router.currentRoute.value.name).toBe('settings')

    switchSpaceMock.mockClear()
    await (wrapper.vm as unknown as { onSpaceSelect: (id: number) => Promise<void> })
      .onSpaceSelect(12)
    expect(switchSpaceMock).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('主内容区为静态星空背景：token 派生、无动画，移动端有底部导航断点', async () => {
    const { wrapper } = await mountShell()

    // 静态背景层存在且由 token 派生（无硬编码色值）
    expect(shellSource).toContain('background-image')
    expect(shellSource).toContain('CosmicBackdrop')
    expect(shellSource).toContain('var(--fg-ink')
    // 无动画红线：不引入 keyframes/transition/animation
    expect(shellSource).not.toMatch(/animation|@keyframes|transition/)
    // 响应式契约：移动端断点 + 最小宽度收缩
    expect(shellSource).toContain('@media (max-width: 768px)')
    expect(shellSource).toContain('min-width: 0')
    expect(wrapper.find('nav[aria-label="底部导航"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('移动端壳（Phase 7 375px 契约）：底部一级导航四入口，次级入口走顶部/账号菜单', async () => {
    const { wrapper, pinia } = await mountShell()
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [makeSpace({})]
    spaces.currentSpaceId = 7
    spaces.members = [makeMember({ role: 'space_admin' })]
    await flushPromises()

    // 底部导航只渲染四个一级入口（家庭/家族树/记忆/统计）
    const bottom = wrapper.find('nav[aria-label="底部导航"]')
    expect(bottom.exists()).toBe(true)
    expect(bottom.findAll('a').map((a) => a.text().trim())).toEqual([
      '我的家庭',
      '家族树',
      '记忆',
      '统计',
    ])
    // 设置/通知/空间管理不进底部导航：通知在顶部入口，设置/管理在账号菜单
    expect(bottom.text()).not.toContain('设置')
    expect(bottom.find('[data-test="notifications-entry"]').exists()).toBe(false)
    expect(bottom.find('[data-test="space-management-link"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="notifications-entry"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="account-menu-trigger"]').exists()).toBe(true)
    // 移动端侧栏隐藏：空间选择器由顶栏承载（.topbar-picker 仅 ≤768px 显示，
    // 桌面端仍使用同一份家族选项）
    expect(wrapper.find('[data-test="space-picker-topbar"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="space-picker-mobile"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('默认空间选择以 watch(isLoggedIn) 触发：未登录挂载不发起，登录后触发（/login 重载循环回归）', async () => {
    // 回归背景：App.vue 在 router 初始导航未解析窗口内 meta 为空，壳会先于登录页
    // 挂载；未登录时发起 spaces.load 必然 401，叠加会话过期跳转曾形成 /login
    // 无限整页重载（09-01 走查实测，2320 次循环请求）。
    const pinia = createPinia()
    const router = makeRouter()
    await router.push('/')
    await router.isReady()
    // 不调用 setLoggedIn：模拟初始导航窗口内认证尚未恢复
    const wrapper = mount(AppShell, {
      global: { plugins: [pinia, router] },
      attachTo: document.body,
    })
    await flushPromises()
    expect(ensureDefaultSpaceMock).not.toHaveBeenCalled()

    // 登录完成（isLoggedIn 翻转）→ watch 触发一次默认空间选择
    setLoggedIn(useAuthStore(pinia))
    await flushPromises()
    expect(ensureDefaultSpaceMock).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('移动端壳源级契约：侧栏隐藏、选择器整行、底部导航留白含安全区、链接 52px', () => {
    // ≤768px：侧栏隐藏，主内容为底部导航预留 padding-bottom（含 iOS 安全区）
    expect(shellSource).toMatch(
      /@media \(max-width: 768px\)[\s\S]*\.shell-sidebar\s*\{\s*display: none;/,
    )
    expect(shellSource).toContain('calc(64px + env(safe-area-inset-bottom, 0px))')
    expect(shellSource).toContain('padding-bottom: env(safe-area-inset-bottom, 0px)')
    // 移动端不再重复渲染顶部空间选择器
    expect(shellSource).not.toContain('order: 10;')
    // 底部一级导航点击目标 52px ≥ 44px
    expect(shellSource).toMatch(/\.bottom-link\s*\{[\s\S]*?min-height: 52px;/)
  })
})
