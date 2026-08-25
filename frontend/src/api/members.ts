import type {
  ClanDisclosure,
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

/** 建档：响应携带一次性 PIN 明文，此后任何接口不可再取 */
export async function createMember(payload: MemberCreatePayload): Promise<MemberCreateResponse> {
  const { data } = await apiClient.post<MemberCreateResponse>('/users', payload)
  return data
}

export async function updateMember(id: number, patch: MemberUpdatePayload): Promise<Member> {
  const { data } = await apiClient.patch<Member>(`/users/${id}`, patch)
  return data
}

/** AD-9 披露开关整体替换（五键恰好） */
export async function updateDisclosure(
  id: number,
  disclosure: ClanDisclosure,
): Promise<Member> {
  const { data } = await apiClient.put<Member>(`/users/${id}/disclosure`, disclosure)
  return data
}

/** 删除档案：二次确认在后端以 confirm_name 校验，不符 409 */
export async function removeMember(id: number, confirmName: string): Promise<void> {
  await apiClient.delete(`/users/${id}`, { params: { confirm_name: confirmName } })
}
