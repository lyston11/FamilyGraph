import { describe, expect, it, vi } from 'vitest'

import { parseSseFrame, useAgentStream } from '@/composables/useAgentStream'
import type { AgentRunEvent } from '@/types/agent'

/**
 * useAgentStream 合同测试（AC-AS6）：
 * mock fetch 流（ReadableStream 构造 SSE 帧），验证解析、Last-Event-ID 续传、
 * 401→refresh→重连一次、终态收口与 dispose。
 */

const encoder = new TextEncoder()

/** 把 wire 事件编码为标准 SSE 帧 */
function frame(event: { seq: number; type: string; payload?: Record<string, unknown> }): string {
  const data = JSON.stringify({
    run_id: 7,
    seq: event.seq,
    type: event.type,
    payload: event.payload ?? {},
    created_at: '2026-08-26T00:00:00',
  })
  return `id: ${event.seq}\nevent: ${event.type}\ndata: ${data}\n\n`
}

/** 立即完整放出的流 */
function instantStream(chunks: string[]): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
}

function okResponse(body: ReadableStream<Uint8Array> | null, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    body,
  } as unknown as Response
}

interface FetchCall {
  url: string
  headers: Record<string, string>
}

function setupFetch(responses: Array<Response | Error>): { calls: FetchCall[] } {
  const calls: FetchCall[] = []
  let index = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string | URL, init?: RequestInit): Promise<Response> => {
      calls.push({ url: String(url), headers: (init?.headers ?? {}) as Record<string, string> })
      const item = responses[index]
      index += 1
      if (item instanceof Error) throw item
      return item
    }),
  )
  return { calls }
}

function makeStream(overrides: Partial<Parameters<typeof useAgentStream>[0]> = {}) {
  const events: AgentRunEvent[] = []
  const ends: string[] = []
  const stream = useAgentStream({
    getAccessToken: () => 'token-a',
    refreshSession: async () => undefined,
    onEvent: (event) => events.push(event),
    onEnded: (info) => ends.push(info.reason),
    baseDelayMs: 1,
    ...overrides,
  })
  return { stream, events, ends }
}

describe('parseSseFrame', () => {
  it('解析 id/event/data 行并忽略注释心跳', () => {
    expect(parseSseFrame(': keepalive')).toBeNull()
    expect(parseSseFrame('')).toBeNull()
    const parsed = parseSseFrame(frame({ seq: 3, type: 'run.settled' }))
    expect(parsed).not.toBeNull()
    expect(parsed?.seq).toBe(3)
    expect(parsed?.type).toBe('run.settled')
    expect(parsed?.run_id).toBe(7)
  })

  it('非法 JSON / 缺字段返回 null 不抛错', () => {
    expect(parseSseFrame('data: not-json')).toBeNull()
    expect(parseSseFrame('data: {"type":"x"}')).toBeNull() // 缺 seq
  })
})

