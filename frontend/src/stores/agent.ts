import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import {
  CLIENT_AGENT_ERRORS,
  cancelAgentRun,
  createAgentMessage,
  createAgentSession,
  deleteAgentSession,
  fetchAgentMessages,
  fetchAgentRun,
  fetchRunEventCitations,
  fetchAgentSessions,
  friendlyAgentError,
  renameAgentSession,
} from '@/api/agent'
import { ApiError } from '@/api/errors'
import { useAgentStream } from '@/composables/useAgentStream'
import { useAuthStore } from '@/stores/auth'
import type { AgentEventPayload, AgentMessageOut, AgentSession, WebCitation } from '@/types/agent'
import type { MemoryCitation } from '@/types/memory'
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
  /** Assistant 结构化回复中引用的 ActionCard id；只保留整数 id。 */
  cardIds?: number[]
  /** Assistant 结构化回复中的安全 RAG 引用投影；不信任消息中的任意对象。 */
  citations?: MemoryCitation[]
  /** 因来源失效/失权而未列入 citations 的引用数量（服务端授权投影）。 */
  unavailableCitationCount?: number
  citationLoadState?: 'loading' | 'failed' | 'loaded'
  citationRequest?: { runId: number; seq: number }
  /** Assistant 结构化回复中的受控联网外部引用（trust=external）。 */
  webCitations?: WebCitation[]
  /** 由 SSE 回放合并产生的消息（刷新恢复去重标记） */
  fromReplay?: boolean
  /**
   * 临时正文投影（SSE `assistant.text_delta` 累积）：仅用于实时显示，不是权威结果。
   * 没有 id/citations，不入历史；权威 `message.assistant_added` 到达时被整体替换。
   */
  provisional?: boolean
}

export interface ActiveRunView {
  id: number
  status: string
  terminal: boolean
}

/**
 * 错误横幅结构化动作（09-06 R4）：白名单 kind，UI 按 kind 渲染可行动入口，
 * 绝不把后端 detail 原始 JSON 带进视图层（spec/backend/error-handling.md）。
 */
export interface AgentErrorAction {
  kind: 'open-model-settings'
}

export interface AgentErrorView {
  code: string
  message: string
  action?: AgentErrorAction
}

