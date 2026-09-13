import { isRecord } from './decode'
import { apiClient } from './client'

import type {
  InferredEdgeActionResult,
  InferredEdgeConfirmResult,
} from '@/types/api'

/**
 * 管家推测边操作客户端合同（09-13 推测层；backend/app/api/steward_inferred.py）：
 *
 * - 三个动作均 POST `/steward-inferred-edges/{id}/{action}?space_id=`，
 *   body `{ expected_revision }` CAS + 幂等；
 * - confirm：有权当事人（关系端点本人或合法代管人）→ 200 已转正；
 *   无权 viewer → 202 生成关系提案（绝不显示为已确认，linked state 原样透传）；
 * - dismiss / reinstate：恒 200；
 * - 解码 fail-closed：形状非法即抛错（写操作的响应不静默降级）。
 */

function decodeActionEdge(value: unknown): InferredEdgeActionResult {
  if (
    !isRecord(value) ||
    typeof value.id !== 'number' ||
    typeof value.status !== 'string' ||
    typeof value.revision !== 'number'
  ) {
    throw new Error('推测边操作响应格式无效')
  }
  return { id: value.id, status: value.status, revision: value.revision }
}

function decodeConfirmPayload(
  value: unknown,
  status: number,
): InferredEdgeConfirmResult {
  if (!isRecord(value) || !isRecord(value.edge)) {
    throw new Error('推测边确认响应格式无效')
  }
  const edge = decodeActionEdge(value.edge)
  let linked: InferredEdgeConfirmResult['linked_proposal'] = null
  if (value.linked_proposal !== null) {
    if (
      !isRecord(value.linked_proposal) ||
      typeof value.linked_proposal.source_fact_id !== 'number' ||
      typeof value.linked_proposal.revision !== 'number' ||
      typeof value.linked_proposal.state !== 'string' ||
      typeof value.linked_proposal.fact_type !== 'string'
    ) {
      throw new Error('推测边确认响应格式无效')
    }
    linked = {
      source_fact_id: value.linked_proposal.source_fact_id,
      revision: value.linked_proposal.revision,
      state: value.linked_proposal.state,
      fact_type: value.linked_proposal.fact_type,
    }
  }
  if (!Array.isArray(value.pending_confirmations)) {
    throw new Error('推测边确认响应格式无效')
  }
  const pending = value.pending_confirmations.filter(
    (entry): entry is { account_id: number } =>
      isRecord(entry) && typeof entry.account_id === 'number',
  )
  if (status === 202 && linked === null) {
    // 202 必须带提案引用：绝不把「待对方确认」渲染成已确认
    throw new Error('推测边确认响应格式无效')
  }
  return { edge, linked_proposal: linked, pending_confirmations: pending }
}

/** 确认推测关系：200 已转正 / 202 已代为提案（待有权当事人确认） */
export async function confirmInferredEdge(
  spaceId: number,
  edgeId: number,
  expectedRevision: number,
): Promise<InferredEdgeConfirmResult> {
  const response = await apiClient.post<unknown>(
    `/steward-inferred-edges/${edgeId}/confirm`,
    { expected_revision: expectedRevision },
    {
      params: { space_id: spaceId },
      validateStatus: (status) => status === 200 || status === 202,
    },
  )
  return decodeConfirmPayload(response.data, response.status)
}

/** 驳回推测关系（同证据哈希冷却，不再重复上树） */
export async function dismissInferredEdge(
  spaceId: number,
  edgeId: number,
  expectedRevision: number,
): Promise<InferredEdgeActionResult> {
  const { data } = await apiClient.post<unknown>(
    `/steward-inferred-edges/${edgeId}/dismiss`,
    { expected_revision: expectedRevision },
    { params: { space_id: spaceId } },
  )
  return decodeActionEdge(data)
}

/** 撤销驳回（rejected → proposed；需要无同三元组活跃行且不超上限） */
export async function reinstateInferredEdge(
  spaceId: number,
  edgeId: number,
  expectedRevision: number,
): Promise<InferredEdgeActionResult> {
  const { data } = await apiClient.post<unknown>(
    `/steward-inferred-edges/${edgeId}/reinstate`,
    { expected_revision: expectedRevision },
    { params: { space_id: spaceId } },
  )
  return decodeActionEdge(data)
}
