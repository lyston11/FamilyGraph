import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import { getSafeInternalRedirect } from '@/router/redirect'

declare module 'vue-router' {
  interface RouteMeta {
    /** 'blank'：沉浸页（登录/引导/改 PIN/确档），App.vue 不渲染应用壳 */
    chrome?: 'blank'
    /** 仅独立 system_admin 主体可访问。 */
    systemAdminOnly?: boolean
    /** @deprecated 请使用 platformOperatorOnly；不得将其解释为空间管理员。 */
    adminOnly?: boolean
    /** 仅目标空间 active owner/space_admin 可访问。 */
    spaceManagerOnly?: boolean
  }
}

/**
 * 家庭用户路由表（09-01 design.md §2）：
 * - `/`（name `home`）= 我的家庭 HouseholdCardView，登录成功默认目标；
 * - `/family-tree`（name `family-space`）= 家族空间树 FamilyTreeView；
 * - 旧 `/home` 显式重定向到 `/`，不再挂载旧成员混合页。
 * route name 保持外部引用兼容：`home` 仍是登录默认目标，`family-space`
 * 沿用为家族树入口（各视图返回链接无需改动）。
 */
const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/HouseholdCardView.vue'),
    },
    {
      path: '/family-tree',
      name: 'family-space',
      component: () => import('@/views/FamilyTreeView.vue'),
    },
    {
      // 他人只读公示页：安全上下文由当前空间/快照管理，不接受 viewer/root 参数
      path: '/people/:userId',
      name: 'person-profile',
      component: () => import('@/views/PersonProfileView.vue'),
    },
    {
      path: '/notifications',
      name: 'notifications',
      component: () => import('@/views/NotificationsView.vue'),
    },
    {
      // 旧首页路径：显式重定向，不再挂载旧成员混合页（HomeView 已于 Phase 3 删除，流程已抽出复用）
      path: '/home',
      redirect: { name: 'home' },
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true, chrome: 'blank' },
    },
    {
      path: '/onboarding',
      name: 'onboarding',
      component: () => import('@/views/OnboardingView.vue'),
      meta: { public: true, chrome: 'blank' },
    },
    {
      path: '/force-change-pin',
      name: 'force-change-pin',
      component: () => import('@/views/ChangePinView.vue'),
      meta: { chrome: 'blank' },
    },
    {
      path: '/identity-setup',
      name: 'identity-setup',
      component: () => import('@/views/IdentitySetupView.vue'),
      meta: { chrome: 'blank' },
    },
    {
      path: '/stats',
      name: 'stats',
      component: () => import('@/views/StatsView.vue'),
    },
    {
      path: '/system-admin',
      name: 'system-admin',
      component: () => import('@/views/SystemAdminView.vue'),
      meta: { systemAdminOnly: true },
    },
    {
      // 系统管理员登录占位（PRD §2.7）：独立于家庭登录页（blank chrome、无家庭壳），
      // 真实登录流程由 09-01-system-admin-governance-routes 任务实现。
      path: '/system-admin/login',
      name: 'system-admin-login',
      component: () => import('@/views/SystemAdminLoginView.vue'),
      meta: { public: true, chrome: 'blank' },
    },
    {
      path: '/admin',
      redirect: { name: 'system-admin' },
    },
    {
      path: '/spaces/:spaceId/manage',
      name: 'space-management',
      component: () => import('@/views/SpaceManagementView.vue'),
      meta: { spaceManagerOnly: true },
    },
    {
      path: '/memory',
      name: 'memory',
      component: () => import('@/views/MemoryView.vue'),
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/SettingsView.vue'),
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

/**
 * 认证守卫（architecture.md §1 + state-management.md 红线）：
 * 1. 首启未初始化 → 一律进引导页（公开页除外）
 * 2. 未登录 → /login；已登录访问 /login → /（name home，我的家庭）
 * 3. system_admin / family_user 互斥：系统主体只能停留在系统后台，
 *    家庭主体（含家庭壳任何页面）不得进入系统后台路由
 * 4. pin_must_change=true → 白名单外强制跳改 PIN 页
 */
router.beforeEach(async (to) => {
  const auth = useAuthStore()

  // 首启探测：仅首次会话请求一次
  await auth.checkBootstrap()
  if (!auth.systemInitialized) {
    return to.name === 'onboarding' ? true : { name: 'onboarding' }
  }

  // 恢复会话：内存无 access 但 localStorage 有 refresh（硬刷新场景）。
  // 必须先恢复再做权限判断，否则平台运营者硬刷新 /admin 会被误判成普通用户。
  if (!auth.isLoggedIn && auth.refreshToken) {
    await auth.resume()
  }

  if (to.meta.public) {
    // 已登录主体访问系统管理员登录占位：系统管理员回后台，家庭用户按互斥回家庭壳
    if (auth.isLoggedIn && to.name === 'system-admin-login') {
      return auth.isSystemAdmin ? { name: 'system-admin' } : { name: 'family-space' }
    }
    if (auth.isLoggedIn && to.name === 'login') {
      return { name: 'home' }
    }
    return true
  }

  if (!auth.isLoggedIn) {
    const redirect = to.fullPath !== '/' ? to.fullPath : undefined
    return {
      name: 'login',
      query: redirect ? { redirect } : undefined,
    }
  }

  // 主体互斥（design.md §6）：系统管理员 token 不能进入家庭用户壳的任何页面；
  // 家庭用户 token 不能进入系统后台路由（含后台登录占位页）。
  if (auth.isSystemAdmin && to.name !== 'system-admin' && to.name !== 'force-change-pin') {
    return { name: 'system-admin' }
  }
  if (
    !auth.isSystemAdmin &&
    (to.name === 'system-admin' || to.name === 'system-admin-login' || to.meta.systemAdminOnly)
  ) {
    return { name: 'family-space' }
  }

  if (to.meta.spaceManagerOnly) {
    const spaces = useSpacesStore()
    const rawSpaceId = to.params.spaceId
    const targetSpaceId = typeof rawSpaceId === 'string' ? Number(rawSpaceId) : NaN
    // 路由守卫只依赖已加载的 active membership；平台运营者不因平台身份获得空间权限。
    if (!Number.isInteger(targetSpaceId) || targetSpaceId <= 0) {
      return { name: 'family-space' }
    }
    if (spaces.spaces.length === 0) {
      await spaces.load().catch(() => undefined)
    }
    if (!spaces.spaces.some((space) => space.id === targetSpaceId)) {
      return { name: 'family-space' }
    }
    // 每次进入都重新确认目标空间的 active membership，避免旧空间/旧会话缓存放行。
    // 成员请求失败时也必须拒绝（fail-closed），不能拿旧缓存继续做授权判断。
    try {
      await spaces.loadMembers(targetSpaceId)
    } catch {
      return { name: 'family-space' }
    }
    if (spaces.currentSpaceId !== targetSpaceId || !spaces.canManageSpace) {
      return { name: 'family-space' }
    }
  }

  const redirect = getSafeInternalRedirect(to.query.redirect)
  const hasRedirectQuery = typeof to.query.redirect !== 'undefined'
  const originalTarget =
    redirect ??
    (!hasRedirectQuery && to.name !== 'force-change-pin' && to.name !== 'identity-setup'
      ? to.fullPath
      : undefined)

  // 首登强制改 PIN：仅放行改 PIN 页自身，并保留原始安全回跳地址。
  if (auth.mustChangePin && to.name !== 'force-change-pin') {
    return {
      name: 'force-change-pin',
      query: originalTarget ? { redirect: originalTarget } : undefined,
    }
  }

  // v2 确档向导（F-1，Gap2）：/me 直出 profile_status，本人档案 provisional →
  // 引导完成「这是我」+清单。判定源是登录/刷新响应自带的身份状态（同步、无额外
  // 请求），不再由 fact-reviews 推断，也无 fail-open 兑底；清单内容仍由
  // IdentitySetupView 自行拉取。
  if (auth.user?.profile_status === 'provisional' && to.name !== 'identity-setup') {
    return {
      name: 'identity-setup',
      query: originalTarget ? { redirect: originalTarget } : undefined,
    }
  }

  return true
})

export default router
