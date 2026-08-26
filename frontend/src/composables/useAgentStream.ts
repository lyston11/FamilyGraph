import { readonly, ref } from 'vue'

import type { AgentRunEvent } from '@/types/agent'
import { TERMINAL_STREAM_EVENT_TYPES } from '@/types/agent'

/**
 * Agent Run SSE 流：基于 fetch 的手动解析实现（V2.2 Block C3）。
 *
 * 为什么不用 EventSource：浏览器 EventSource 无法携带 Authorization 头，
 * 而事件端点要求 JWT 认证；因此用 fetch + ReadableStream 手工解析 SSE 帧
 * （notes.md：前端 SSE 不会自动复用 Axios interceptor，需要专门的
 * token / 401 / reconnect 设计）。
 *
 * 行为合同（task design / AC-AS6）：
 * - 断线自动重连：指数退避（baseDelayMs × 2^n），至多 maxRetries 次；
 *   连接成功后失败计数清零；
 * - 续传：记录最后收到事件的 seq，重连请求带 Last-Event-ID 头
 *   （服务端同时支持 ?after_event_id=，头优先级相同取较大者）；
 * - 401：先尝试一次 refresh 再重连（每个 open 周期至多一次），
 *   仍 401 则停止并上报 unauthorized；
 * - 终态事件（run.settled/failed/cancelled）：服务端发送后关闭流，
 *   客户端收到后不再重连，正常收口；
 * - close()/组件卸载：AbortController 立即中止且不触发重连。
 */

export type AgentStreamStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'error'

export interface AgentStreamEndInfo {
  reason:
    | 'terminal' // 收到终态事件后正常结束
    | 'aborted' // 调用方主动 close()
    | 'exhausted' // 重试次数用尽仍未恢复
    | 'unauthorized' // 刷新后仍 401
}

export interface AgentStreamOptions {
  getAccessToken: () => string | null
  refreshSession: () => Promise<unknown>
  onEvent: (event: AgentRunEvent) => void
  onEnded?: (info: AgentStreamEndInfo) => void
  /** 断线重连上限（不含首次连接）；默认 3 */
  maxRetries?: number
  /** 首次退避基数毫秒；默认 500，指数 ×2 */
  baseDelayMs?: number
}

const TERMINAL_EVENTS = new Set<string>(TERMINAL_STREAM_EVENT_TYPES)

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function isAbortError(error: unknown): boolean {
  return typeof error === 'object' && error !== null && (error as { name?: string }).name === 'AbortError'
}

/** 解析单个 SSE 帧（id:/event:/data: 行与 :keepalive 注释行） */
export function parseSseFrame(frame: string): AgentRunEvent | null {
  const dataLines: string[] = []
  for (const line of frame.split('\n')) {
    if (line === '' || line.startsWith(':')) continue // 注释/心跳
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'data') dataLines.push(value)
    // id/event 字段：seq 以 data JSON 为准（服务端两者同值），type 同理
  }
  if (dataLines.length === 0) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(dataLines.join('\n'))
  } catch {
    return null
  }
  if (typeof parsed !== 'object' || parsed === null) return null
  const record = parsed as Record<string, unknown>
  if (typeof record.seq !== 'number' || typeof record.type !== 'string') return null
  return {
    run_id: typeof record.run_id === 'number' ? record.run_id : 0,
    seq: record.seq,
    type: record.type,
    payload: (record.payload ?? {}) as Record<string, unknown>,
    created_at: typeof record.created_at === 'string' ? record.created_at : '',
  }
}

