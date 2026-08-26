/**
 * ActionCard 域类型（V2.4 Block S3）：与后端 /api/action-cards 合同对齐（人工同步）。
 *
 * 安全边界（PRD ST-4）：卡片 payload 不存 masked 原值；evidence 只含 fact_ids
 * 与 path_summary 路径摘要，前端只做展示，不得尝试还原原始 SourceFact 内容。
 */

export type ActionCardKind = 'household_link' | 'lineage_request'

export type ProposedActionType = 'create_household' | 'request_lineage'

/**
 * 卡片状态机（PRD ST-4）：pending/viewed/accepted 为活跃态，
 * executed/dismissed/expired/superseded 为终态——终态不可复活。
 */
export type ActionCardState =
  | 'pending'
  | 'viewed'
  | 'accepted'
  | 'executed'
  | 'dismissed'
  | 'expired'
  | 'superseded'

/** 终态集合（models：CARD_TERMINAL_STATUSES，人工同步） */
export const TERMINAL_ACTION_CARD_STATES: readonly ActionCardState[] = [
  'executed',
  'dismissed',
  'expired',
  'superseded',
]

export function isTerminalCardState(state: ActionCardState): boolean {
  return TERMINAL_ACTION_CARD_STATES.includes(state)
}

export interface ActionCardUserRef {
  id: number
  name: string
}

/** 证据投影：只含事实 id 列表、路径摘要与证据版本，不含原值 */
export interface ActionCardEvidence {
  fact_ids: number[]
  path_summary: string | null
  evidence_version: number
}

export interface ProposedAction {
  type: ProposedActionType
  params: Record<string, unknown>
}

/** GET /api/action-cards 条目（CardOut） */
export interface ActionCard {
  id: number
  kind: ActionCardKind
  space_id: number
  subject_user: ActionCardUserRef
  object_user: ActionCardUserRef | null
  reason_text: string
  evidence: ActionCardEvidence
  proposed_action: ProposedAction
  privacy_effect: string
  state: ActionCardState
  expires_at: string | null
  created_at: string
  revision: number
}

/** POST view/dismiss/accept 响应（compare-and-set revision） */
export interface ActionCardTransitionResponse {
  id: number
  state: ActionCardState
  revision: number
}

/** POST execute 成功响应 */
export interface ActionCardExecuteResponse {
  id: number
  state: ActionCardState
}
