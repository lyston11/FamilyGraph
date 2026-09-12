<script setup lang="ts">
/**
 * 后台壳：只含 admin 导航 + 账号动作。
 * 零家庭依赖（不 import 家庭 store / 组件 / API）；登出后回后台 /login。
 *
 * 移动端响应式（R4，09-11 整改）：≤768px 桌面横向导航折叠为「菜单」按钮
 * + 展开面板（aria-expanded 驱动）；面板内链接 ≥44px 点按目标；路由切换
 * 与 Escape 自动收起；开启时锁 body 滚动避免页面级横向滚动。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NDropdown } from 'naive-ui'
import { useAdminAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAdminAuthStore()
const loggingOut = ref(false)
const mobileNavOpen = ref(false)

const adminLabel = computed(() => auth.admin?.username ?? '')

const accountOptions = [
  { key: 'settings', label: '账号设置' },
  { key: 'logout', label: '退出登录' },
] as const

// 路由切换（含移动端菜单选择后）自动收起导航面板
watch(
  () => route.fullPath,
  () => {
    mobileNavOpen.value = false
  },
)

// 面板开启时锁定 body 滚动（对齐抽屉语义），关闭时恢复
watch(mobileNavOpen, (open) => {
  document.body.classList.toggle('ag-nav-locked', open)
})

function toggleMobileNav(): void {
  mobileNavOpen.value = !mobileNavOpen.value
}

function onNavKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && mobileNavOpen.value) {
    mobileNavOpen.value = false
  }
}

onBeforeUnmount(() => {
  document.body.classList.remove('ag-nav-locked')
})

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
  <div class="admin-shell" @keydown="onNavKeydown">
    <header class="admin-header">
      <span class="admin-brand">FamilyGraph 系统管理后台</span>
      <button
        type="button"
        class="admin-nav-toggle"
        data-testid="mobile-nav-toggle"
        :aria-expanded="mobileNavOpen"
        aria-controls="admin-nav-panel"
        aria-label="导航菜单"
        @click="toggleMobileNav"
      >
        菜单
      </button>
      <nav
        id="admin-nav-panel"
        class="admin-nav"
        :class="{ 'admin-nav--open': mobileNavOpen }"
        aria-label="主导航"
      >
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
        <button
          type="button"
          class="ag-tag"
          data-testid="account-menu"
          aria-haspopup="menu"
          :aria-label="`账号菜单：${adminLabel || '当前管理员'}`"
        >
          {{ adminLabel }}
        </button>
      </NDropdown>
    </header>
    <main class="admin-main">
      <RouterView />
    </main>
  </div>
</template>
