/**
 * 独立系统管理员前端入口。
 *
 * 模块图完全独立于家庭 frontend/：自己的 Pinia、router、API client。
 * 只访问 /admin-api；会话过期只回本应用的 /login。
 */

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from '@/App.vue'
import { adminRouter, setupAdminRouterGuards } from '@/router'
import { wireAdminApiClient } from '@/api/client'
import { useAdminAuthStore } from '@/stores/auth'
import { useAdminAccessSessionStore } from '@/stores/accessSession'
import '@/styles/main.css'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)

// client ← store 接线（避免 api ↔ store 循环 import：provider 只暴露读取函数）
const auth = useAdminAuthStore(pinia)
const accessSessions = useAdminAccessSessionStore(pinia)

wireAdminApiClient({
  getAccessToken: () => auth.accessToken,
  getAccessSessionToken: (targetType, targetId) =>
    accessSessions.peekValid(targetType, targetId),
  tryRefresh: () => auth.tryRefresh(),
  onSessionExpired: () => {
    // 统一过期处理：清会话后回后台登录页（已在登录页则短路，防止整页重载循环）
    auth.clearSession()
    const current = adminRouter.currentRoute.value
    if (current.name !== 'login') {
      void adminRouter.replace({ name: 'login', query: { expired: '1' } })
    }
  },
})

setupAdminRouterGuards(adminRouter)
app.use(adminRouter)

void auth.restoreSession().finally(() => {
  app.mount('#app')
})
