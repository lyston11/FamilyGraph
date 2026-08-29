<script setup lang="ts">
// 应用壳骨架（design.md §3.1）：顶部导航（产品标识 / 全局搜索挂位 / 主题切换 / 用户菜单占位）。
// P0 仅骨架：搜索与用户菜单为挂位，分别随 P4-4 / P5 接入；样式全走 --fg-* token。
import { NSwitch } from 'naive-ui'

import { useUiStore } from '@/stores/ui'

const ui = useUiStore()

function onThemeSwitch(value: boolean): void {
  ui.setTheme(value ? 'modern' : 'paper')
}
</script>

<template>
  <div class="app-shell">
    <header class="shell-header">
      <div class="shell-brand">FamilyGraph</div>
      <div class="shell-search">
        <!-- 全局搜索挂位（P4-4 迁移 GlobalSearch 时接入） -->
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
}

/* 纸墨：纸感浮起导航；清雅：白底细分割线（均由主题变量驱动） */
.shell-header {
  position: sticky;
  top: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  gap: 24px;
  height: 56px;
  padding: 0 24px;
  background-color: var(--fg-surface-raised);
  border-bottom: 1px solid var(--fg-line);
  box-shadow: var(--fg-shadow-card);
}

.shell-brand {
  font-family: var(--fg-font-display);
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--fg-ink);
  white-space: nowrap;
}

.shell-search {
  flex: 1;
  max-width: 480px;
}

.shell-actions {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-left: auto;
}

.user-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
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

/* 不限内容宽度：各视图自带布局（画布需全幅），P0 不约束 */
.shell-main {
  flex: 1;
}
</style>
