import type {
  KinshipPresentation,
  SuggestionAction,
  SuggestionDismissResult,
  SuggestionItem,
  SuggestionKind,
  SuggestionOrigin,
  SuggestionState,
  SuggestionsPage,
  SuggestionSubmitPreferenceResult,
  SuggestionSubmitProposalResult,
} from '@/types/api'

import { isNullableString, isOneOf, isRecord } from './decode'
import { apiClient } from './client'

/**
 * Steward 建议审核客户端合同（09-11 candidate-review）：
 * - `GET /steward-suggestions?space_id=&cursor=&limit=`：分页 items/next_cursor，
 *   只含安全显示字段（后端绝不回传 raw model payload）；
 * - `POST /steward-suggestions/{id}/dismiss`：expected_revision CAS，幂等，
 *   按收件人独立 + 同证据版本冷却；
 * - `POST /steward-suggestions/{id}/submit`：expected_revision + evidence_hash +
 *   confirm=true + Idempotency-Key；relation_proposal 返回 202（提案待确认），
 *   term_preference 返回 200（已 resolved）；
 * - 已读/查看（open_details）与提交（submit）严格分离：打开详情不提交任何动作；
 * - decoder fail-closed：顶层非法即整份拒绝，单条非法仅丢弃该条。
 */

const SUGGESTION_KINDS: readonly SuggestionKind[] = [
  'relation_proposal',
  'term_preference',
  'identity_duplicate',
  'missing_information',
]

const SUGGESTION_ORIGINS: readonly SuggestionOrigin[] = ['deterministic', 'model']

const SUGGESTION_STATES: readonly SuggestionState[] = [
  'proposed',
  'submitted',
  'resolved',
  'dismissed',
  'expired',
]

const SUGGESTION_ACTIONS: readonly SuggestionAction[] = ['open_details', 'submit', 'dismiss']

const EVIDENCE_KINDS = [
  'confirmed_path',
  'inferred_path',
  'unverified_candidate',
  'unavailable',
] as const

/** 呈现载荷宽松透传：结构不完整时安全降级为 null（不回退 raw enum 解码） */
function decodePresentation(value: unknown): KinshipPresentation | null {
  if (
    !isRecord(value) ||
    typeof value.version !== 'number' ||
    typeof value.summary !== 'string' ||
    !isRecord(value.evidence) ||
    !isOneOf(EVIDENCE_KINDS)(value.evidence.kind)
  ) {
    return null
  }
  return value as unknown as KinshipPresentation
}

const isSuggestionKind = isOneOf(SUGGESTION_KINDS)
const isSuggestionOrigin = isOneOf(SUGGESTION_ORIGINS)
const isSuggestionState = isOneOf(SUGGESTION_STATES)
const isSuggestionAction = isOneOf(SUGGESTION_ACTIONS)

function decodeEvidenceSummary(value: unknown): SuggestionItem['evidence_summary'] | null {
  if (!isRecord(value) || typeof value.fact_count !== 'number' || !Array.isArray(value.facts)) {
    return null
  }
  const facts: Array<{ fact_id: number; revision: number }> = []
  for (const raw of value.facts) {
    if (!isRecord(raw) || typeof raw.fact_id !== 'number' || typeof raw.revision !== 'number') {
      return null
    }
    facts.push({ fact_id: raw.fact_id, revision: raw.revision })
  }
  return { fact_count: value.fact_count, facts }
}

