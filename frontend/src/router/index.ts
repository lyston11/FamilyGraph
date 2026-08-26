import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'family-space',
      component: () => import('@/views/FamilySpaceView.vue'),
    },
    {
      path: '/home',
      name: 'home',
      component: () => import('@/views/HomeView.vue'),
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/onboarding',
      name: 'onboarding',
      component: () => import('@/views/OnboardingView.vue'),
      meta: { public: true },
    },
    {
      path: '/force-change-pin',
      name: 'force-change-pin',
      component: () => import('@/views/ChangePinView.vue'),
    },
    {
      path: '/identity-setup',
      name: 'identity-setup',
      component: () => import('@/views/IdentitySetupView.vue'),
    },
    {
      path: '/stats',
      name: 'stats',
      component: () => import('@/views/StatsView.vue'),
    },
    {
      path: '/admin',
      name: 'admin',
      component: () => import('@/views/AdminView.vue'),
      meta: { adminOnly: true },
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
 * 2. 未登录 → /login；已登录访问 /login → /
 * 3. pin_must_change=true → 白名单外强制跳改 PIN 页
 */
router.beforeEach(async (to) => {
  const auth = useAuthStore()

  // 管理员路由双重校验（后端 API 另有 403）
  if (to.meta.adminOnly && auth.user?.is_admin !== true) {
    return { name: 'family-space' }
  }


  // 首启探测：仅首次会话请求一次
  await auth.checkBootstrap()
  if (!auth.systemInitialized) {
    return to.name === 'onboarding' ? true : { name: 'onboarding' }
  }

  // 恢复会话：内存无 access 但 localStorage 有 refresh（硬刷新场景）
  if (!auth.isLoggedIn && auth.refreshToken) {
    await auth.resume()
  }

  if (to.meta.public) {
    if (auth.isLoggedIn && to.name === 'login') {
      return { name: 'home' }
    }
    return true
  }

  if (!auth.isLoggedIn) {
    return { name: 'login', query: to.fullPath !== '/' ? { redirect: to.fullPath } : undefined }
  }

  // 首登强制改 PIN：仅放行改 PIN 页自身
  if (auth.mustChangePin && to.name !== 'force-change-pin') {
    return { name: 'force-change-pin' }
  }

  // v2 确档向导（F-1，Gap2）：/me 直出 profile_status，本人档案 provisional →
  // 引导完成「这是我」+清单。判定源是登录/刷新响应自带的身份状态（同步、无额外
  // 请求），不再由 fact-reviews 推断，也无 fail-open 兑底；清单内容仍由
  // IdentitySetupView 自行拉取。
  if (auth.user?.profile_status === 'provisional' && to.name !== 'identity-setup') {
    return { name: 'identity-setup' }
  }

  return true
})

export default router
