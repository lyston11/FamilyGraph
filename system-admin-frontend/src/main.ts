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

// 唯一的启动恢复入口：在 router.install 触发首次导航之前发起，守卫会等待这笔
// 在途恢复（不会重复发起）。若这里不先发起，首次导航会自己发起恢复；两处都
// 落到同一笔单飞轮换。
void auth.restoreSession()

app.use(adminRouter)

// 不 await 恢复：必须等 router.install 的首次导航解析完成（route.meta 已就绪）
// 再 mount，否则 App.vue 会在空 meta 上误判页面壳层（历史 /login 重载竞态）。
// 首次导航失败（如部署后懒加载 chunk 失效）时 isReady 会 reject：仍必须挂载，
// 否则应用永久停在未挂载状态，并抛出未处理的 rejection。
void adminRouter
  .isReady()
  .catch(() => undefined)
  .then(() => {
    app.mount('#app')
  })
