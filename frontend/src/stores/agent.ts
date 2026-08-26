import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import {
  CLIENT_AGENT_ERRORS,
  cancelAgentRun,
  createAgentMessage,
  createAgentSession,
  fetchAgentMessages,
  fetchAgentRun,
  fetchAgentSessions,
  friendlyAgentError,
} from '@/api/agent'
import { ApiError } from '@/api/errors'
import { useAgentStream } from '@/composables/useAgentStream'
import { useAuthStore } from '@/stores/auth'
import type { AgentEventPayload, AgentMessageOut, AgentSession } from '@/types/agent'
import { TERMINAL_RUN_STATUSES } from '@/types/agent'

/**
 * Agent 会话状态（V2.2 Block C3）。
 *
 * 安全边界（PRD AS-1/AS-2、design.md 空间切换）：
 * - 一切状态按 space_id 分区（Map<spaceId, SessionPartition>），不同 scope
 *   永不混入同一数组；切换空间 resetForSpace 首版优先清除旧 scope 全部数据；
 * - 登出 / 账号切换 / 撤权经 auth.clearSession() → clear()：关闭流、清空分区
 *   与 sessionStorage 中的 Run 恢复游标；
 * - sessionStorage 仅存「session id → run id」整数恢复游标（AC-AS6 刷新恢复），
 *   不含任何消息内容；登出与空间切换时删除。
 */

export interface ToolSummaryView {
  toolCallId: string
  toolName: string
  status: 'running' | 'ok' | 'error'
}

export interface AgentMessageView {
  /** 服务端 id；乐观插入的本地消息为 null */
  id: number | null
  role: 'user' | 'assistant'
  text: string
  createdAt: string | null
  status: 'sent' | 'pending' | 'failed'
  /** 由 SSE 回放合并产生的消息（刷新恢复去重标记） */
  fromReplay?: boolean
}

export interface ActiveRunView {
  id: number
  status: string
  terminal: boolean
}

interface SessionPartition {
  sessions: AgentSession[]
  sessionsLoaded: boolean
  activeSessionId: number | null
  messages: AgentMessageView[]
  toolSummaries: ToolSummaryView[]
  run: ActiveRunView | null
  error: { code: string; message: string } | null
  sending: boolean
  loadingHistory: boolean
  draft: string
  /** SSE 回放合并的起始扫描下标（见 mergeReplayedMessage） */
  replayCursor: number
  /** 流断开且未恢复（显示重试入口） */
  streamLost: boolean
  /** 已加载过历史的会话标题（首条用户消息截断，纯展示） */
  titles: Record<number, string>
}

const ACTIVE_RUN_STATUSES = new Set(['queued', 'leased', 'running'])
const RUN_KEY_PREFIX = 'fg.agent.run.'
/** 会话标题截断宽度（PRD：首条用户消息截断 24 字，纯展示） */
const SESSION_TITLE_LENGTH = 24

function emptyPartition(): SessionPartition {
  return {
    sessions: [],
    sessionsLoaded: false,
    activeSessionId: null,
    messages: [],
    toolSummaries: [],
    run: null,
    error: null,
    sending: false,
    loadingHistory: false,
    draft: '',
    replayCursor: 0,
    streamLost: false,
    titles: {},
  }
}

function runKey(sessionId: number): string {
  return `${RUN_KEY_PREFIX}${sessionId}`
}

function saveActiveRunId(sessionId: number, runId: number): void {
  try {
    sessionStorage.setItem(runKey(sessionId), String(runId))
  } catch {
    /* 隐私模式等场景写入失败可容忍：只影响刷新恢复 */
  }
}

function forgetActiveRunId(sessionId: number): void {
  try {
    sessionStorage.removeItem(runKey(sessionId))
  } catch {
    /* 同上 */
  }
}

function forgetAllRunIds(): void {
  try {
    for (let i = sessionStorage.length - 1; i >= 0; i -= 1) {
      const key = sessionStorage.key(i)
      if (key !== null && key.startsWith(RUN_KEY_PREFIX)) sessionStorage.removeItem(key)
    }
  } catch {
    /* 同上 */
  }
}

function payloadText(payload: AgentEventPayload): string {
  return typeof payload.text === 'string' ? payload.text : ''
}

function payloadString(payload: AgentEventPayload, key: string, fallback = ''): string {
  const value = payload[key]
  return typeof value === 'string' ? value : fallback
}

