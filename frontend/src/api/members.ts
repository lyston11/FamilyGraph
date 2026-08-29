import type {
  ClanDisclosure,
  DisclosureMatrix,
  Member,
  MemberCreatePayload,
  MemberCreateResponse,
  MemberUpdatePayload,
} from '@/types/api'

import { apiClient } from './client'

/** 与我相关的档案列表（自己 + 我创建的；admin 全部） */
export async function fetchMembers(): Promise<Member[]> {
  const { data } = await apiClient.get<Member[]>('/users')
  return data
}

export async function fetchMember(id: number): Promise<Member> {
  const { data } = await apiClient.get<Member>(`/users/${id}`)
  return data
}

/** 原子建档：一次提交名字+关系；Idempotency-Key 保证重试/并发不重复建边。
 *  响应携带一次性 PIN 明文（仅首次）；幂等重放时 pin=null。 */
export async function createMember(
  payload: MemberCreatePayload,
  idempotencyKey: string,
): Promise<MemberCreateResponse> {
  const { data } = await apiClient.post<MemberCreateResponse>('/users', payload, {
    headers: { 'Idempotency-Key': idempotencyKey },
  })
  return data
}

export async function updateMember(id: number, patch: MemberUpdatePayload): Promise<Member> {
  const { data } = await apiClient.patch<Member>(`/users/${id}`, patch)
  return data
}

/** AD-9 披露开关整体替换（基础五类）；携带 spaceId 时为逐空间覆盖（仅本人） */
export async function updateDisclosure(
  id: number,
  disclosure: ClanDisclosure,
  spaceId?: number,
): Promise<Member> {
  const body = spaceId === undefined ? disclosure : { ...disclosure, space_id: spaceId }
  const { data } = await apiClient.put<Member>(`/users/${id}/disclosure`, body)
  return data
}

/** 披露偏好合并矩阵：全局 + 逐空间覆盖（v2 Gap3；读者域与写入一致） */
export async function fetchDisclosureMatrix(id: number): Promise<DisclosureMatrix> {
  const { data } = await apiClient.get<DisclosureMatrix>(`/users/${id}/disclosure`)
  return data
}

/** 删除档案：二次确认在后端以 confirm_name 校验，不符 409 */
export async function removeMember(id: number, confirmName: string): Promise<void> {
  await apiClient.delete(`/users/${id}`, { params: { confirm_name: confirmName } })
}

/** 名字前缀搜索（可见范围内，m1b 添加关系 / m1c 邀请用） */
export async function fetchMembersByPrefix(q: string): Promise<Member[]> {
  const { data } = await apiClient.get<Member[]>('/users', { params: { q } })
  return data
}