describe('useAgentStream', () => {
  it('按序解析事件并在终态后收口，不再发起请求', async () => {
    const { calls } = setupFetch([
      okResponse(
        instantStream([
          frame({ seq: 0, type: 'run.started' }),
          ': keepalive\n\n',
          frame({ seq: 1, type: 'tool.execution.started', payload: { tool_call_id: 't1', tool_name: 'fg_search_space', tool_version: 1 } }),
          frame({ seq: 2, type: 'message.assistant_added', payload: { role: 'assistant', text: '答案是…' } }),
          frame({ seq: 3, type: 'run.settled' }),
        ]),
      ),
    ])
    const { stream, events, ends } = makeStream()

    await stream.open(7)

    expect(events.map((e) => e.type)).toEqual([
      'run.started',
      'tool.execution.started',
      'message.assistant_added',
      'run.settled',
    ])
    expect(stream.lastSeq.value).toBe(3)
    expect(ends).toEqual([])
    expect(calls).toHaveLength(1)
    expect(calls[0].headers.Authorization).toBe('Bearer token-a')
    expect(stream.status.value).toBe('open')
  })

  it('断线后带 Last-Event-ID 续传且重试次数受限', async () => {
    const { calls } = setupFetch([
      // 第一次：两个事件后服务端异常断开（无终态）
      okResponse(instantStream([frame({ seq: 0, type: 'run.started' }), frame({ seq: 1, type: 'turn.started' })])),
      // 第二次：续传剩余 + 终态
      okResponse(instantStream([frame({ seq: 2, type: 'run.settled' })])),
    ])
    const { stream, events } = makeStream({ maxRetries: 2 })

    await stream.open(7)

    expect(calls).toHaveLength(2)
    expect(calls[1].headers['Last-Event-ID']).toBe('1')
    expect(events.map((e) => e.seq)).toEqual([0, 1, 2])
    expect(events.every((e, i) => e.seq === i)).toBe(true)
  })

  it('401 → 刷新一次 → 用新 token 重连成功', async () => {
    let token = 'expired-token'
    const refreshSession = vi.fn(async () => {
      token = 'fresh-token' // 模拟 auth store 刷新后更新内存 access token
    })
    const { calls } = setupFetch([
      okResponse(null, 401),
      okResponse(instantStream([frame({ seq: 0, type: 'run.settled' })])),
    ])
    const { stream } = makeStream({
      getAccessToken: () => token,
      refreshSession,
    })

    await stream.open(7)

    expect(refreshSession).toHaveBeenCalledTimes(1)
    expect(calls).toHaveLength(2)
    expect(calls[1].headers.Authorization).toBe('Bearer fresh-token')
  })

  it('刷新后仍 401 → 停止并上报 unauthorized', async () => {
    setupFetch([okResponse(null, 401), okResponse(null, 401)])
    const { stream, ends } = makeStream()

    await stream.open(7)

    expect(ends).toEqual(['unauthorized'])
    expect(stream.status.value).toBe('error')
  })

  it('连续网络失败 → 指数退避至 maxRetries 后上报 exhausted', async () => {
    const { calls } = setupFetch([new Error('net down'), new Error('net down'), new Error('net down')])
    const { stream, ends } = makeStream({ maxRetries: 2 })

    await stream.open(7)

    // 首次 + 2 次重试
    expect(calls).toHaveLength(3)
    expect(ends).toEqual(['exhausted'])
    expect(stream.status.value).toBe('error')
  })

  it('close() 中止底层连接且不触发重连/onEnded', async () => {
    let aborted = false
    // 永不主动完成的流；abort 时以 AbortError 拒绝 read（模拟真实 fetch 行为）
    const fetchMock = vi.fn(async (): Promise<Response> => {
      return okResponse(
        new ReadableStream<Uint8Array>({
          start(controller) {
            // 下一次微任务再挂监听，确保先拿到 signal（同一 tick 内已可用，直接取）
            void controller
          },
        }),
      )
    })
    const hangingControllerRef: Array<{ error: (e: unknown) => void; signal: AbortSignal | null }> = []
    fetchMock.mockImplementation(async (_url?: unknown, init?: RequestInit): Promise<Response> => {
      let errored = false
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          hangingControllerRef.push({
            error: (e) => {
              if (!errored) {
                errored = true
                controller.error(e)
              }
            },
            signal: init?.signal ?? null,
          })
        },
      })
      return okResponse(body)
    })
    vi.stubGlobal('fetch', fetchMock)
    const { stream, ends } = makeStream()

    const pending = stream.open(7)
    await vi.waitFor(() => expect(stream.status.value).toBe('open'))
    stream.close()
    // 触发与真实 fetch 一致的 abort 语义（close 可能已同步 abort，需先查状态）
    for (const entry of hangingControllerRef) {
      const fail = (): void => {
        aborted = true
        entry.error(Object.assign(new Error('The operation was aborted'), { name: 'AbortError' }))
      }
      if (entry.signal === null) continue
      if (entry.signal.aborted) fail()
      else entry.signal.addEventListener('abort', fail)
    }
    await pending

    expect(aborted).toBe(true)
    expect(ends).toEqual([])
    expect(stream.status.value).toBe('idle')
  })
})
