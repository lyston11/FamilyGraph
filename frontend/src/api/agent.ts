import { apiClient } from '@/api/client'

import type {
  AgentMessageCreatedResponse,
  AgentMessageOut,
  AgentRun,
  AgentSession,
  RunEventCitations,
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

/** 重命名会话（1..120 字符，端点内 strip）；非本人会话 404。 */
export async function renameAgentSession(sessionId: number, title: string): Promise<AgentSession> {
  const { data } = await apiClient.patch<AgentSession>(`/agent/sessions/${sessionId}`, { title })
  return data
}

/** 删除会话（消息/Run/事件服务端级联清理）；有进行中 Run 时 409 AGENT_RUN_SESSION_BUSY。 */
export async function deleteAgentSession(sessionId: number): Promise<void> {
  await apiClient.delete(`/agent/sessions/${sessionId}`)
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

/**
 * 引用固定后备读取：16 KiB 公开事件装不下完整引用元数据时，按 (run_id, seq)
 * 授权补取。服务端重验当前读者权限；旧消息/无引用返回空集合。
 */
export async function fetchRunEventCitations(
  runId: number,
  seq: number,
): Promise<RunEventCitations> {
  const response = await apiClient.get<RunEventCitations>(
    `/agent/runs/${runId}/events/${seq}/citations`,
  )
  return response.data
}

export async function cancelAgentRun(runId: number): Promise<AgentRun> {
  const { data } = await apiClient.post<AgentRun>(`/agent/runs/${runId}/cancel`)
  return data
}

// ---- 结构化错误码 → 用户文案（spec/backend/error-handling.md：只映射文案，不透传 detail）----

const AGENT_ERROR_COPY: Record<string, string> = {
  AGENT_RUN_LIMIT: '并发任务较多，请稍后再试',
  AGENT_RUNTIME_DISABLED: '助手功能当前未启用',
  // PROVIDER_UNRESOLVED 基线 = 第三态（空间未选模型）；两态细分见 friendlyAgentError
  PROVIDER_UNRESOLVED: '请到 空间管理 → 模型设置 选择模型',
  PROVIDER_LOCAL_REQUIRED_UNAVAILABLE: '该空间要求本地模型执行，但本地服务暂不可用',
  IDEMPOTENCY_PAYLOAD_CONFLICT: '请求校验冲突，请刷新页面后重试',
  AGENT_SESSION_NOT_FOUND: '会话不存在或无权访问',
  AGENT_SESSION_TITLE_INVALID: '会话标题须为 1..120 个字符',
  AGENT_RUN_SESSION_BUSY: '会话有进行中的任务，请先取消或等待完成后再删除',
  AGENT_RUN_NOT_FOUND: '任务不存在或无权访问',
  SPACE_FORBIDDEN_ACTOR: '你已不是该空间的活跃成员，无法继续使用助手',
  SPACE_NOT_FOUND: '空间不存在或无权访问',
  IDEMPOTENCY_KEY_REQUIRED: '请求缺少幂等标识，请刷新页面后重试',
  POLICY_TOOL_BLOCKED: '回答中的某个操作被安全策略拦截，请换个问法',
  POLICY_TOOL_RESULT_BLOCKED: '回答涉及的某些内容被安全策略拦截，请换个问法',
  POLICY_SECRET_LEAK: '检测到不安全的输出内容，已拦截本次回答',
  POLICY_PROVIDER_BLOCKED: '当前模型与空间的安全策略不匹配，请联系空间所有者调整模型设置',
  PROVIDER_DENIED_NO_LOCAL: '该空间要求本地模型执行，但本地服务暂不可用',
  PROVIDER_DENIED_CLOUD_FORBIDDEN: '该空间未开放云端模型，请联系空间所有者调整配置',
  // sidecar 运行期 Provider 出网失败（网络/凭据/上游拒绝；上游错误已脱敏）
  PROVIDER_STREAM_ERROR: '模型服务暂时不可用，请稍后重试',
  // sidecar 运行期：模型完成回合但没有返回任何正文（空最终回答）
  PROVIDER_EMPTY_ANSWER: '模型没有返回内容，请重试或换个问法',
  SIDECAR_ERROR: '助手服务暂时不可用，请稍后重试',
  // 服务端收敛：执行期间空间成员资格被撤销（不是服务故障，重试也不会成功）
  AGENT_MEMBERSHIP_REVOKED: '你已不是该空间的活跃成员，本次回答已停止',
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

/**
 * 结构化错误码 → 用户文案。
 *
 * detail 参数（可选，09-06 治理迁移）：PROVIDER_UNRESOLVED 依据后端 detail
 * 两态细分文案（design §4/§6.4）：
 * - platform_default_configured=false → 平台通道未配置（联系管理员）；
 * - reason=cloud_not_allowed → 通道已有但未同意云执行（去模型设置开启）；
 * - 其余（含无 detail 的 SSE 路径）→ 落基线文案（去模型设置选择模型）。
 */
export function friendlyAgentError(
  code: string | null | undefined,
  fallback?: string,
  detail?: unknown,
): string {
  if (code === 'PROVIDER_UNRESOLVED') {
    const payload =
      typeof detail === 'object' && detail !== null
        ? (detail as { platform_default_configured?: boolean; reason?: string })
        : {}
    if (payload.platform_default_configured === false) {
      return '助手模型尚未由平台管理员配置，请联系平台管理员'
    }
    if (payload.reason === 'cloud_not_allowed') {
      return '该模型需要云端执行同意，请到 空间管理 → 模型设置 开启'
    }
    return AGENT_ERROR_COPY.PROVIDER_UNRESOLVED
  }
  if (code && code in AGENT_ERROR_COPY) return AGENT_ERROR_COPY[code] as string
  if (code && code in CLIENT_ERROR_COPY) return CLIENT_ERROR_COPY[code] as string
  return fallback ?? '操作失败，请稍后重试'
}
