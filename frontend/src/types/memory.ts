/** Browser-facing contracts for reviewable memories and explicit scopes. */

export type PlatformFeatureFlags = {
  memory_enabled: boolean
  rag_enabled: boolean
}

export type MemoryScope = 'private' | `household:${number}` | `lineage:${number}`
export type MemoryScopeKind = 'private' | 'household' | 'lineage'
export type MemorySensitivity = 'normal' | 'sensitive' | 'high' | 'local_required'
export type MemoryCandidateStatus = 'pending' | 'dismissed' | 'confirmed'
export type MemoryStatus = 'active' | 'revoked' | 'deleted'
export type MemoryScopeSelection = MemoryScope
export type MemorySourceStatus = 'available' | 'deleted_snapshot' | 'unavailable' | 'unverified'
export type MemorySourceKind = 'manual' | 'agent_message' | 'rag_chunk' | 'legacy'

export type MemoryCandidateSource =
  | { kind: 'manual' }
  | { kind: 'agent_message'; message_id: number }
  | {
      kind: 'rag_chunk'
      document_id: number
      chunk_id: number
      revision: number
      index_version: string
      /** The space in which the user searched, not the source document's scope. */
      space_id: number
    }

export const MEMORY_SOURCE_STATUS_LABELS: Record<MemorySourceStatus, string> = {
  available: '来源可用',
  deleted_snapshot: '原会话已删除，保留已确认快照',
  unavailable: '来源不可用，内容暂不显示',
  unverified: '来源待验证，内容暂不显示',
}

export function memorySourceReadable(status: MemorySourceStatus): boolean {
  return status === 'available' || status === 'deleted_snapshot'
}

export const MEMORY_CANDIDATE_STATUS_LABELS: Record<MemoryCandidateStatus, string> = {
  pending: '待确认',
  dismissed: '已忽略',
  confirmed: '已确认',
}

export const MEMORY_SCOPE_LABELS: Record<MemoryScopeKind | 'private', string> = {
  private: '仅我可见',
  household: '家庭共享',
  lineage: '族谱共享',
}

export const MEMORY_SENSITIVITY_LABELS: Record<MemorySensitivity, string> = {
  normal: '普通',
  sensitive: '敏感',
  high: '高敏感',
  local_required: '仅本地模型',
}


export interface MemoryCitation {
  source_type: string
  source_id: string
  scope: string
  sensitivity: string
  revision: number
  citation_handle: string
  text?: string
  chunk_id?: number
  document_id?: number
  index_version?: string
}

export interface RagSearchResult extends MemoryCitation {
  chunk_id: number
  document_id: number
  text: string
  index_version: string
  sensitivity: MemorySensitivity
  space_id: number | null
  allowed_scopes: MemoryScope[]
}

/** Shared scopes always carry the selected space id at the request boundary. */
export interface MemoryCandidate {
  id: number
  source_message_id: number | null
  source_document_ref: string | null
  source_span_json: Record<string, unknown>
  source_kind: MemorySourceKind
  source_status: MemorySourceStatus
  allowed_scopes: MemoryScope[]
  raw_quote: string | null
  summary: string | null
  suggested_scope: MemoryScopeKind
  purpose: string | null
  sensitivity: MemorySensitivity
  extractor_version: string
  status: MemoryCandidateStatus
  memory_id: number | null
  created_at: string
  decided_at: string | null
}

export interface Memory {
  id: number
  source_candidate_id: number | null
  source_message_id: number | null
  source_document_ref: string | null
  source_kind: MemorySourceKind
  source_status: MemorySourceStatus
  allowed_scopes: MemoryScope[]
  raw_quote: string | null
  content: string | null
  purpose: string | null
  scope: MemoryScopeKind
  space_id: number | null
  sensitivity: MemorySensitivity
  confirmation_status: 'confirmed'
  revision: number
  retention_until: string | null
  status: MemoryStatus
  revoked_at: string | null
  created_at: string
  updated_at: string
}
