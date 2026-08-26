/** Browser-facing contracts for reviewable memories and explicit scopes. */

export type MemoryScope = 'private' | `household:${number}` | `lineage:${number}`
export type MemoryScopeKind = 'private' | 'household' | 'lineage'
export type MemorySensitivity = 'normal' | 'sensitive' | 'high' | 'local_required'
export type MemoryCandidateStatus = 'pending' | 'dismissed' | 'confirmed'
export type MemoryStatus = 'active' | 'revoked' | 'deleted'
export type MemoryScopeSelection = MemoryScope

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

export type RagSearchResult = Required<Pick<MemoryCitation, 'chunk_id' | 'document_id' | 'source_type' | 'source_id' | 'text' | 'scope' | 'sensitivity' | 'revision' | 'index_version' | 'citation_handle'>>

/** Shared scopes always carry the selected space id at the request boundary. */
export interface MemoryCandidate {
  id: number
  source_message_id: number | null
  source_document_ref: string | null
  source_span_json: Record<string, unknown>
  raw_quote: string
  summary: string
  suggested_scope: MemoryScopeKind
  purpose: string
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
  raw_quote: string
  content: string
  purpose: string
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
