import { apiClient } from '@/api/client'
import type { Memory, MemoryCandidate, MemoryCitation, MemoryScope, MemoryScopeKind, MemorySensitivity } from '@/types/memory'

export interface ConfirmMemoryCandidatePayload {
  scope: MemoryScope
  retention_days?: number
}

/**
 * 手动新建候选（与后端 POST /memory-candidates 的既有合同对齐）：
 * 所有写入（含检索结果「保存」与私有记忆「新增」）都只能先创建候选，
 * 经待确认流程明确 scope 后才成为可检索记忆（V2.5 合同：无绕过审计的直接发布）。
 */
export interface CreateMemoryCandidatePayload {
  raw_quote: string
  summary: string
  purpose: string
  suggested_scope: MemoryScopeKind
  sensitivity: MemorySensitivity
  source_span?: Record<string, unknown>
  source_message_id?: number | null
  source_document_ref?: string | null
}

export async function fetchMemoryCandidates(includeDecided = false): Promise<MemoryCandidate[]> {
  const { data } = await apiClient.get<MemoryCandidate[]>('/memory-candidates', {
    params: includeDecided ? { include_decided: true } : undefined,
  })
  return data
}

export async function createMemoryCandidate(
  payload: CreateMemoryCandidatePayload,
): Promise<MemoryCandidate> {
  const { data } = await apiClient.post<MemoryCandidate>('/memory-candidates', {
    source_span: {},
    ...payload,
  })
  return data
}

/** The server returns private memories only without a space, and private plus shared memories with one. */
export async function fetchMemories(spaceId?: number): Promise<Memory[]> {
  const { data } = await apiClient.get<Memory[]>('/memories', {
    params: spaceId === undefined ? undefined : { space_id: spaceId },
  })
  return data
}

/** Confirmation is the only transition that makes a candidate searchable. */
export async function confirmMemoryCandidate(
  candidateId: number,
  payload: ConfirmMemoryCandidatePayload,
): Promise<Memory> {
  const { data } = await apiClient.post<Memory>(
    `/memory-candidates/${candidateId}/confirm`,
    payload,
  )
  return data
}

export async function dismissMemoryCandidate(candidateId: number): Promise<MemoryCandidate> {
  const { data } = await apiClient.post<MemoryCandidate>(
    `/memory-candidates/${candidateId}/dismiss`,
  )
  return data
}

export async function revokeMemory(memoryId: number): Promise<Memory> {
  const { data } = await apiClient.post<Memory>(`/memories/${memoryId}/revoke`)
  return data
}

export async function deleteMemory(memoryId: number): Promise<void> {
  await apiClient.delete(`/memories/${memoryId}`)
}

export async function searchRag(
  spaceId: number,
  query: string,
  limit = 20,
): Promise<MemoryCitation[]> {
  const { data } = await apiClient.get<MemoryCitation[]>('/rag/search', {
    params: { space_id: spaceId, q: query, limit },
  })
  return data
}

export const searchMemory = searchRag

const MEMORY_ERROR_COPY: Record<string, string> = {
  MEMORY_CANDIDATE_NOT_FOUND: '候选记忆不存在或已被处理',
  MEMORY_SCOPE_FORBIDDEN: '只能选择本人所在的活跃空间',
  MEMORY_SENSITIVE_SCOPE_FORBIDDEN: '高敏感内容不能共享到空间',
  MEMORY_STATE_CONFLICT: '记忆状态已变化，请刷新后重试',
  MEMORY_PRIVACY_BLOCKED: '隐私策略阻止了这个 scope，请选择更严格的范围',
  MEMORY_NOT_FOUND: '记忆不存在或已被删除',
  RAG_SOURCE_NOT_ALLOWED: '该材料未获准进入知识检索',
  RAG_POLICY_DENIED: '当前空间策略不允许检索这条知识',
  PROVIDER_LOCAL_REQUIRED_UNAVAILABLE: '该内容要求本地模型，但本地服务暂不可用',
}

export function friendlyMemoryError(code: string | null | undefined, fallback?: string): string {
  return code && code in MEMORY_ERROR_COPY
    ? MEMORY_ERROR_COPY[code] as string
    : fallback ?? '记忆操作失败，请稍后重试'
}