function decodeSuggestionItem(value: unknown): SuggestionItem | null {
  if (!isRecord(value)) return null
  if (
    typeof value.id !== 'number' ||
    typeof value.space_id !== 'number' ||
    !isSuggestionKind(value.kind) ||
    !isSuggestionOrigin(value.origin) ||
    !isSuggestionState(value.state) ||
    typeof value.revision !== 'number' ||
    typeof value.evidence_hash !== 'string' ||
    typeof value.subject_user_id !== 'number' ||
    !isNullableString(value.subject_name ?? null) ||
    !isRecord(value.value) ||
    !Array.isArray(value.allowed_actions) ||
    typeof value.created_at !== 'string'
  ) {
    return null
  }
  const evidence = decodeEvidenceSummary(value.evidence_summary)
  if (evidence === null) return null
  const actions: SuggestionAction[] = []
  for (const raw of value.allowed_actions) {
    if (!isSuggestionAction(raw)) return null
    actions.push(raw)
  }
  const objectUserId =
    value.object_user_id === null || value.object_user_id === undefined
      ? null
      : typeof value.object_user_id === 'number'
        ? value.object_user_id
        : null
  return {
    id: value.id,
    space_id: value.space_id,
    kind: value.kind,
    origin: value.origin,
    state: value.state,
    revision: value.revision,
    evidence_hash: value.evidence_hash,
    subject_user_id: value.subject_user_id,
    object_user_id: objectUserId,
    subject_name: typeof value.subject_name === 'string' ? value.subject_name : null,
    object_name:
      typeof value.object_name === 'string' ? value.object_name : (null as string | null),
    value: value.value,
    presentation: decodePresentation(value.presentation),
    subject_display: isRecord(value.subject_display) ? value.subject_display : null,
    object_display: isRecord(value.object_display) ? value.object_display : null,
    source_state: typeof value.source_state === 'string' ? value.source_state : null,
    recipient_state: typeof value.recipient_state === 'string' ? value.recipient_state : null,
    evidence_summary: evidence,
    allowed_actions: actions,
    expires_at: typeof value.expires_at === 'string' ? value.expires_at : null,
    created_at: value.created_at,
  }
}

/** 按 ID 读取详情（旧通知不依赖首页缓存；与列表同授权/状态/序列化） */
export async function fetchSuggestionDetail(
  spaceId: number,
  suggestionId: number,
): Promise<SuggestionItem> {
  const { data } = await apiClient.get<unknown>(`/steward-suggestions/${suggestionId}`, {
    params: { space_id: spaceId },
  })
  const item = decodeSuggestionItem(data)
  if (item === null) throw new Error('建议详情响应格式无效')
  return item
}

function decodeSuggestionsPage(value: unknown): SuggestionsPage {
  if (
    !isRecord(value) ||
    typeof value.space_id !== 'number' ||
    !Array.isArray(value.items) ||
    (value.next_cursor !== null && typeof value.next_cursor !== 'number')
  ) {
    throw new Error('建议列表响应格式无效')
  }
  const items: SuggestionItem[] = []
  for (const raw of value.items) {
    const item = decodeSuggestionItem(raw)
    if (item !== null) items.push(item)
  }
  return {
    space_id: value.space_id,
    items,
    next_cursor: typeof value.next_cursor === 'number' ? value.next_cursor : null,
  }
}

/** 分页读取建议列表（keyset cursor；首页 cursor 传 null；可按 kind 过滤） */
export async function fetchSuggestions(
  spaceId: number,
  cursor?: number | null,
  limit = 20,
  kind?: SuggestionKind,
): Promise<SuggestionsPage> {
  const { data } = await apiClient.get<unknown>('/steward-suggestions', {
    params: { space_id: spaceId, ...(cursor ? { cursor } : {}), limit, ...(kind ? { kind } : {}) },
  })
  return decodeSuggestionsPage(data)
}

function decodeDismissResult(value: unknown): SuggestionDismissResult {
  if (
    !isRecord(value) ||
    typeof value.id !== 'number' ||
    !isSuggestionState(value.state) ||
    typeof value.revision !== 'number'
  ) {
    throw new Error('建议驳回响应格式无效')
  }
  return {
    id: value.id,
    state: value.state,
    revision: value.revision,
    dismissed_at: typeof value.dismissed_at === 'string' ? value.dismissed_at : null,
    cooldown_until: typeof value.cooldown_until === 'string' ? value.cooldown_until : null,
  }
}

