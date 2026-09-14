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
  /** 服务端展示标题（首条用户消息派生或重命名）；null = 尚无用户消息且未重命名 */
  title: string | null
  /** 最近一次用户消息时间；无消息等于 created_at */
  updated_at: string
}

/** 会话历史消息投影（不含系统内部字段） */
export interface AgentMessageOut {
  id: number
  role: string
  content_json: { text?: string } & Record<string, unknown>
  created_at: string
  /** 服务端按当前读者授权投影后的引用（不回显受限来源标识）。 */
  citations?: unknown[]
  /** 因来源失效/失权而未列入 citations 的引用数量（可选，缺省 0）。 */
  unavailable_citation_count?: number
}

/** GET /api/agent/runs/{run_id}/events/{seq}/citations 固定后备读取。 */
export interface RunEventCitations {
  run_id: number
  seq: number
  citations: unknown[]
  unavailable_citation_count: number
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
  'run.expired',
] as const

export type AgentEventType = (typeof AGENT_EVENT_TYPES)[number]

/** 终态事件：服务端发送后关闭流 */
export const TERMINAL_STREAM_EVENT_TYPES: readonly string[] = [
  'run.settled',
  'run.failed',
  'run.cancelled',
  'run.expired',
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

// ---- 空间模型设置（backend/app/api/space_model_settings.py，09-06 治理迁移）----

/** Provider 设置的 Agent 维度（assistant/steward 各自独立选择与平台默认） */
export type AgentConfigKind = 'assistant' | 'steward'

/** 单 agent 维度的空间行级设置；enabled=false 且 provider/model 空 = 显式停用 */
export interface SpaceAgentSetting {
  agent_kind: AgentConfigKind
  provider_id: number | null
  model: string | null
  cloud_allowed: boolean
  local_required: boolean
  enabled: boolean
  /** 模型辅助层空间级开关（仅 steward 维度消费；assistant 行恒 false） */
  assist_candidate: boolean
  assist_ranking: boolean
  assist_explanation: boolean
  assist_terminology: boolean
  /** 09-13 治理：辅助开关生效值（平台配置 ∧ 空间级）；空间开而平台关 → 提示依据 */
  assist_candidate_effective: boolean
  assist_ranking_effective: boolean
  assist_explanation_effective: boolean
  assist_terminology_effective: boolean
  /** 09-13 推测层空间级开关（仅 steward 维度；assistant 行恒 false） */
  inferred_tree: boolean
  /** 推测层生效 = 平台 AND 空间；空间开而平台关 → 前端显示可解释提示 */
  inferred_effective: boolean
}

/** 管理员允许目录条目（仅 enabled Provider；无任何密钥形态字段） */
export interface AgentModelCatalogEntry {
  provider_id: number
  name: string
  kind: 'openai_compatible' | 'local'
  api: string
  models: string[]
}

/** 单 agent 维度的平台默认（provider/model 成对；null = 该维度未设默认） */
export interface AgentPlatformDefaultKind {
  provider_id: number
  model: string
}

/** 平台默认状态（backend AgentPlatformDefaultsOut） */
export interface AgentPlatformDefaultsState {
  assistant: AgentPlatformDefaultKind | null
  steward: AgentPlatformDefaultKind | null
  updated_at: string | null
}

/** GET /spaces/{space_id}/model-settings 响应（owner 侧模型设置视图） */
export interface SpaceModelSettings {
  space_id: number
  settings: { assistant: SpaceAgentSetting | null; steward: SpaceAgentSetting | null }
  catalog: AgentModelCatalogEntry[]
  platform_default: AgentPlatformDefaultsState
}
