import { apiClient } from '@/api/client'

import type {
  AgentMessageCreatedResponse,
  AgentMessageOut,
  AgentRun,
  AgentSession,
} from '@/types/agent'

/**
 * Agent 浏览器 API 封装（V2.2 Block C3）。
 *
 * 复用统一 axios 实例：Authorization 注入与 401 静默刷新由拦截器负责；
 * SSE 流不走此文件（EventSource 无法带 Authorization 头，见 useAgentStream）。
 */

export async function createAgentSession(spaceId: number): Promise<AgentSession> {
  const { data } = await apiClient.post<AgentSession>('/agent/sessions', { space_id: spaceId })
  return data
}

export async function fetchAgentSessions(spaceId?: number): Promise<AgentSession[]> {
  const { data } = await apiClient.get<AgentSession[]>('/agent/sessions', {
    params: spaceId === undefined ? undefined : { space_id: spaceId },
  })
  return data
}

export async function fetchAgentMessages(sessionId: number): Promise<AgentMessageOut[]> {
  const { data } = await apiClient.get<AgentMessageOut[]>(`/agent/sessions/${sessionId}/messages`)
  return data
}

/** 提交用户消息并入队 Assistant Run；idempotencyKey 由调用方生成（UUID）。 */
export async function createAgentMessage(
  sessionId: number,
  content: string,
  idempotencyKey: string,
): Promise<AgentMessageCreatedResponse> {
  const { data } = await apiClient.post<AgentMessageCreatedResponse>(
    `/agent/sessions/${sessionId}/messages`,
    { content },
    { headers: { 'Idempotency-Key': idempotencyKey } },
  )
  return data
}

export async function fetchAgentRun(runId: number): Promise<AgentRun> {
  const { data } = await apiClient.get<AgentRun>(`/agent/runs/${runId}`)
  return data
}

export async function cancelAgentRun(runId: number): Promise<AgentRun> {
  const { data } = await apiClient.post<AgentRun>(`/agent/runs/${runId}/cancel`)
  return data
}

// ---- 结构化错误码 → 用户文案（spec/backend/error-handling.md：只映射文案，不透传 detail）----

const AGENT_ERROR_COPY: Record<string, string> = {
  AGENT_RUN_LIMIT: '并发任务较多，请稍后再试',
  AGENT_RUNTIME_DISABLED: '助手功能当前未启用',
  PROVIDER_UNRESOLVED: '当前空间还没有可用的模型配置，请联系空间所有者在管理页选择 Provider',
  PROVIDER_LOCAL_REQUIRED_UNAVAILABLE: '该空间要求本地模型执行，但本地服务暂不可用',
  IDEMPOTENCY_PAYLOAD_CONFLICT: '请求校验冲突，请刷新页面后重试',
  AGENT_SESSION_NOT_FOUND: '会话不存在或无权访问',
  AGENT_RUN_NOT_FOUND: '任务不存在或无权访问',
  SPACE_FORBIDDEN_ACTOR: '你已不是该空间的活跃成员，无法继续使用助手',
  SPACE_NOT_FOUND: '空间不存在或无权访问',
  IDEMPOTENCY_KEY_REQUIRED: '请求缺少幂等标识，请刷新页面后重试',
  POLICY_TOOL_BLOCKED: '回答中的某个操作被安全策略拦截，请换个问法',
  POLICY_SECRET_LEAK: '检测到不安全的输出内容，已拦截本次回答',
  PROVIDER_DENIED_NO_LOCAL: '该空间要求本地模型执行，但本地服务暂不可用',
  PROVIDER_DENIED_CLOUD_FORBIDDEN: '该空间未开放云端模型，请联系空间所有者调整配置',
  SIDECAR_ERROR: '助手服务暂时不可用，请稍后重试',
}

/** 客户端合成错误码 */
export const CLIENT_AGENT_ERRORS = {
  STREAM_LOST: 'STREAM_LOST',
  AUTH_EXPIRED: 'AUTH_EXPIRED',
  RUN_FAILED: 'RUN_FAILED',
  SEND_FAILED: 'SEND_FAILED',
} as const

const CLIENT_ERROR_COPY: Record<string, string> = {
  STREAM_LOST: '连接中断，任务状态未知，请点击「重试」恢复',
  AUTH_EXPIRED: '登录状态已失效，请重新登录',
  RUN_FAILED: '本次回答执行失败，请重试或换个问法',
  SEND_FAILED: '发送失败，请稍后重试',
}

export function friendlyAgentError(code: string | null | undefined, fallback?: string): string {
  if (code && code in AGENT_ERROR_COPY) return AGENT_ERROR_COPY[code] as string
  if (code && code in CLIENT_ERROR_COPY) return CLIENT_ERROR_COPY[code] as string
  return fallback ?? '操作失败，请稍后重试'
}