/** 驳回（幂等；CAS expected_revision；仅影响当前收件人的独立状态） */
export async function dismissSuggestion(
  spaceId: number,
  suggestionId: number,
  expectedRevision: number,
): Promise<SuggestionDismissResult> {
  const { data } = await apiClient.post<unknown>(
    `/steward-suggestions/${suggestionId}/dismiss`,
    { expected_revision: expectedRevision },
    { params: { space_id: spaceId } },
  )
  return decodeDismissResult(data)
}

function decodeSubmitPayload(
  value: unknown,
  status: number,
): SuggestionSubmitProposalResult | SuggestionSubmitPreferenceResult {
  if (!isRecord(value) || !isRecord(value.suggestion)) {
    throw new Error('建议提交响应格式无效')
  }
  const suggestion = decodeSuggestionItem(value.suggestion)
  if (suggestion === null) throw new Error('建议提交响应格式无效')
  if (status === 202) {
    const linked = value.linked_proposal
    if (
      !isRecord(linked) ||
      typeof linked.source_fact_id !== 'number' ||
      typeof linked.revision !== 'number' ||
      typeof linked.state !== 'string' ||
      typeof linked.fact_type !== 'string' ||
      !Array.isArray(value.pending_confirmations)
    ) {
      throw new Error('建议提交响应格式无效')
    }
    // 绝不把 submitted 显示为关系已确认：linked state 原样透传
    return {
      suggestion,
      linked_proposal: {
        source_fact_id: linked.source_fact_id,
        revision: linked.revision,
        state: linked.state,
        fact_type: linked.fact_type,
      },
      pending_confirmations: value.pending_confirmations.filter(
        (entry): entry is { account_id: number } =>
          isRecord(entry) && typeof entry.account_id === 'number',
      ),
    }
  }
  const linked = value.linked_preference
  if (
    !isRecord(linked) ||
    typeof linked.term_id !== 'number' ||
    typeof linked.concept_code !== 'string' ||
    typeof linked.term !== 'string'
  ) {
    throw new Error('建议提交响应格式无效')
  }
  return {
    suggestion,
    linked_preference: {
      term_id: linked.term_id,
      concept_code: linked.concept_code,
      term: linked.term,
    },
  }
}

/** 提交建议动作：动作/对象完全由服务端建议决定；confirm 必须显式为 true */
export async function submitSuggestion(
  spaceId: number,
  suggestionId: number,
  body: { expected_revision: number; evidence_hash: string; confirm: boolean },
  idempotencyKey: string,
): Promise<SuggestionSubmitProposalResult | SuggestionSubmitPreferenceResult> {
  const response = await apiClient.post<unknown>(
    `/steward-suggestions/${suggestionId}/submit`,
    body,
    {
      params: { space_id: spaceId },
      headers: { 'Idempotency-Key': idempotencyKey },
      validateStatus: (status) => status === 200 || status === 202,
    },
  )
  return decodeSubmitPayload(response.data, response.status)
}

/** 恢复默认叫法（B-R5，仅本人）：CAS + 稳定抑制；同键幂等 */
export async function restoreSuggestionTerm(
  spaceId: number,
  suggestionId: number,
  body: {
    expected_revision: number
    expected_projection_revision: number
    semantic_hash: string
  },
  idempotencyKey: string,
): Promise<void> {
  await apiClient.post(`/steward-suggestions/${suggestionId}/restore-term`, body, {
    params: { space_id: spaceId },
    headers: { 'Idempotency-Key': idempotencyKey },
  })
}

/** 确认关系提案（仅有权当事人；owner 非端点会被服务端 404 拒绝） */
export async function confirmProposal(
  spaceId: number,
  factId: number,
  expectedRevision: number,
): Promise<void> {
  await apiClient.post(
    `/relationship-proposals/${factId}/confirm`,
    { expected_revision: expectedRevision },
    { params: { space_id: spaceId } },
  )
}

/** 拒绝关系提案（不撤销任何已存在的正式事实） */
export async function rejectProposal(spaceId: number, factId: number): Promise<void> {
  await apiClient.post(`/relationship-proposals/${factId}/reject`, {}, {
    params: { space_id: spaceId },
  })
}
