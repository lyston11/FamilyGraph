<script setup lang="ts">
/**
 * 后台壳：只含 admin 导航 + 账号动作。
 * 零家庭依赖（不 import 家庭 store / 组件 / API）；登出后回后台 /login。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NDropdown } from 'naive-ui'
import { useAdminAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAdminAuthStore()
const loggingOut = ref(false)

const adminLabel = computed(() => auth.admin?.username ?? '')

const accountOptions = [
  { key: 'settings', label: '账号设置' },
  { key: 'logout', label: '退出登录' },
] as const

async function onAccountAction(key: string): Promise<void> {
  if (key === 'settings') {
    await router.push({ name: 'account-settings' })
    return
  }
  if (key === 'logout') {
    loggingOut.value = true
    try {
      // logout 内部清空 admin store、refresh key 与 access-session 内存票据
      await auth.logout()
    } finally {
      loggingOut.value = false
      await router.replace({ name: 'login' })
    }
  }
}
</script>

<template>
  <div class="admin-shell">
    <header class="admin-header">
      <span class="admin-brand">FamilyGraph 系统管理后台</span>
      <nav class="admin-nav" aria-label="主导航">
        <RouterLink :to="{ name: 'overview' }">概览</RouterLink>
        <RouterLink :to="{ name: 'space-admins' }">空间管理员</RouterLink>
        <RouterLink :to="{ name: 'anomaly-queue' }">异常队列</RouterLink>
        <RouterLink :to="{ name: 'operations' }">运营治理</RouterLink>
        <RouterLink :to="{ name: 'agent-monitor' }">Agent 监控</RouterLink>
        <RouterLink :to="{ name: 'agent-providers' }">模型治理</RouterLink>
        <RouterLink :to="{ name: 'access-audit' }">读取审计</RouterLink>
      </nav>
      <NDropdown
        trigger="click"
        :options="[...accountOptions]"
        @select="onAccountAction"
      >
        <button type="button" class="ag-tag" data-testid="account-menu">
          {{ adminLabel }}
        </button>
      </NDropdown>
    </header>
    <main class="admin-main">
      <RouterView />
    </main>
  </div>
</template>
