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
})
