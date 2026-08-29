import type {
  ConnectionRequestPayload,
  GraphData,
  Relation,
} from '@/types/api'

import { apiClient } from './client'

/** 我的图数据：family=±depth；clan=全连通分量。指定 spaceId 时服务端将结果限定到该空间。 */
export async function fetchMyGraph(
  scope: 'family' | 'clan' = 'family',
  depth = 1,
  spaceId?: number,
): Promise<GraphData> {
  const params: { scope: 'family' | 'clan'; depth: number; space_id?: number } = { scope, depth }
  if (spaceId !== undefined) params.space_id = spaceId
  const { data } = await apiClient.get<GraphData>('/graph/me', { params })
  return data
}

/** 向已有账号发起合并请求（space_membership 由 m1c 放开） */
export async function createConnectionRequest(
  payload: ConnectionRequestPayload,
): Promise<Relation> {
  const { data } = await apiClient.post<Relation>('/connection-requests', payload)
  return data
}

/** 发给我的 pending 请求列表（审批 UI 归 m2c） */
export async function fetchIncomingConnections(): Promise<Relation[]> {
  const { data } = await apiClient.get<Relation[]>('/connections/incoming')
  return data
}

/** 接受/拒绝合并请求（仅被请求方）；cancel 仅发起方；revoke 任一方（D8 断连轨） */
export async function resolveConnection(
  edgeId: number,
  action: 'accept' | 'reject' | 'cancel',
): Promise<Relation> {
  const { data } = await apiClient.post<Relation>(`/connection-requests/${edgeId}/${action}`)
  return data
}

export async function revokeRelation(edgeId: number): Promise<Relation> {
  const { data } = await apiClient.post<Relation>(`/relations/${edgeId}/revoke`)
  return data
}
