/**
 * Agent 域类型：与 backend/app/schemas/agent.py 及 agent/src/events.ts
 * 的事件注册表一一对应（人工同步）。
 *
 * 安全边界（V2.2 PRD AS-2/AS-5）：payload 只含白名单投影字段，
 * 前端不得渲染 idempotency_key、token、policy_version 等内部字段。
 */

export interface AgentSession {
  id: number
  space_id: number
  /** assistant | steward（浏览器只会创建 assistant） */
  agent_kind: string
  created_at: string
}

/** 会话历史消息投影（不含系统内部字段） */
export interface AgentMessageOut {
  id: number
  role: string
  content_json: { text?: string } & Record<string, unknown>
  created_at: string
}

export interface AgentRunRef {
  id: number
  status: string
  attempt: number
  cancel_requested: boolean
}

export interface AgentMessageCreatedResponse {
  message: AgentMessageOut
  run: AgentRunRef | null
  replayed: boolean
}

export interface AgentRun {
  id: number
  session_id: number
  kind: string
  status: string
  attempt: number
  max_attempts: number
  cancel_requested: boolean
  error_code: string | null
  created_at: string
  updated_at: string
  settled_at: string | null
}

// ---- SSE 事件（GET /api/agent/runs/{id}/events）----

export const AGENT_EVENT_TYPES = [
  'run.started',
  'message.user_added',
  'turn.started',
  'turn.completed',
  'message.assistant_added',
  'tool.execution.started',
  'tool.execution.completed',
  'run.settled',
  'run.failed',
  'run.cancelled',
] as const

export type AgentEventType = (typeof AGENT_EVENT_TYPES)[number]

/** 终态事件：服务端发送后关闭流 */
export const TERMINAL_STREAM_EVENT_TYPES: readonly string[] = [
  'run.settled',
  'run.failed',
  'run.cancelled',
]

/** Run 终态（models.agent.RUN_TERMINAL_STATUSES） */
export const TERMINAL_RUN_STATUSES: readonly string[] = [
  'succeeded',
  'failed',
  'cancelled',
  'expired',
]

/**
 * 事件载荷：注册表为闭合白名单，但前端按 Record 宽松读取 +
 * 字段级守卫，避免后端 additive 演进时崩溃。
 */
export type AgentEventPayload = Record<string, unknown>

export interface AgentRunEvent {
  run_id: number
  seq: number
  type: string
  payload: AgentEventPayload
  created_at: string
}

/** 受控联网外部引用（backend WebCitationOut / agent WebCitationPayload） */
export interface WebCitation {
  url: string
  title: string
  excerpt: string
  fetched_at: string
  trust: 'external'
}