interface SessionPartition {
  sessions: AgentSession[]
  sessionsLoaded: boolean
  activeSessionId: number | null
  messages: AgentMessageView[]
  toolSummaries: ToolSummaryView[]
  run: ActiveRunView | null
  error: AgentErrorView | null
  sending: boolean
  loadingHistory: boolean
  draft: string
  /** SSE 回放合并的起始扫描下标（见 mergeReplayedMessage） */
  replayCursor: number
  /** 流断开且未恢复（显示重试入口） */
  streamLost: boolean
  /** 无服务端标题会话的兜底标题（乐观首条消息截断，纯展示） */
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

function payloadCardIds(payload: AgentEventPayload): number[] | undefined {
  const refs = payload.card_ids ?? payload.card_refs
  const values = Array.isArray(refs)
    ? refs
    : typeof payload.card_id === 'number' && Number.isInteger(payload.card_id)
      ? [payload.card_id]
      : []
  const ids = values.filter((value): value is number => Number.isInteger(value) && value > 0)
  return ids.length > 0 ? [...new Set(ids)] : undefined
}

function parseCitationArray(raw: unknown): MemoryCitation[] | undefined {
  if (!Array.isArray(raw)) return undefined
  const citations = raw.flatMap((value): MemoryCitation[] => {
    if (typeof value !== 'object' || value === null) return []
    const item = value as Record<string, unknown>
    if (
      typeof item.source_type !== 'string' ||
      typeof item.source_id !== 'string' ||
      typeof item.scope !== 'string' ||
      typeof item.sensitivity !== 'string' ||
      !Number.isInteger(item.revision) ||
      typeof item.citation_handle !== 'string'
    ) {
      return []
    }
    const citation: MemoryCitation = {
      source_type: item.source_type,
      source_id: item.source_id,
      scope: item.scope,
      sensitivity: item.sensitivity,
      revision: Number(item.revision),
      citation_handle: item.citation_handle,
      ...(typeof item.text === 'string' ? { text: item.text } : {}),
      ...(typeof item.index_version === 'string' ? { index_version: item.index_version } : {}),
    }
    if (typeof item.chunk_id === 'number' && Number.isInteger(item.chunk_id)) {
      citation.chunk_id = item.chunk_id
    }
    if (typeof item.document_id === 'number' && Number.isInteger(item.document_id)) {
      citation.document_id = item.document_id
    }
    return [citation]
  })
  return citations.length > 0 ? citations : undefined
}

function payloadCitations(payload: AgentEventPayload): MemoryCitation[] | undefined {
  return parseCitationArray(payload.citations)
}

function payloadWebCitations(payload: AgentEventPayload): WebCitation[] | undefined {
  const raw = payload.web_citations
  if (!Array.isArray(raw)) return undefined
  const citations = raw.flatMap((value): WebCitation[] => {
    if (typeof value !== 'object' || value === null) return []
    const item = value as Record<string, unknown>
    if (
      typeof item.url !== 'string' ||
      typeof item.title !== 'string' ||
      typeof item.excerpt !== 'string' ||
      typeof item.fetched_at !== 'string' ||
      item.trust !== 'external'
    ) {
      return []
    }
    return [
      {
        url: item.url,
        title: item.title,
        excerpt: item.excerpt,
        fetched_at: item.fetched_at,
        trust: 'external',
      },
    ]
  })
  return citations.length > 0 ? citations : undefined
}

function toMessageView(message: AgentMessageOut): AgentMessageView {
  return {
    id: message.id,
    role: message.role === 'assistant' ? 'assistant' : 'user',
    text: payloadText(message.content_json),
    createdAt: message.created_at,
    status: 'sent',
    cardIds: payloadCardIds(message.content_json),
    // 历史读取面：citations 已由服务端按当前读者授权投影（受限来源不回显，
    // 仅计数）。不再从 content_json 读取引用。
    citations: parseCitationArray(message.citations),
    unavailableCitationCount: message.unavailable_citation_count ?? 0,
    webCitations: payloadWebCitations(message.content_json),
  }
}

export function truncateSessionTitle(text: string): string {
  const compact = text.replace(/\s+/g, ' ').trim()
  const chars = Array.from(compact)
  if (chars.length <= SESSION_TITLE_LENGTH) return compact
  return `${chars.slice(0, SESSION_TITLE_LENGTH).join('')}…`
}

/**
 * R4：PROVIDER_UNRESOLVED + detail.reason=cloud_not_allowed → 「去模型设置」
 * 跳转动作（ErrorNotice 仅对当前空间管理员渲染）。其余错误（含无 detail 的
 * SSE run.failed 路径）不产生动作，纯文案。
 */
export function providerUnresolvedAction(
  code: string,
  detail: unknown,
): { action?: AgentErrorAction } {
  if (code !== 'PROVIDER_UNRESOLVED') return {}
  const payload = typeof detail === 'object' && detail !== null ? (detail as { reason?: unknown }) : {}
  return payload.reason === 'cloud_not_allowed' ? { action: { kind: 'open-model-settings' } } : {}
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
  function mergeReplayedMessage(
    partition: SessionPartition,
    role: 'user' | 'assistant',
    text: string,
    cardIds?: number[],
    citations?: MemoryCitation[],
    webCitations?: WebCitation[],
  ): AgentMessageView {
    for (let i = Math.min(partition.replayCursor, partition.messages.length); i < partition.messages.length; i += 1) {
      const existing = partition.messages[i]
      if (existing && !existing.fromReplay && existing.role === role && existing.text === text) {
        existing.fromReplay = true
        if (cardIds !== undefined) existing.cardIds = cardIds
        if (citations !== undefined) existing.citations = citations
        if (webCitations !== undefined) existing.webCitations = webCitations
        partition.replayCursor = i + 1
        return existing
      }
    }
    const view: AgentMessageView = {
      id: null,
      role,
      text,
      createdAt: null,
      status: 'sent',
      cardIds,
      citations,
      webCitations,
      fromReplay: true,
    }
    partition.messages.push(view)
    return partition.messages[partition.messages.length - 1]!
  }

  /**
   * 临时正文累积（SSE `assistant.text_delta`）。
   *
   * 只追加到分区末尾的临时助手气泡；没有就新建一条。临时消息没有 id、没有引用，
   * 不入历史；权威 `message.assistant_added` 到达时按同一 turn 收口。
   * 同一 Run 内 attempt 变化（auto_retry 丢弃上一份尝试）由 text_reset 清空，
   * 不把两次尝试的正文拼在一起。
   */
  function appendProvisionalText(partition: SessionPartition, delta: string): void {
    if (delta.length === 0) return
    const last = partition.messages[partition.messages.length - 1]
    if (last && last.role === 'assistant' && last.provisional === true) {
      last.text += delta
      return
    }
    partition.messages.push({
      id: null,
      role: 'assistant',
      text: delta,
      createdAt: null,
      status: 'sent',
      provisional: true,
    })
  }

  /**
   * 丢弃临时正文（SSE `assistant.text_reset`）：SDK 丢弃了正在重新生成的
   * assistant 消息，已显示的分片不再对应任何消息，必须立即隐藏。
   * 不触碰历史消息——权威结果和既有历史没有 provisional 标记。
   */
  function clearProvisionalText(partition: SessionPartition): void {
    partition.messages = partition.messages.filter((m) => m.provisional !== true)
  }

  async function retryMessageCitations(view: AgentMessageView): Promise<void> {    const request = view.citationRequest
    const stillDisplayed = (): boolean => [...partitions.value.values()].some((p) => p.messages.includes(view))
    if (request === undefined || view.citationLoadState === 'loading' || !stillDisplayed()) return
    view.citationLoadState = 'loading'
    try {
      const result = await fetchRunEventCitations(request.runId, request.seq)
      if (view.citationRequest !== request || !stillDisplayed()) return
      view.citations = parseCitationArray(result.citations)
      view.unavailableCitationCount = result.unavailable_citation_count
      view.citationLoadState = 'loaded'
    } catch {
      if (view.citationRequest === request && stillDisplayed()) view.citationLoadState = 'failed'
    }
  }

  function applyStreamEvent(event: { seq?: number; type: string; payload: AgentEventPayload }): void {
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
      case 'message.assistant_added': {
        // 权威结果到达：同轮临时投影先移除，避免与最终正文重复渲染。
        // 只在合并前清理，否则 mergeReplayedMessage 会去重到临时气泡上。
        clearProvisionalText(partition)
        const view = mergeReplayedMessage(
          partition,
          'assistant',
          payloadText(event.payload),
          payloadCardIds(event.payload),
          payloadCitations(event.payload),
          payloadWebCitations(event.payload),
        )
        const unavailable = event.payload.unavailable_citation_count
        if (typeof unavailable === 'number' && Number.isInteger(unavailable) && unavailable >= 0) {
          view.unavailableCitationCount = unavailable
        }
        // Only the server's complete marker proves all citations fit in 16 KiB.
        // Partial or legacy events use the same authorized run/seq fallback.
        if (event.payload.citations_complete !== true && typeof event.seq === 'number') {
          view.citationRequest = { runId: Number(streamCtx.runId), seq: event.seq }
          view.citationLoadState = undefined
          void retryMessageCitations(view)
        } else if (event.payload.citations_complete === true) {
          view.citationRequest = undefined
          view.citationLoadState = 'loaded'
        }
        break
      }
      case 'assistant.text_delta':
        appendProvisionalText(partition, payloadString(event.payload, 'delta'))
        break
      case 'assistant.text_reset':
        clearProvisionalText(partition)
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
      case 'run.expired':
        finishRun(partition, 'expired')
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
    // 终态到达：临时正文进入终态展示（09-18 design「保留已显示的安全部分并标终态」）。
    // 只去掉 provisional 标记——正文保留（那是用户已经看到的内容，且不是完成答案，
    // 不进历史/引用）；否则 MessageList 会一直渲染「生成中…」，取消后看起来仍在生成。
    // 与 text_reset 的区别保持：reset 是「丢弃」（隐藏），终态是「保留但结束」。
    for (const message of partition.messages) {
      if (message.provisional === true) message.provisional = false
    }
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
    // 标题纯展示：优先服务端落库标题；无则取该会话首条用户消息截断（组件再兜底时间戳）
    if (!partition.titles) partition.titles = {}
    if (partition.titles[sessionId]) return
    const session = partition.sessions.find((s) => s.id === sessionId)
    if (session?.title) {
      partition.titles[sessionId] = session.title
      return
    }
    const firstUser = partition.messages.find((m) => m.role === 'user')
    if (firstUser) partition.titles[sessionId] = truncateSessionTitle(firstUser.text)
  }

  /** 重命名会话：服务端落库后原地更新列表项（保持排序），并同步标题兜底缓存。 */
  async function renameSession(spaceId: number, sessionId: number, title: string): Promise<void> {
    const partition = requirePartition(spaceId)
    if (!partition) return
    try {
      const updated = await renameAgentSession(sessionId, title)
      const index = partition.sessions.findIndex((s) => s.id === sessionId)
      if (index >= 0) partition.sessions[index] = updated
      if (updated.title) partition.titles[sessionId] = updated.title
    } catch (error) {
      partition.error = describeApiError(error)
    }
  }

  /**
   * 删除会话：服务端级联清理消息/Run/事件。
   * 删除当前会话时关流清场，并切换到最近一个剩余会话（无则回到「开始新会话」空状态）；
   * 409（有进行中 Run）等错误经 describeApiError 写入 error 横幅。
   */
  async function deleteSession(spaceId: number, sessionId: number): Promise<void> {
    const partition = requirePartition(spaceId)
    if (!partition) return
    try {
      await deleteAgentSession(sessionId)
    } catch (error) {
      partition.error = describeApiError(error)
      return
    }
    partition.error = null
    const wasActive = partition.activeSessionId === sessionId
    partition.sessions = partition.sessions.filter((s) => s.id !== sessionId)
    forgetActiveRunId(sessionId)
    if (streamCtx !== null && streamCtx.sessionId === sessionId) {
      stream.close()
      streamCtx = null
    }
    if (!wasActive) return
    partition.run = null
    partition.messages = []
    partition.toolSummaries = []
    partition.replayCursor = 0
    partition.streamLost = false
    partition.draft = ''
    const next = partition.sessions[0] ?? null
    partition.activeSessionId = next?.id ?? null
    if (next === null) return
    partition.loadingHistory = true
    try {
      const history = await fetchAgentMessages(next.id)
      partition.messages = history.map(toMessageView)
      rememberSessionTitle(partition, next.id)
    } finally {
      partition.loadingHistory = false
    }
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
      // 首条用户消息乐观写标题（与服务端派生规则一致；组件优先展示服务端 title）
      if (!response.replayed && partition.titles[sessionId] === undefined) {
        partition.titles[sessionId] = truncateSessionTitle(content)
      }
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

  function describeApiError(error: unknown): AgentErrorView {
    if (error instanceof ApiError) {
      // detail 透传 friendlyAgentError：PROVIDER_UNRESOLVED 两态细分文案（09-06）
      return {
        code: error.code,
        message: friendlyAgentError(error.code, error.message, error.detail),
        ...providerUnresolvedAction(error.code, error.detail),
      }
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
    renameSession,
    deleteSession,
    sendMessage,
    cancelRun,
    reattachRun,
    retryMessageCitations,
    setDraft,
    resetForSpace,
    clear,
  }
})
