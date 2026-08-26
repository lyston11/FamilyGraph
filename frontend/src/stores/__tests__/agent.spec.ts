import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as agentApi from '@/api/agent'
import { truncateSessionTitle, useAgentStore } from '@/stores/agent'
import type { AgentRunEvent } from '@/types/agent'

/**
 * stores/agent 合同测试：
 * - 按 space_id 分区隔离（AC-AS2）；
 * - 空间切换 resetForSpace 清空旧 scope 且关闭旧流（跨 scope 对抗）；
 * - auth.clearSession() 联动 clear()（AC-AS7）；
 * - 事件投影：消息合流去重、工具摘要、终态收口、刷新恢复（AC-AS6）。
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

// 可控的假流：捕获回调，测试中手动投喂事件
type StreamCallbacks = {
  onEvent: (event: AgentRunEvent) => void
  onEnded: (info: { reason: string }) => void
}
let streamCallbacks: StreamCallbacks | null = null
const streamOpen = vi.fn(async () => undefined)
const streamClose = vi.fn()

vi.mock('@/composables/useAgentStream', () => ({
  useAgentStream: vi.fn((options: StreamCallbacks) => {
    streamCallbacks = options
    return {
      status: { value: 'idle' },
      lastSeq: { value: 0 },
      open: streamOpen,
      close: streamClose,
    }
  }),
}))

const mockedCreateSession = vi.mocked(agentApi.createAgentSession)
const mockedFetchSessions = vi.mocked(agentApi.fetchAgentSessions)
const mockedFetchMessages = vi.mocked(agentApi.fetchAgentMessages)
const mockedCreateMessage = vi.mocked(agentApi.createAgentMessage)
const mockedFetchRun = vi.mocked(agentApi.fetchAgentRun)

function makeEvent(seq: number, type: string, payload: Record<string, unknown> = {}): AgentRunEvent {
  return { run_id: 100, seq, type, payload, created_at: '2026-08-26T00:00:00' }
}

async function seedSpaceWithRun(store: ReturnType<typeof useAgentStore>, spaceId = 1): Promise<void> {
  mockedFetchSessions.mockResolvedValue([])
  await store.ensureSpace(spaceId)
  mockedCreateSession.mockResolvedValue({
    id: 11,
    space_id: spaceId,
    agent_kind: 'assistant',
    created_at: '2026-08-26T00:00:00',
  })
  mockedCreateMessage.mockResolvedValue({
    message: { id: 21, role: 'user', content_json: { text: '谁是我的长辈？' }, created_at: '2026-08-26T00:00:01' },
    run: { id: 100, status: 'queued', attempt: 1, cancel_requested: false },
    replayed: false,
  })
  await store.sendMessage(spaceId, '谁是我的长辈？')
}

describe('truncateSessionTitle（会话标题纯展示，AS-1）', () => {
  it('首条用户消息截断到 24 字并加省略号', () => {
    const long = '这是一个超过二十四个字的很长很长的用户问题需要被截断处理掉后面部分'
    expect(Array.from(truncateSessionTitle(long))).toHaveLength(25) // 24 + …
    expect(truncateSessionTitle('短问题')).toBe('短问题')
    expect(truncateSessionTitle('  多   空白  ')).toBe('多 空白')
  })
})

describe('agent store（V2.2 Block C3）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    streamCallbacks = null
    sessionStorage.clear()
  })

  it('按 space_id 分区：space1 的活动不泄漏进 space2（AC-AS2）', async () => {
    const store = useAgentStore()
    await seedSpaceWithRun(store, 1)

    expect(store.partitions.get(2)).toBeUndefined()
    expect(streamCallbacks).not.toBeNull()
    streamCallbacks?.onEvent(makeEvent(0, 'message.assistant_added', { role: 'assistant', text: '回答' }))

    const p1 = store.partitions.get(1)
    expect(p1?.messages.map((m) => m.text)).toEqual(['谁是我的长辈？', '回答'])
    // 新空间分区创建后为空白，不含 space1 的任何投影
    await store.ensureSpace(2)
    const p2 = store.partitions.get(2)
    expect(p2?.messages).toEqual([])
    expect(p2?.toolSummaries).toEqual([])
    expect(p2?.sessionsLoaded).toBe(true)
    expect(mockedFetchSessions).toHaveBeenCalledWith(2)
  })

  it('发送携带 UUID 幂等键；错误时草稿回填且显示结构化文案', async () => {
    const store = useAgentStore()
    mockedFetchSessions.mockResolvedValue([])
    await store.ensureSpace(1)
    mockedCreateMessage.mockRejectedValueOnce(
      new (await import('@/api/errors')).ApiError(409, 'AGENT_RUN_LIMIT', '并发 Run 超限'),
    )
    store.setDraft(1, '你好')
    await store.sendMessage(1)

    expect(mockedCreateMessage).toHaveBeenCalledWith(11, '你好', expect.any(String))
    const key = vi.mocked(mockedCreateMessage).mock.calls[0][2]
    expect(key).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i)
    const p1 = store.partitions.get(1)
    expect(p1?.error?.code).toBe('AGENT_RUN_LIMIT')
    expect(p1?.messages.at(-1)?.status).toBe('failed')
    expect(p1?.draft).toBe('你好')
  })

  it('事件投影：工具摘要 started/completed 与终态收口；sessionStorage 游标清理', async () => {
    const store = useAgentStore()
    await seedSpaceWithRun(store, 1)
    expect(sessionStorage.getItem('fg.agent.run.11')).toBe('100')
    expect(streamOpen).toHaveBeenCalledWith(100)

    streamCallbacks?.onEvent(makeEvent(1, 'run.started'))
    streamCallbacks?.onEvent(
      makeEvent(2, 'tool.execution.started', { tool_call_id: 't1', tool_name: 'fg_list_visible_people', tool_version: 1 }),
    )
    streamCallbacks?.onEvent(
      makeEvent(3, 'tool.execution.completed', { tool_call_id: 't1', tool_name: 'fg_list_visible_people', is_error: true }),
    )
    streamCallbacks?.onEvent(
      makeEvent(4, 'message.assistant_added', {
        role: 'assistant',
        text: '资料不足',
        card_ids: [3, 3, -1],
      }),
    )
    streamCallbacks?.onEvent(makeEvent(5, 'run.settled'))

    const p1 = store.partitions.get(1)
    expect(p1?.toolSummaries).toEqual([
      { toolCallId: 't1', toolName: 'fg_list_visible_people', status: 'error' },
    ])
    expect(p1?.run).toEqual({ id: 100, status: 'succeeded', terminal: true })
    // 终态后恢复游标已删除；已持久化消息保留（取消只取消 Run）
    expect(sessionStorage.getItem('fg.agent.run.11')).toBeNull()
    expect(p1?.messages.find((m) => m.text === '资料不足')?.cardIds).toEqual([3])
  })

  it('刷新恢复：selectSession 重订阅非终态 Run 并对历史去重合流（AC-AS6）', async () => {
    const store = useAgentStore()
    mockedFetchSessions.mockResolvedValue([
      { id: 11, space_id: 1, agent_kind: 'assistant', created_at: '2026-08-26T00:00:00' },
    ])
    await store.ensureSpace(1)
    // 模拟刷新前的现场：游标存在 + 历史里已有用户消息（助手回复未持久化）
    sessionStorage.setItem('fg.agent.run.11', '100')
    mockedFetchMessages.mockResolvedValue([
      { id: 21, role: 'user', content_json: { text: '谁是我的长辈？' }, created_at: '2026-08-26T00:00:01' },
    ])
    mockedFetchRun.mockResolvedValue({
      id: 100,
      session_id: 11,
      kind: 'turn',
      status: 'running',
      attempt: 1,
      max_attempts: 3,
      cancel_requested: false,
      error_code: null,
      created_at: '2026-08-26T00:00:02',
      updated_at: '2026-08-26T00:00:02',
      settled_at: null,
    })

    await store.selectSession(1, 11)

    expect(mockedFetchRun).toHaveBeenCalledWith(100)
    expect(streamOpen).toHaveBeenCalledWith(100)
    // 回放重放 user_added：应消费既有历史而非重复追加
    streamCallbacks?.onEvent(makeEvent(0, 'message.user_added', { role: 'user', text: '谁是我的长辈？' }))
    streamCallbacks?.onEvent(makeEvent(1, 'message.assistant_added', { role: 'assistant', text: '依据路径…' }))
    const messages = store.partitions.get(1)?.messages ?? []
    expect(messages.filter((m) => m.role === 'user' && m.text === '谁是我的长辈？')).toHaveLength(1)
    expect(messages.filter((m) => m.text === '依据路径…')).toHaveLength(1)
    // 会话标题 = 首条用户消息截断 24 字（纯展示）
    expect(store.partitions.get(1)?.titles[11]).toBe('谁是我的长辈？')
  })

  it('resetForSpace：关流、删分区与游标，旧 scope 无残留（跨 scope 对抗）', async () => {
    const store = useAgentStore()
    await seedSpaceWithRun(store, 1)
    mockedCreateSession.mockResolvedValue({
      id: 12,
      space_id: 2,
      agent_kind: 'assistant',
      created_at: '2026-08-26T00:00:00',
    })
    await store.ensureSpace(2)
    await store.newSession(2)

    store.resetForSpace(1)

    expect(store.partitions.has(1)).toBe(false)
    expect(store.partitions.has(2)).toBe(true)
    expect(streamClose).toHaveBeenCalled()
    expect(sessionStorage.getItem('fg.agent.run.11')).toBeNull()
    // space2 分区不受影响
    expect(store.partitions.get(2)?.activeSessionId).toBe(12)
  })

  it('clear：全量清空分区、关流并删除所有 Run 游标（AC-AS7）', async () => {
    const store = useAgentStore()
    await seedSpaceWithRun(store, 1)
    await store.ensureSpace(2)

    store.clear()

    expect(store.partitions.size).toBe(0)
    expect(streamClose).toHaveBeenCalled()
    expect(sessionStorage.getItem('fg.agent.run.11')).toBeNull()
  })

  it('auth.clearSession() 联动清空 agent store（AC-AS7）', async () => {
    const { useAuthStore } = await import('@/stores/auth')
    const auth = useAuthStore()
    const store = useAgentStore()
    await seedSpaceWithRun(store, 1)
    expect(store.partitions.size).toBeGreaterThan(0)

    auth.clearSession()
    await vi.waitFor(() => expect(useAgentStore().partitions.size).toBe(0))
    expect(streamClose).toHaveBeenCalled()
  })
})