export function useAgentStream(options: AgentStreamOptions) {
  const maxRetries = options.maxRetries ?? 3
  const baseDelayMs = options.baseDelayMs ?? 500

  const status = ref<AgentStreamStatus>('idle')
  /** 最后收到的 seq：续传游标 */
  const lastSeq = ref(0)

  let controller: AbortController | null = null
  let currentRunId: number | null = null
  /** open/close 都会使旧的连接循环失效 */
  let generation = 0
  let refreshAttempted = false
  let failures = 0

  async function open(runId: number): Promise<void> {
    close()
    generation += 1
    currentRunId = runId
    refreshAttempted = false
    failures = 0
    lastSeq.value = 0
    await connectLoop(generation)
  }

  function close(): void {
    generation += 1
    currentRunId = null
    controller?.abort()
    controller = null
    status.value = 'idle'
  }

  async function connectLoop(gen: number): Promise<void> {
    for (;;) {
      if (gen !== generation || currentRunId === null) return
      const outcome = await connectOnce(gen)
      if (gen !== generation || outcome.done) return
      failures += 1
      if (failures > maxRetries) {
        status.value = 'error'
        options.onEnded?.({ reason: outcome.unauthorized ? 'unauthorized' : 'exhausted' })
        return
      }
      status.value = 'reconnecting'
      await sleep(baseDelayMs * 2 ** (failures - 1))
    }
  }

  /**
   * 尝试一次连接。
   * 返回 done=true 表示不再继续（终态/被关闭/致命错误）；
   * done=false 表示可重试（网络中断、非 401 HTTP 错误、刷新后的 401 重试）。
   */
  async function connectOnce(gen: number): Promise<{ done: boolean; unauthorized?: boolean }> {
    if (currentRunId === null) return { done: true }
    controller = new AbortController()
    const headers: Record<string, string> = { Accept: 'text/event-stream' }
    const token = options.getAccessToken()
    if (token) headers.Authorization = `Bearer ${token}`
    if (lastSeq.value > 0) headers['Last-Event-ID'] = String(lastSeq.value)

    status.value = failures === 0 ? 'connecting' : 'reconnecting'

    let response: Response
    try {
      response = await fetch(`/api/agent/runs/${currentRunId}/events`, {
        headers,
        signal: controller.signal,
      })
    } catch (error) {
      if (isAbortError(error)) return { done: true } // close() 触发
      return { done: false } // 网络异常 → 退避重试
    }
    if (gen !== generation) return { done: true }

    if (response.status === 401) {
      if (!refreshAttempted) {
        // 先刷新再重连一次；刷新本身失败也走一次重连（拿到新 401 后终止）
        refreshAttempted = true
        try {
          await options.refreshSession()
        } catch {
          /* 刷新失败：由下一次请求的 401 分支收口为 unauthorized */
        }
        return { done: false }
      }
      status.value = 'error'
      options.onEnded?.({ reason: 'unauthorized' })
      return { done: true }
    }

    if (!response.ok || response.body === null) return { done: false }

    status.value = 'open'
    failures = 0
    try {
      const reachedTerminal = await consume(response.body, gen)
      return { done: reachedTerminal }
    } catch (error) {
      if (isAbortError(error)) return { done: true }
      return { done: false } // 流中途异常 → 退避重试（带 Last-Event-ID 续传）
    }
  }

  /** 消费响应体；返回是否因终态事件而完成 */
  async function consume(body: ReadableStream<Uint8Array>, gen: number): Promise<boolean> {
    const reader = body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    try {
      for (;;) {
        const { value, done } = await reader.read()
        if (gen !== generation) return true // 已被关闭/替换
        if (done) break // 服务端提前断开（未见终态）→ 可重试
        buffer += decoder.decode(value, { stream: true })
        let separator = buffer.indexOf('\n\n')
        while (separator !== -1) {
          const frame = buffer.slice(0, separator)
          buffer = buffer.slice(separator + 2)
          const event = parseSseFrame(frame)
          if (event) {
            lastSeq.value = event.seq
            options.onEvent(event)
            if (TERMINAL_EVENTS.has(event.type)) return true
          }
          separator = buffer.indexOf('\n\n')
        }
      }
      return false
    } finally {
      reader.releaseLock()
    }
  }

  return {
    status: readonly(status),
    lastSeq: readonly(lastSeq),
    open,
    close,
  }
}
