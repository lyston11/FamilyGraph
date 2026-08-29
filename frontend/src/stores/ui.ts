import { ref } from 'vue'
import { defineStore } from 'pinia'

import { isThemeName, type ThemeName } from '@/styles/tokens'

/**
 * UI 偏好 store（spec/frontend/state-management.md：localStorage 仅允许存
 * refresh token 与 UI 偏好）。当前持有主题偏好：
 * - paper（纸墨，默认）/ modern（清雅），键 `fg-theme`。
 * 本应用无 SSR，可直接操作 document；`<html data-theme>` 与 L2 CSS 变量注入
 * （App.vue watchEffect）共同保证主题切换即时生效、不刷新页面。
 */
const THEME_STORAGE_KEY = 'fg-theme'

function readStoredTheme(): ThemeName {
  const raw = localStorage.getItem(THEME_STORAGE_KEY)
  return raw !== null && isThemeName(raw) ? raw : 'paper'
}

export const useUiStore = defineStore('ui', () => {
  const theme = ref<ThemeName>(readStoredTheme())

  // store 创建即同步 data-theme，保证硬刷新后首帧标识正确
  document.documentElement.dataset.theme = theme.value

  function setTheme(next: ThemeName): void {
    theme.value = next
    localStorage.setItem(THEME_STORAGE_KEY, next)
    document.documentElement.dataset.theme = next
  }

  return { theme, setTheme }
})