function toMessageView(message: AgentMessageOut): AgentMessageView {
  return {
    id: message.id,
    role: message.role === 'assistant' ? 'assistant' : 'user',
    text: payloadText(message.content_json),
    createdAt: message.created_at,
    status: 'sent',
  }
}

export function truncateSessionTitle(text: string): string {
  const compact = text.replace(/\s+/g, ' ').trim()
  const chars = Array.from(compact)
  if (chars.length <= SESSION_TITLE_LENGTH) return compact
  return `${chars.slice(0, SESSION_TITLE_LENGTH).join('')}…`
}

export const useAgentStore = defineStore('agent', () => {
  // ---- 状态：按 space_id 分区 ----
  const partitions = ref<Map<number, SessionPartition>>(new Map())

  // ---- SSE 流（store 内单实例；同一时刻只订阅一个 Run）----

  let streamCtx: { spaceId: number; sessionId: number; runId: number } | null = null

  const stream = useAgentStream({
    getAccessToken: () => useAuthStore().accessToken,
    refreshSession: () => useAuthStore().refreshSession(),
    onEvent: (event) => applyStreamEvent(event),
    onEnded: (info) => handleStreamEnded(info),
  })

  function requirePartition(spaceId: number): SessionPartition | null {
    return partitions.value.get(spaceId) ?? null
  }

  // ---- 事件投影 ----

  /**
   * 回放/实时消息合流：优先消费既有历史中未标记的匹配项（role+text 相同），
   * 找不到才追加——避免刷新恢复时「历史接口 + 事件回放」双份渲染。
   */
  function mergeReplayedMessage(partition: SessionPartition, role: 'user' | 'assistant', text: string): void {
    for (let i = Math.min(partition.replayCursor, partition.messages.length); i < partition.messages.length; i += 1) {
      const existing = partition.messages[i]
      if (existing && !existing.fromReplay && existing.role === role && existing.text === text) {
        existing.fromReplay = true
        partition.replayCursor = i + 1
        return
      }
    }
    partition.messages.push({
      id: null,
      role,
      text,
      createdAt: null,
      status: 'sent',
      fromReplay: true,
    })
  }

  function applyStreamEvent(event: { type: string; payload: AgentEventPayload }): void {
    if (streamCtx === null) return
    const partition = partitions.value.get(streamCtx.spaceId)
    if (!partition || partition.run === null || partition.run.id !== streamCtx.runId) return

    switch (event.type) {
      case 'run.started':
      case 'turn.started':
        break
      case 'message.user_added':
        mergeReplayedMessage(partition, 'user', payloadText(event.payload))
        break
      case 'tool.execution.started': {
        const toolCallId = payloadString(event.payload, 'tool_call_id')
        if (toolCallId && !partition.toolSummaries.some((t) => t.toolCallId === toolCallId)) {
          partition.toolSummaries.push({
            toolCallId,
            toolName: payloadString(event.payload, 'tool_name'),
            status: 'running',
          })
        }
        break
      }
      case 'tool.execution.completed': {
        const toolCallId = payloadString(event.payload, 'tool_call_id')
        const summary = partition.toolSummaries.find((t) => t.toolCallId === toolCallId)
        if (summary) summary.status = event.payload.is_error === true ? 'error' : 'ok'
        else {
          // 断线续传时可能只收到 completed：补一条已完结摘要
          partition.toolSummaries.push({
            toolCallId,
            toolName: payloadString(event.payload, 'tool_name'),
            status: event.payload.is_error === true ? 'error' : 'ok',
          })
        }
        break
      }
      case 'message.assistant_added':
        mergeReplayedMessage(partition, 'assistant', payloadText(event.payload))
        break
      case 'turn.completed':
        break
      case 'run.settled':
        finishRun(partition, 'succeeded')
        break
      case 'run.failed':
        finishRun(partition, 'failed', payloadString(event.payload, 'error_code') || CLIENT_AGENT_ERRORS.RUN_FAILED)
        break
      case 'run.cancelled':
        finishRun(partition, 'cancelled')
        break
      default:
        // 未知类型（未来版本 additive）：忽略，不崩溃
        break
    }
  }

  function finishRun(
    partition: SessionPartition,
    status: string,
    errorCode?: string,
  ): void {
    if (partition.run === null || partition.run.terminal) return
    partition.run = { ...partition.run, status, terminal: true }
    partition.streamLost = false
    if (partition.activeSessionId !== null) forgetActiveRunId(partition.activeSessionId)
    if (status === 'failed') {
      partition.error = {
        code: errorCode ?? CLIENT_AGENT_ERRORS.RUN_FAILED,
        message: friendlyAgentError(errorCode),
      }
    } else if (status === 'cancelled') {
      partition.error = null
    }
  }

  function handleStreamEnded(info: { reason: string }): void {
    if (streamCtx === null) return
    const partition = partitions.value.get(streamCtx.spaceId)
    if (!partition) return
    if (info.reason === 'exhausted' || info.reason === 'unauthorized') {
      partition.streamLost = true
      partition.error = {
        code: info.reason === 'unauthorized' ? CLIENT_AGENT_ERRORS.AUTH_EXPIRED : CLIENT_AGENT_ERRORS.STREAM_LOST,
        message: '',
      }
    }
    // terminal：事件已在 applyStreamEvent 收口；aborted：主动关闭无需处理
  }

  // ---- Run 生命周期 ----

  function startRun(spaceId: number, sessionId: number, runId: number, status: string): void {
    const partition = requirePartition(spaceId)
    if (!partition) return
    streamCtx = { spaceId, sessionId, runId }
    partition.streamLost = false
    partition.error = null
    partition.replayCursor = partition.messages.length - 1
    partition.run = { id: runId, status, terminal: false }
    saveActiveRunId(sessionId, runId)
    void stream.open(runId)
  }

  /** 断线/错误后手动重连当前 Run（UI「重试」按钮） */
  async function reattachRun(spaceId: number): Promise<void> {
    const partition = requirePartition(spaceId)
    if (!partition || partition.run === null || partition.run.terminal) return
    const runId = partition.run.id
    try {
      const run = await fetchAgentRun(runId)
      if (TERMINAL_RUN_STATUSES.includes(run.status)) {
        finishRun(partition, run.status, run.error_code ?? undefined)
      } else {
        partition.streamLost = false
        partition.error = null
        streamCtx = { spaceId, sessionId: partition.activeSessionId ?? 0, runId }
        void stream.open(runId)
      }
    } catch {
      partition.error = { code: CLIENT_AGENT_ERRORS.STREAM_LOST, message: '' }
    }
  }

  async function cancelRun(spaceId: number): Promise<void> {
    const partition = requirePartition(spaceId)
    if (!partition || partition.run === null || partition.run.terminal) return
    try {
      await cancelAgentRun(partition.run.id)
      // 终态以 run.cancelled 事件收口；queued 状态服务端直接 settle
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        // 已是终态：拉一次状态对齐本地投影
        await reattachRun(spaceId)
        return
      }
      throw error
    }
  }

  // ---- 会话 ----

  async function ensureSpace(spaceId: number): Promise<void> {
    if (!partitions.value.has(spaceId)) {
      partitions.value.set(spaceId, emptyPartition())
    }
    const partition = requirePartition(spaceId)
    if (partition && !partition.sessionsLoaded) {
      partition.sessions = await fetchAgentSessions(spaceId)
      partition.sessionsLoaded = true
    }
  }

  async function newSession(spaceId: number): Promise<AgentSession> {
    const partition = requirePartition(spaceId)
    if (!partition) throw new Error('SPACE_PARTITION_MISSING')
    // 创建前关闭旧会话的进行中流（同一空间内切换）
    if (streamCtx !== null && streamCtx.spaceId === spaceId) stream.close()
    const session = await createAgentSession(spaceId)
    partition.sessions.unshift(session)
    partition.activeSessionId = session.id
    partition.messages = []
    partition.toolSummaries = []
    partition.run = null
    partition.error = null
    partition.draft = ''
    partition.replayCursor = 0
    partition.streamLost = false
    return session
  }

  async function selectSession(spaceId: number, sessionId: number): Promise<void> {
    const partition = requirePartition(spaceId)
    if (!partition || partition.activeSessionId === sessionId) return
    if (streamCtx !== null && streamCtx.sessionId !== sessionId) stream.close()
    partition.activeSessionId = sessionId
    partition.messages = []
    partition.toolSummaries = []
    partition.run = null
    partition.error = null
    partition.replayCursor = 0
    partition.streamLost = false
    partition.loadingHistory = true
    try {
      const history = await fetchAgentMessages(sessionId)
      partition.messages = history.map(toMessageView)
      rememberSessionTitle(partition, sessionId)
      // AC-AS6 刷新恢复：sessionStorage 有未终态 Run 则重新订阅（全量回放 + 去重合流）
      let savedRunId: number | null = null
      try {
        const raw = sessionStorage.getItem(runKey(sessionId))
        savedRunId = raw === null ? null : Number(raw)
      } catch {
        savedRunId = null
      }
      if (savedRunId !== null && Number.isInteger(savedRunId)) {
        try {
          const run = await fetchAgentRun(savedRunId)
          if (TERMINAL_RUN_STATUSES.includes(run.status)) {
            forgetActiveRunId(sessionId)
          } else {
            partition.run = { id: run.id, status: run.status, terminal: false }
            streamCtx = { spaceId, sessionId, runId: run.id }
            void stream.open(run.id)
          }
        } catch {
          forgetActiveRunId(sessionId)
        }
      }
    } finally {
      partition.loadingHistory = false
    }
  }

  function rememberSessionTitle(partition: SessionPartition, sessionId: number): void {
    // 标题纯展示：取该会话首条用户消息截断（无则回退时间戳格式，由组件兜底）
    if (!partition.titles) partition.titles = {}
    if (partition.titles[sessionId]) return
    const firstUser = partition.messages.find((m) => m.role === 'user')
    if (firstUser) partition.titles[sessionId] = truncateSessionTitle(firstUser.text)
  }

  // ---- 发送 ----

  async function sendMessage(spaceId: number, rawContent?: string): Promise<void> {
    const partition = requirePartition(spaceId)
    if (!partition || partition.sending) return
    const content = (rawContent ?? partition.draft).trim()
    if (!content) return

    partition.error = null
    let sessionId = partition.activeSessionId
    if (sessionId === null) {
      try {
        const session = await newSession(spaceId)
        sessionId = session.id
      } catch (error) {
        partition.error = describeApiError(error)
        return
      }
    }

    partition.sending = true
    const optimistic: AgentMessageView = {
      id: null,
      role: 'user',
      text: content,
      createdAt: null,
      status: 'pending',
    }
    partition.messages.push(optimistic)
    if (partition.draft === content) partition.draft = ''

    try {
      const response = await createAgentMessage(sessionId, content, crypto.randomUUID())
      optimistic.id = response.message.id
      optimistic.createdAt = response.message.created_at
      optimistic.status = 'sent'
      if (
        response.run !== null &&
        ACTIVE_RUN_STATUSES.has(response.run.status) &&
        partition.activeSessionId === sessionId
      ) {
        startRun(spaceId, sessionId, response.run.id, response.run.status)
      }
    } catch (error) {
      optimistic.status = 'failed'
      partition.draft = content // 还给用户草稿，避免丢失输入
      partition.error = describeApiError(error)
    } finally {
      partition.sending = false
    }
  }

  function describeApiError(error: unknown): { code: string; message: string } {
    if (error instanceof ApiError) {
      return { code: error.code, message: friendlyAgentError(error.code, error.message) }
    }
    return { code: CLIENT_AGENT_ERRORS.SEND_FAILED, message: '' }
  }

  // ---- 草稿 / 清理 ----

  function setDraft(spaceId: number, draft: string): void {
    const partition = requirePartition(spaceId)
    if (partition) partition.draft = draft
  }

  /** 空间切换：首版优先清除——关流、删分区与会话级 Run 游标，不留缓存 */
  function resetForSpace(spaceId: number): void {
    const partition = partitions.value.get(spaceId)
    if (!partition) return
    if (streamCtx !== null && streamCtx.spaceId === spaceId) {
      stream.close()
      streamCtx = null
    }
    for (const session of partition.sessions) forgetActiveRunId(session.id)
    partitions.value.delete(spaceId)
  }

  /** 登出 / 账号切换 / 撤权（auth.clearSession 调用）：全量清理 */
  function clear(): void {
    stream.close()
    streamCtx = null
    partitions.value.clear()
    forgetAllRunIds()
  }

  const streamStatus = computed(() => stream.status.value)

  return {
    partitions,
    streamStatus,
    ensureSpace,
    newSession,
    selectSession,
    sendMessage,
    cancelRun,
    reattachRun,
    setDraft,
    resetForSpace,
    clear,
  }
})
