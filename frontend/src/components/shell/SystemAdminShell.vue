<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

/**
 * 系统后台壳（SAR-F3）：只承载治理导航与 RouterView，不渲染家庭导航、
 * 关系图、Memory、Session 或任何家庭数据入口。登出撤销当前 system_admin
 * 主体的 refresh session（后端按主体分流）并清空系统管理员会话与家庭
 * 缓存残留，随后回 `/system-admin/login`。
 */
const auth = useAuthStore()
const router = useRouter()
const loggingOut = ref(false)

async function logout(): Promise<void> {
  if (loggingOut.value) return
  loggingOut.value = true
  try {
    // auth.logout 经 /auth/logout 撤销对应主体的 refresh session；
    // clearSession 同步清空凭据、家庭敏感 stores 与 localStorage refresh token
    await auth.logout()
  } finally {
    loggingOut.value = false
    await router.replace({ name: 'system-admin-login' })
  }
}
</script>

<template>
  <div class="system-admin-shell">
    <header class="system-admin-header">
      <RouterLink class="system-admin-brand" :to="{ name: 'system-admin' }">FamilyGraph · 系统治理</RouterLink>
      <nav aria-label="系统管理导航">
        <RouterLink :to="{ name: 'system-admin' }">治理概览</RouterLink>
      </nav>
      <div class="system-admin-header-end">
        <button
          class="system-admin-logout"
          type="button"
          data-test="system-admin-logout"
          :disabled="loggingOut"
          @click="logout"
        >
          {{ loggingOut ? '登出中…' : '登出' }}
        </button>
      </div>
    </header>
    <main><RouterView /></main>
  </div>
</template>

<style scoped>
.system-admin-shell { min-height: 100vh; background: var(--fg-surface); color: var(--fg-ink); }
.system-admin-header { display: flex; align-items: center; gap: 28px; min-height: 64px; padding: 0 28px; border-bottom: 1px solid var(--fg-line); background: var(--fg-surface-raised); }
.system-admin-brand { color: var(--fg-ink); font-family: var(--fg-font-display); font-weight: 700; text-decoration: none; }
nav a { color: var(--fg-ink-secondary); font-size: 13px; text-decoration: none; }
nav a.router-link-active { color: var(--fg-accent); }
.system-admin-header-end { margin-left: auto; }
.system-admin-logout {
  padding: 6px 14px;
  font-size: 13px;
  color: var(--fg-ink-secondary);
  background-color: transparent;
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-control);
  cursor: pointer;
}
.system-admin-logout:hover:not(:disabled) { color: var(--fg-ink); border-color: var(--fg-accent); }
.system-admin-logout:disabled { opacity: 0.6; cursor: default; }
main { min-width: 0; }
</style>
