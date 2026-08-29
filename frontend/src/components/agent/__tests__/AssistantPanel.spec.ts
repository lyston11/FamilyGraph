import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'
import { defineComponent, h } from 'vue'

import * as agentApi from '@/api/agent'
import AssistantPanel from '@/components/agent/AssistantPanel.vue'
import { useAgentStore } from '@/stores/agent'
import { useSpacesStore } from '@/stores/spaces'
import type { AgentRunEvent } from '@/types/agent'

/**
 * AssistantPanel：桌面抽屉 / 移动全屏共享内容层；
 * Esc 关闭、打开时焦点入面板（focus trap 宿主）、aria 标签；
 * SSE 流式渲染回归（组件级，模拟流分片）见文件尾部 describe。
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

// 可控的假流：捕获回调，测试中手动投喂事件（模拟流分片）
let streamCallbacks: { onEvent: (event: AgentRunEvent) => void } | null = null
vi.mock('@/composables/useAgentStream', () => ({
  useAgentStream: vi.fn((options: { onEvent: (event: AgentRunEvent) => void }) => {
    streamCallbacks = options
    return {
      status: { value: 'idle' },
      lastSeq: { value: 0 },
      open: vi.fn(async () => undefined),
      close: vi.fn(),
    }
  }),
}))

// PanelContent 打开时会 ensureLoaded 行动卡列表：mock 掉避免真实 XHR（stderr 噪音源）
vi.mock('@/api/actionCards', () => ({
  ACTION_CARD_ERRORS: {
    CARD_STATE_CONFLICT: 'CARD_STATE_CONFLICT',
    CARD_EXPIRED: 'CARD_EXPIRED',
    CARD_EXECUTE_REJECTED: 'CARD_EXECUTE_REJECTED',
    SPACE_FORBIDDEN_ACTOR: 'SPACE_FORBIDDEN_ACTOR',
  },
  fetchActionCards: vi.fn().mockResolvedValue([]),
  viewActionCard: vi.fn(),
  dismissActionCard: vi.fn(),
  acceptActionCard: vi.fn(),
  executeActionCard: vi.fn(),
  friendlyActionCardError: (code: string) => code,
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

  // ActionCardItem（经 MessageList 内联）内部 useMessage 需要 NMessageProvider 祖先
  const Harness = defineComponent({
    render() {
      return h('div', [h(NMessageProvider, () => h(AssistantPanel, { open }))])
    },
  })
  const wrapper = mount(Harness, {
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
  await new Promise((resolve) => setTimeout(resolve))
  return { wrapper, pinia }
}

describe('AssistantPanel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    streamCallbacks = null
    sessionStorage.clear()
    document.body.innerHTML = ''
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('open=true：渲染面板内容层并带 dialog aria 标签', async () => {
    const { wrapper } = await mountPanel(true)
    const content = document.querySelector('[data-test="assistant-panel-content"]')
    expect(content).not.toBeNull()
    expect(content?.getAttribute('aria-label')).toBe('家庭助手')
    // 人格标识位（Assistant/Steward 双人格的视觉位）
    expect(content?.querySelector('[data-test="assistant-persona"]')?.textContent).toContain('家庭助手')
    wrapper.unmount()
  })

  it('移动端全屏分支：打开时焦点入面板（focus trap 宿主）', async () => {
    const { wrapper } = await mountPanel(true, true)
    const content = document.querySelector('[data-test="assistant-panel-content"]')
    expect(document.activeElement).toBe(content)
    wrapper.unmount()
  })

  it('Esc 关闭 → 上抛 update:open=false（焦点回收由 Launcher 负责）', async () => {
    const { wrapper } = await mountPanel(true)
    const content = document.querySelector('[data-test="assistant-panel-content"]')!
    content.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await wrapper.vm.$nextTick()
    const panel = wrapper.findComponent(AssistantPanel)
    expect(panel.emitted('update:open')?.at(-1)).toEqual([false])
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
    const { wrapper } = await mountPanel(false)
    expect(mediaListeners).toBe(true)
    wrapper.unmount()
  })
})

describe('AssistantPanel：SSE 流式渲染回归（组件级，模拟流分片）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    streamCallbacks = null
    sessionStorage.clear()
    document.body.innerHTML = ''
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  function makeEvent(seq: number, type: string, payload: Record<string, unknown> = {}): AgentRunEvent {
    return { run_id: 100, seq, type, payload, created_at: '2026-08-26T00:00:00' }
  }

  it('工具 chip 与助手气泡随流事件渐进渲染；终态后思考指示消失', async () => {
    vi.mocked(agentApi.createAgentSession).mockResolvedValue({
      id: 11,
      space_id: 1,
      agent_kind: 'assistant',
      created_at: '2026-08-26T00:00:00',
    })
    vi.mocked(agentApi.createAgentMessage).mockResolvedValue({
      message: { id: 21, role: 'user', content_json: { text: '谁是我的长辈？' }, created_at: '2026-08-26T00:00:01' },
      run: { id: 100, status: 'queued', attempt: 1, cancel_requested: false },
      replayed: false,
    })

    const { wrapper, pinia } = await mountPanel(true)
    const agent = useAgentStore(pinia)

    // 发送 → 乐观气泡 + 活动 Run：先出现「思考中」指示
    await agent.sendMessage(1, '谁是我的长辈？')
    await wrapper.vm.$nextTick()
    const userBubble = document.querySelector('[data-test="message-item"][data-role="user"]')
    expect(userBubble?.textContent).toContain('谁是我的长辈？')
    expect(document.querySelector('[data-test="thinking-indicator"]')).not.toBeNull()

    // 分片 1：工具开始执行 → 工具 chip 出现
    streamCallbacks?.onEvent(
      makeEvent(1, 'tool.execution.started', { tool_call_id: 't1', tool_name: 'fg_search_space' }),
    )
    await wrapper.vm.$nextTick()
    expect(document.querySelector('[data-test="tool-chip"]')?.textContent).toContain('fg_search_space')

    // 分片 2：助手回复到达 → 气泡渲染、思考指示让位
    streamCallbacks?.onEvent(
      makeEvent(2, 'message.assistant_added', { role: 'assistant', text: '依据已确认的路径…' }),
    )
    await wrapper.vm.$nextTick()
    const assistantBubble = document.querySelector('[data-test="message-item"][data-role="assistant"]')
    expect(assistantBubble?.textContent).toContain('依据已确认的路径…')
    expect(document.querySelector('[data-test="thinking-indicator"]')).toBeNull()

    // 分片 3：终态收口 → live region 非打断播报最终回复
    streamCallbacks?.onEvent(makeEvent(3, 'run.settled'))
    await wrapper.vm.$nextTick()
    expect(document.querySelector('[data-test="live-region"]')?.textContent).toContain('依据已确认的路径…')
    wrapper.unmount()
  })
})
