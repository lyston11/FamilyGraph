import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
    },
  ],
})

// 登录守卫占位：m0b 接入认证态后在此重定向未登录访问，
// 并处理 PIN_CHANGE_REQUIRED 强制跳转（architecture.md §1）
router.beforeEach(() => true)

export default router
