import type { FamilySpace, SpaceMemberInfo } from '@/types/api'

import { apiClient } from './client'

export async function fetchSpaces(): Promise<FamilySpace[]> {
  const { data } = await apiClient.get<FamilySpace[]>('/spaces')
  return data
}

export async function createSpace(name: string): Promise<FamilySpace> {
  const { data } = await apiClient.post<FamilySpace>('/spaces', { name })
  return data
}

export async function fetchSpaceMembers(spaceId: number): Promise<SpaceMemberInfo[]> {
  const { data } = await apiClient.get<SpaceMemberInfo[]>(`/spaces/${spaceId}/members`)
  return data
}

/** 邀请已有账号进空间 → pending（幂等）；managed 新建走建档向导直连 */
export async function inviteToSpace(spaceId: number, userId: number): Promise<SpaceMemberInfo> {
  const { data } = await apiClient.post<SpaceMemberInfo>(`/spaces/${spaceId}/members`, {
    user_id: userId,
  })
  return data
}

/** 被请求方接受/拒绝 */
export async function resolveMembership(
  memberId: number,
  action: 'accept' | 'reject',
): Promise<SpaceMemberInfo> {
  const { data } = await apiClient.post<SpaceMemberInfo>(
    `/space-memberships/${memberId}/${action}`,
  )
  return data
}

/** D8 断连轨：owner 移除活跃成员 或 本人退出；pending 时发起方撤回 */
export async function removeOrWithdrawMembership(memberId: number): Promise<void> {
  await apiClient.delete(`/space-memberships/${memberId}`)
}

/** 家族视图摘要卡：申请进入对方家庭空间（m2c；幂等） */
export async function joinByUser(targetUserId: number): Promise<void> {
  await apiClient.post('/spaces/join-by-user', { target_user_id: targetUserId })
}
