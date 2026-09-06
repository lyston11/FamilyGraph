/**
 * 独立 admin router：路由表只含后台页面（design §2）。
 *
 * guard 只读 admin auth store：
 * - 未登录 → /login（redirect 仅接受内部路径白名单）；
 * - password_must_change → 只允许 /force-change-password（其余页面拦截）；
 * - 已改密访问强制改密页 / 已登录访问登录页 → 回概览；
 * - 未知路径 → NotFound（安全空态，不暴露任何存在性）。
 *
 * 绝不 import 家庭 router / store，session expired 只回后台 /login。
 */

import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAdminAuthStore } from '@/stores/auth'
import { resolveSafeRedirect } from '@/router/safeRedirect'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/AdminLoginView.vue'),
    meta: { public: true },
  },
  {
    path: '/force-change-password',
    name: 'force-change-password',
    component: () => import('@/views/ForceChangePasswordView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/account-settings',
    name: 'account-settings',
    component: () => import('@/views/AccountSettingsView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/',
    name: 'overview',
    component: () => import('@/views/OverviewView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/space-admins',
    name: 'space-admins',
    component: () => import('@/views/SpaceAdminsView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/space-admins/:adminUserId',
    name: 'space-admin-spaces',
    component: () => import('@/views/SpaceAdminSpacesView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/spaces/:spaceId',
    name: 'space-detail',
    component: () => import('@/views/SpaceDetailView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/anomalies',
    name: 'anomaly-queue',
    component: () => import('@/views/AnomalyQueueView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/operations',
    name: 'operations',
    component: () => import('@/views/OperationsView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/agent',
    name: 'agent-monitor',
    component: () => import('@/views/AgentMonitorView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/agent-providers',
    name: 'agent-providers',
    component: () => import('@/views/AgentProviderAdminView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/audit',
    name: 'access-audit',
    component: () => import('@/views/AccessAuditView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { requiresAuth: true },
  },
]

export function createAdminRouter(): ReturnType<typeof createRouter> {
  return createRouter({
    history: createWebHistory(),
    routes,
  })
}

/** 主入口使用单例（tests 用 createAdminRouter 工厂隔离）。 */
export const adminRouter = createAdminRouter()

export function setupAdminRouterGuards(router: ReturnType<typeof createRouter>): void {
  router.beforeEach((to) => {
    const auth = useAdminAuthStore()

    if (to.name === 'login') {
      // 已登录访问登录页：按改密状态分流（避免会话内的回环）
      if (auth.isLoggedIn) {
        return auth.mustChangePassword ? { name: 'force-change-password' } : { name: 'overview' }
      }
      return true
    }

    if (!to.meta.requiresAuth) {
      return true
    }

    if (!auth.isLoggedIn) {
      return {
        name: 'login',
        query: { redirect: to.fullPath },
      }
    }

    // 首登强制改密门禁：白名单外一律拦截
    if (auth.mustChangePassword) {
      if (to.name !== 'force-change-password') {
        return { name: 'force-change-password' }
      }
      return true
    }
    if (to.name === 'force-change-password') {
      return { name: 'overview' }
    }

    return true
  })

  router.afterEach((to) => {
    // 登录页只接受内部路径 redirect；非法值在视图内解析时忽略
    if (to.name === 'login') {
      const redirect = to.query['redirect']
      if (redirect !== undefined && resolveSafeRedirect(redirect) === null) {
        const query = { ...to.query }
        delete query['redirect']
        void router.replace({ name: 'login', query })
      }
    }
  })
}
