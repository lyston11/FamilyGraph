import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { useUiStore } from '@/stores/ui'

describe('ui store（主题偏好）', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.dataset.theme = ''
    setActivePinia(createPinia())
  })

  it('初值：无存储时默认纸墨主题', () => {
    expect(useUiStore().theme).toBe('paper')
  })

  it('初值：localStorage 非法值回退 paper', () => {
    localStorage.setItem('fg-theme', 'dark')
    setActivePinia(createPinia())
    expect(useUiStore().theme).toBe('paper')
  })

  it('初值：localStorage 合法值 modern 生效', () => {
    localStorage.setItem('fg-theme', 'modern')
    setActivePinia(createPinia())
    expect(useUiStore().theme).toBe('modern')
  })

  it('setTheme：切换状态、持久化并写入 data-theme', () => {
    const ui = useUiStore()
    ui.setTheme('modern')
    expect(ui.theme).toBe('modern')
    expect(localStorage.getItem('fg-theme')).toBe('modern')
    expect(document.documentElement.dataset.theme).toBe('modern')
    ui.setTheme('paper')
    expect(localStorage.getItem('fg-theme')).toBe('paper')
    expect(document.documentElement.dataset.theme).toBe('paper')
  })

  it('重建 store 后从 localStorage 恢复主题', () => {
    useUiStore().setTheme('modern')
    setActivePinia(createPinia())
    expect(useUiStore().theme).toBe('modern')
  })

  it('recentHouseholdId：仅内存 UI 偏好，不持久化到 localStorage', () => {
    const ui = useUiStore()
    expect(ui.recentHouseholdId).toBeNull()

    ui.setRecentHousehold(8)
    expect(ui.recentHouseholdId).toBe(8)
    // 红线（design.md §3.2）：授权事实/人员/最近空间一律不落 localStorage
    expect(localStorage.length).toBe(0)

    // 新的 store 实例（模拟新会话）不恢复该偏好
    setActivePinia(createPinia())
    expect(useUiStore().recentHouseholdId).toBeNull()
  })
})
