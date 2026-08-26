import type { FamilySpace, OwnershipTransfer, SpaceMemberInfo, SpaceProfileRefInfo } from '@/types/api'

import { apiClient } from './client'

export async function fetchSpaces(): Promise<FamilySpace[]> {
  const { data } = await apiClient.get<FamilySpace[]>('/spaces')
  return data
}

/** 创建空间：owner 即 active 成员；kind 默认 household（v2 §0.4） */
export async function createSpace(name: string, kind?: 'household' | 'lineage'): Promise<FamilySpace> {
  const { data } = await apiClient.post<FamilySpace>('/spaces', { name, kind })
  return data
}

export async function fetchSpaceMembers(spaceId: number): Promise<SpaceMemberInfo[]> {
  const { data } = await apiClient.get<SpaceMemberInfo[]>(`/spaces/${spaceId}/members`)
  return data
}

/** 待确档最小引用（AC-F2）：仅名字投影；非 active 成员 404 */
export async function fetchSpaceProfileRefs(spaceId: number): Promise<SpaceProfileRefInfo[]> {
  const { data } = await apiClient.get<SpaceProfileRefInfo[]>(`/spaces/${spaceId}/profile-refs`)
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

// ---- owner 移交（AC-F5）----

export async function createOwnershipTransfer(
  spaceId: number,
  toUserId: number,
): Promise<OwnershipTransfer> {
  const { data } = await apiClient.post<OwnershipTransfer>(
    `/spaces/${spaceId}/ownership-transfers`,
    { to_user_id: toUserId },
  )
  return data
}

export async function fetchOwnershipTransfers(spaceId: number): Promise<OwnershipTransfer[]> {
  const { data } = await apiClient.get<OwnershipTransfer[]>(
    `/spaces/${spaceId}/ownership-transfers`,
  )
  return data
}

/** 仅受让人可接受；发起人或受让人可取消 pending（commands.ownership FSM） */
export async function respondOwnershipTransfer(
  transferId: number,
  action: 'accept' | 'cancel',
): Promise<OwnershipTransfer> {
  const { data } = await apiClient.post<OwnershipTransfer>(
    `/ownership-transfers/${transferId}/${action}`,
  )
  return data
}

/** 画布位置记忆：读取 / 批量保存（m1d；仅 active 成员） */
export async function getSpacePositions(
  spaceId: number,
): Promise<{ user_id: number; x: number; y: number }[]> {
  const { data } = await apiClient.get<{ user_id: number; x: number; y: number }[]>(
    `/spaces/${spaceId}/positions`,
  )
  return data
}

export async function putSpacePositions(
  spaceId: number,
  items: { user_id: number; x: number; y: number }[],
): Promise<void> {
  await apiClient.put(`/spaces/${spaceId}/positions`, { items })
}
