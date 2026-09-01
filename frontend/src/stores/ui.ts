import { ref } from 'vue'
import { defineStore } from 'pinia'

import { isThemeName, type ThemeName } from '@/styles/tokens'

/**
 * UI 偏好 store（spec/frontend/state-management.md：localStorage 仅允许存
 * refresh token 与 UI 偏好）。持有：
 * - 主题偏好：paper（纸墨，默认）/ modern（清雅），键 `fg-theme`；
 * - 会话内最近使用的 household（仅内存，见 recentHouseholdId 注释）。
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

  /**
   * 会话内最近使用的 household（design.md §3.2）：仅内存 UI 偏好，
   * 参与默认空间选择（最近使用的 household 优先）。
   * 红线：不写 localStorage——授权事实、人员、root/viewer 一律不得持久化。
   */
  const recentHouseholdId = ref<number | null>(null)

  function setTheme(next: ThemeName): void {
    theme.value = next
    localStorage.setItem(THEME_STORAGE_KEY, next)
    document.documentElement.dataset.theme = next
  }

  function setRecentHousehold(spaceId: number): void {
    recentHouseholdId.value = spaceId
  }

  return { theme, setTheme, recentHouseholdId, setRecentHousehold }
})
