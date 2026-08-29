import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'
import { defineComponent, h } from 'vue'

import * as agentApi from '@/api/agent'
import AssistantLauncher from '@/components/agent/AssistantLauncher.vue'
import { useAgentStore } from '@/stores/agent'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import type { UserOut } from '@/types/api'

/**
 * AssistantLauncher（AC-AS1 显隐策略 + 跨 scope 对抗）：
 * - 未登录 / 强制改 PIN / 确档向导 → 隐藏；
 * - 无可用空间 → 可见但禁用；
 * - currentSpaceId 变化 → resetForSpace 清旧 scope、ensureSpace 装载新 scope。
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

const streamClose = vi.fn()
vi.mock('@/composables/useAgentStream', () => ({
  useAgentStream: vi.fn(() => ({
    status: { value: 'idle' },
    lastSeq: { value: 0 },
    open: vi.fn(),
    close: streamClose,
  })),
}))

// 面板打开会 ensureLoaded 行动卡列表：mock 掉避免真实 XHR（stderr 噪音源）
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

// jsdom 无 matchMedia：Panel 需要
function stubMatchMedia(matches = false): void {
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  )
}

function makeUser(overrides: Partial<UserOut> = {}): UserOut {
  return {
    id: 1,
    name: '我',
    is_admin: false,
    pin_must_change: false,
    claim_status: 'claimed',
    profile_status: 'identity_confirmed',
    ...overrides,
  }
}

async function loginAndSeedSpace(pinia: Pinia, spaceId = 1): Promise<void> {
  const auth = useAuthStore(pinia)
  auth.accessToken = 'tok'
  auth.user = makeUser()
  const spaces = useSpacesStore(pinia)
  spaces.spaces = [
    { id: spaceId, name: '我家', owner_id: 1, kind: 'household', created_at: '2026-08-26T00:00:00', pending_count: 0, member_count: 1 },
  ]
  spaces.currentSpaceId = spaceId
}

// Launcher 内嵌 Panel（经 NDrawer 渲染 ActionCardItem 链路），useMessage 需 provider 祖先
function mountLauncher(pinia: Pinia) {
  const Harness = defineComponent({
    render() {
      return h('div', [h(NMessageProvider, () => h(AssistantLauncher))])
    },
  })
  return mount(Harness, { global: { plugins: [pinia] }, attachTo: document.body })
}

describe('AssistantLauncher', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    sessionStorage.clear()
    stubMatchMedia(false)
  })

  it('未登录 → 隐藏', () => {
    const pinia = createPinia()
    const wrapper = mountLauncher(pinia)
    expect(wrapper.find('[data-test="assistant-launcher"]').exists()).toBe(false)
  })

  it('强制改 PIN / 确档向导中 → 隐藏（AS-3 策略）', async () => {
    const pinia = createPinia()
    await loginAndSeedSpace(pinia)
    useAuthStore(pinia).user = makeUser({ pin_must_change: true })
    let wrapper = mountLauncher(pinia)
    expect(wrapper.find('[data-test="assistant-launcher"]').exists()).toBe(false)
    wrapper.unmount()

    useAuthStore(pinia).user = makeUser({ profile_status: 'provisional' })
    wrapper = mountLauncher(pinia)
    expect(wrapper.find('[data-test="assistant-launcher"]').exists()).toBe(false)
  })

  it('已登录无空间 → 可见但禁用；有空间 → 可用并可开关面板', async () => {
    const pinia = createPinia()
    const auth = useAuthStore(pinia)
    auth.accessToken = 'tok'
    auth.user = makeUser()
    // 不放任何空间
    let wrapper = mountLauncher(pinia)
    let btn = wrapper.find('[data-test="assistant-launcher"]')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('disabled')).toBeDefined()
    wrapper.unmount()

    await loginAndSeedSpace(pinia)
    wrapper = mountLauncher(pinia)
    btn = wrapper.find('[data-test="assistant-launcher"]')
    expect(btn.attributes('disabled')).toBeUndefined()
    expect(btn.attributes('aria-expanded')).toBe('false')
    await btn.trigger('click')
    expect(wrapper.find('[data-test="assistant-launcher"]').attributes('aria-expanded')).toBe('true')
    // 关闭后焦点回 launcher（a11y：focus 回收）
    await wrapper.find('[data-test="assistant-launcher"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve))
    const rawBtn = document.querySelector('[data-test="assistant-launcher"]')
    expect(document.activeElement).toBe(rawBtn)
    wrapper.unmount()
  })

  it('切换空间：旧 scope 分区被清除且流被关闭，新 scope 装载会话（跨 scope 对抗）', async () => {
    const pinia = createPinia()
    await loginAndSeedSpace(pinia, 1)
    const wrapper = mountLauncher(pinia)
    await new Promise((resolve) => setTimeout(resolve))
    expect(useAgentStore(pinia).partitions.get(1)).toBeDefined()

    const spaces = useSpacesStore(pinia)
    spaces.spaces.push({
      id: 2, name: '宗族', owner_id: 1, kind: 'lineage',
      created_at: '2026-08-26T00:00:00', pending_count: 0, member_count: 3,
    })
    spaces.currentSpaceId = 2
    await new Promise((resolve) => setTimeout(resolve))

    const agent = useAgentStore(pinia)
    expect(agent.partitions.has(1)).toBe(false)
    // 流关闭断言见 stores/agent.spec.ts（有活动 Run 的场景）
    expect(vi.mocked(agentApi.fetchAgentSessions)).toHaveBeenCalledWith(2)
    wrapper.unmount()
  })
})
