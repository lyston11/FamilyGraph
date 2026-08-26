import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ElementPlus from 'element-plus'

import AssistantPanel from '@/components/agent/AssistantPanel.vue'
import { useAgentStore } from '@/stores/agent'
import { useSpacesStore } from '@/stores/spaces'

/**
 * AssistantPanel：桌面抽屉 / 移动全屏共享内容层；
 * Esc 关闭、打开时焦点入面板（focus trap 宿主）、aria 标签。
 */

vi.mock('@/api/agent', () => ({
  CLIENT_AGENT_ERRORS: {
    STREAM_LOST: 'STREAM_LOST',
    AUTH_EXPIRED: 'AUTH_EXPIRED',
    RUN_FAILED: 'RUN_FAILED',
    SEND_FAILED: 'SEND_FAILED',
  },
  friendlyAgentError: vi.fn((code: string) => `copy:${code}`),
  createAgentSession: vi.fn(),
  fetchAgentSessions: vi.fn().mockResolvedValue([]),
  fetchAgentMessages: vi.fn().mockResolvedValue([]),
  createAgentMessage: vi.fn(),
  fetchAgentRun: vi.fn(),
  cancelAgentRun: vi.fn(),
}))

let mediaListeners = false
function stubMatchMedia(matches: boolean): void {
  mediaListeners = false
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      addEventListener: () => {
        mediaListeners = true
      },
      removeEventListener: vi.fn(),
    })),
  )
}

async function mountPanel(open: boolean, mobile = false) {
  stubMatchMedia(mobile)
  const pinia = createPinia()
  const spaces = useSpacesStore(pinia)
  spaces.spaces = [
    { id: 1, name: '我家', owner_id: 1, kind: 'household', created_at: '2026-08-26T00:00:00', pending_count: 0, member_count: 1 },
  ]
  spaces.currentSpaceId = 1
  await useAgentStore(pinia).ensureSpace(1)

  const wrapper = mount(AssistantPanel, {
    props: { open },
    global: { plugins: [pinia, ElementPlus] },
    attachTo: document.body,
  })
  await new Promise((resolve) => setTimeout(resolve))
  return wrapper
}

describe('AssistantPanel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    sessionStorage.clear()
    document.body.innerHTML = ''
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('open=true：渲染面板内容层并带 dialog aria 标签', async () => {
    const wrapper = await mountPanel(true)
    const content = document.querySelector('[data-test="assistant-panel-content"]')
    expect(content).not.toBeNull()
    expect(content?.getAttribute('aria-label')).toBe('家庭助手')
    wrapper.unmount()
  })

  it('移动端全屏分支：打开时焦点入面板（focus trap 宿主）', async () => {
    const wrapper = await mountPanel(true, true)
    const content = document.querySelector('[data-test="assistant-panel-content"]')
    expect(document.activeElement).toBe(content)
    wrapper.unmount()
  })

  it('Esc 关闭 → 上抛 update:open=false（焦点回收由 Launcher 负责）', async () => {
    const wrapper = await mountPanel(true)
    const content = document.querySelector('[data-test="assistant-panel-content"]')!
    content.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('update:open')?.at(-1)).toEqual([false])
    wrapper.unmount()
  })

  it('ScopeBanner 在面板内始终可见且显示当前空间', async () => {
    await mountPanel(true)
    const banner = document.querySelector('[data-test="scope-banner"]')
    expect(banner?.textContent).toContain('我家')
    expect(banner?.textContent).toContain('家庭空间')
  })

  it('移动端 <768px：Teleport 全屏面板而非固定宽度抽屉', async () => {
    await mountPanel(true, true)
    expect(document.querySelector('[data-test="assistant-panel-mobile"]')).not.toBeNull()
    expect(document.querySelector('[data-test="scope-banner"]')).not.toBeNull()
  })

  it('matchMedia 变更监听已注册（响应式容器切换）', async () => {
    const wrapper = await mountPanel(false)
    expect(mediaListeners).toBe(true)
    wrapper.unmount()
  })
})
