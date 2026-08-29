<script setup lang="ts">
// 应用壳（design.md §3.1）：顶部导航（产品标识 / 全局搜索 / 主题切换 / 用户菜单占位）。
// 导航链接保持稳定的命名路由入口，方便在各页面之间往返。
import { NSwitch } from 'naive-ui'
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import GlobalSearch from '@/components/common/GlobalSearch.vue'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import { useUiStore } from '@/stores/ui'

const auth = useAuthStore()
const spaces = useSpacesStore()
const route = useRoute()
const ui = useUiStore()
const currentSpaceManagementTarget = computed(() =>
  spaces.currentSpace ? { name: 'space-management', params: { spaceId: spaces.currentSpace.id } } : { name: 'family-space' },
)

function onThemeSwitch(value: boolean): void {
  ui.setTheme(value ? 'modern' : 'paper')
}
</script>

<template>
  <div class="app-shell">
    <header class="shell-header">
      <RouterLink
        class="shell-brand"
        :to="{ name: 'family-space' }"
        active-class="shell-brand--active"
        aria-label="FamilyGraph 家庭空间"
      >
        FamilyGraph
      </RouterLink>

      <nav class="shell-nav" aria-label="主导航">
        <RouterLink
          class="nav-link"
          :to="{ name: 'family-space' }"
          active-class="nav-link--active"
          exact-active-class="nav-link--active"
          :aria-current="route.name === 'family-space' ? 'page' : undefined"
        >
          家庭空间
        </RouterLink>
        <RouterLink
          class="nav-link"
          :to="{ name: 'home' }"
          active-class="nav-link--active"
          exact-active-class="nav-link--active"
          :aria-current="route.name === 'home' ? 'page' : undefined"
        >
          成员
        </RouterLink>
        <RouterLink
          class="nav-link"
          :to="{ name: 'stats' }"
          active-class="nav-link--active"
          exact-active-class="nav-link--active"
          :aria-current="route.name === 'stats' ? 'page' : undefined"
        >
          统计
        </RouterLink>
        <RouterLink
          class="nav-link"
          :to="{ name: 'memory' }"
          active-class="nav-link--active"
          exact-active-class="nav-link--active"
          :aria-current="route.name === 'memory' ? 'page' : undefined"
        >
          记忆与知识
        </RouterLink>
        <RouterLink
          class="nav-link"
          :to="{ name: 'settings' }"
          active-class="nav-link--active"
          exact-active-class="nav-link--active"
          :aria-current="route.name === 'settings' ? 'page' : undefined"
        >
          设置
        </RouterLink>
        <RouterLink
          v-if="auth.isPlatformOperator"
          class="nav-link"
          :to="{ name: 'admin' }"
          active-class="nav-link--active"
          exact-active-class="nav-link--active"
          :aria-current="route.name === 'admin' ? 'page' : undefined"
        >
          平台运营后台
        </RouterLink>
        <RouterLink
          v-if="spaces.canManageSpace && spaces.currentSpace"
          class="nav-link"
          :to="currentSpaceManagementTarget"
          active-class="nav-link--active"
          exact-active-class="nav-link--active"
          :aria-current="route.name === 'space-management' ? 'page' : undefined"
          data-test="space-management-link"
        >
          空间管理
        </RouterLink>
      </nav>

      <div class="shell-search">
        <GlobalSearch />
      </div>
      <div class="shell-actions">
        <NSwitch
          class="theme-switch"
          :value="ui.theme === 'modern'"
          aria-label="切换配色主题（纸墨 / 清雅）"
          @update:value="onThemeSwitch"
        >
          <template #checked>清雅</template>
          <template #unchecked>纸墨</template>
        </NSwitch>
        <button class="user-button" type="button" aria-label="用户菜单（占位）">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true">
            <path
              d="M12 12a4.5 4.5 0 1 0-4.5-4.5A4.5 4.5 0 0 0 12 12Zm0 2.25c-3.6 0-7.5 1.8-7.5 4.5v1.13c0 .48.39.87.87.87h13.26c.48 0 .87-.39.87-.87v-1.13c0-2.7-3.9-4.5-7.5-4.5Z"
            />
          </svg>
        </button>
      </div>
    </header>
    <main class="shell-main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  min-width: 0;
}

/* 纸墨：纸感浮起导航；清雅：白底细分割线（均由主题变量驱动） */
.shell-header {
  position: sticky;
  top: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 16px;
  min-height: 56px;
  padding: 8px 24px;
  box-sizing: border-box;
  background-color: var(--fg-surface-raised);
  border-bottom: 1px solid var(--fg-line);
  box-shadow: var(--fg-shadow-card);
}

.shell-brand {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  flex: 0 0 auto;
  font-family: var(--fg-font-display);
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--fg-ink);
  text-decoration: none;
  white-space: nowrap;
}

.shell-brand--active {
  color: var(--fg-accent);
}

.shell-nav {
  display: flex;
  align-items: center;
  gap: 2px;
  min-width: 0;
  flex: 1 1 auto;
  overflow-x: auto;
  scrollbar-width: thin;
}

.nav-link {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  flex: 0 0 auto;
  padding: 8px 10px;
  box-sizing: border-box;
  border-radius: var(--fg-radius-control);
  color: var(--fg-ink-secondary);
  font-size: 13px;
  text-decoration: none;
  white-space: nowrap;
}

.nav-link:hover {
  color: var(--fg-ink);
  background-color: var(--fg-surface-sunken);
}

.nav-link--active {
  color: var(--fg-accent);
  background-color: var(--fg-accent-soft);
  font-weight: 600;
}

.shell-search {
  min-width: 0;
  flex: 1 1 180px;
  max-width: 320px;
}

.shell-search :deep(.global-search) {
  min-width: 0;
}

.shell-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 0 1 auto;
  margin-left: auto;
}

.theme-switch {
  flex-shrink: 1;
}

.user-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  flex: 0 0 auto;
  padding: 0;
  border: 1px solid var(--fg-line);
  border-radius: 50%;
  background-color: var(--fg-surface);
  color: var(--fg-ink-secondary);
  cursor: pointer;
}

.user-button:hover {
  border-color: var(--fg-line-strong);
  color: var(--fg-ink);
}

/* 不限内容宽度：各视图自带布局（画布需全幅），壳只负责收缩 */
.shell-main {
  min-width: 0;
  flex: 1;
}

@media (max-width: 768px) {
  .shell-header {
    gap: 8px 12px;
    padding: 8px 12px;
  }

  .shell-nav {
    order: 4;
    flex-basis: 100%;
  }

  .shell-search {
    flex: 1 1 120px;
    max-width: none;
  }
}

@media (max-width: 480px) {
  .shell-header {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    grid-template-areas:
      'brand actions'
      'search search'
      'nav nav';
    align-items: center;
    gap: 8px 12px;
  }

  .shell-brand {
    grid-area: brand;
  }

  .shell-search {
    grid-area: search;
    width: 100%;
  }

  .shell-actions {
    grid-area: actions;
    margin-left: 0;
  }

  .shell-nav {
    grid-area: nav;
    width: 100%;
  }
}
</style>
