import type {
  EligibleManagerTarget,
  FamilySpace,
  FamilySpaceOptions,
  MemberRelationLabelEdge,
  ManagerApplicationStatus,
  ManagerRequestKind,
  ManagerTransferConsent,
  OwnershipTransfer,
  PendingInvitation,
  SpaceManagerApplication,
  SpaceManagementBootstrap,
  SpaceMemberInfo,
  SpaceProfileRefInfo,
} from '@/types/api'

import { apiClient } from './client'

export async function fetchSpaces(): Promise<FamilySpace[]> {
  const { data } = await apiClient.get<FamilySpace[]>('/spaces')
  return data
}

export async function createSpace(
  name: string,
  kind?: 'household' | 'lineage',
  lineageSpaceId?: number | null,
): Promise<FamilySpace> {
  const { data } = await apiClient.post<FamilySpace>('/spaces', {
    name,
    kind,
    lineage_space_id: lineageSpaceId ?? null,
  })
  return data
}

/**
 * 空间设置（既有 PATCH /spaces/{space_id} 合同）：请求仍仅包含空间名；
 * current_role 只作为列表读取投影返回，不接受客户端写入，也不能替代服务端授权。
 */
export async function updateSpace(spaceId: number, name: string): Promise<FamilySpace> {
  const { data } = await apiClient.patch<FamilySpace>(`/spaces/${spaceId}`, { name })
  return data
}

/**
 * 设置/解除家庭空间所属家族（PUT /spaces/{space_id}/lineage-link，仅空间管理员；
 * lineageSpaceId=null 即解除）。配对是「当前家族空间」选择器切换维度的数据基础。
 */
export async function setSpaceLineageLink(
  spaceId: number,
  lineageSpaceId: number | null,
): Promise<FamilySpace> {
  const { data } = await apiClient.put<FamilySpace>(`/spaces/${spaceId}/lineage-link`, {
    lineage_space_id: lineageSpaceId,
  })
  return data
}

/**
 * 提交成为指定 lineage 家族空间管理员的申请（需系统管理员审批 + 原管理员同意）。
 * 邀请成员不走此流程，active member 可直接邀请。
 */
export async function submitManagerApplication(
  requestKind: ManagerRequestKind,
  payload: { spaceId: number },
): Promise<SpaceManagerApplication> {
  const { data } = await apiClient.post<SpaceManagerApplication>('/spaces/manager-applications', {
    request_kind: requestKind,
    space_id: payload.spaceId,
  })
  return data
}

/** 可申请管理员的目标 lineage 空间；资格与目标名称均由服务端裁定 */
export async function fetchEligibleManagerTargets(): Promise<EligibleManagerTarget[]> {
  const { data } = await apiClient.get<EligibleManagerTarget[]>(
    '/spaces/manager-applications/eligible-targets',
  )
  return data
}

/** 我作为原管理员收到的交接同意工单 */
export async function fetchMyTransferConsents(): Promise<ManagerTransferConsent[]> {
  const { data } = await apiClient.get<ManagerTransferConsent[]>(
    '/spaces/manager-transfer-consents/mine',
  )
  return data
}

/** 原管理员处理工单：accept 后系统管理员才可完成交接；reject 终止申请 */
export async function respondTransferConsent(
  consentId: number,
  decision: 'accept' | 'reject',
  reason?: string,
): Promise<ManagerTransferConsent> {
  const { data } = await apiClient.post<ManagerTransferConsent>(
    `/spaces/manager-transfer-consents/${consentId}/decision`,
    { decision, reason: reason || null },
  )
  return data
}

/** 我的管理者申请与状态（pending/approved/rejected + 平台备注） */
export async function fetchMyManagerApplications(
  status?: ManagerApplicationStatus,
): Promise<SpaceManagerApplication[]> {
  const { data } = await apiClient.get<SpaceManagerApplication[]>(
    '/spaces/manager-applications/mine',
    { params: status ? { status } : {} },
  )
  return data
}

export async function fetchSpaceManagementBootstrap(
  spaceId: number,
): Promise<SpaceManagementBootstrap> {
  const { data } = await apiClient.get<SpaceManagementBootstrap>(
    `/spaces/${spaceId}/management-bootstrap`,
  )
  return data
}
export async function fetchSpaceMembers(spaceId: number): Promise<SpaceMemberInfo[]> {
  const { data } = await apiClient.get<SpaceMemberInfo[]>(`/spaces/${spaceId}/members`)
  return data
}

/**
 * 发给我的 / 我发起的 pending 空间邀请（跨全部空间）。
 *
 * 自足投影：自带 `space_name`，**不依赖当前空间上下文**——pending 受邀人还不是该空间
 * active 成员，读不到该空间的通知（安全 404），通知中心又只按当前空间加载，
 * 于是邀请此前在任何界面都不可达。本端点就是那条缺失的入口。
 */
export async function fetchMyInvitations(): Promise<PendingInvitation[]> {
  const { data } = await apiClient.get<PendingInvitation[]>('/spaces/invitations')
  return data
}

/** 待确档最小引用（AC-F2）：仅名字投影；非 active 成员 404 */
export async function fetchSpaceProfileRefs(spaceId: number): Promise<SpaceProfileRefInfo[]> {
  const { data } = await apiClient.get<SpaceProfileRefInfo[]>(`/spaces/${spaceId}/profile-refs`)
  return data
}

/** 邀请已有账号进空间 → pending（幂等）；managed 新建走建档向导直连 */
export async function inviteToSpace(
  spaceId: number,
  userId: number,
  relationLabel: string,
): Promise<SpaceMemberInfo> {
  const { data } = await apiClient.post<SpaceMemberInfo>(`/spaces/${spaceId}/members`, {
    user_id: userId,
    relation_label: relationLabel,
  })
  return data
}

/**
 * 个人公示页在当前家族空间下的双向选择（只读）。
 *
 * 返回「我在该家族空间下的家庭空间（邀请方向）」与「对方在该家族空间下的家庭
 * 空间（申请方向）」及各自状态；双方不同族时两列表为空（走邀请码途径）。
 */
export async function fetchFamilySpaceOptions(
  lineageSpaceId: number,
  targetUserId: number,
): Promise<FamilySpaceOptions> {
  const { data } = await apiClient.get<FamilySpaceOptions>('/spaces/family-space-options', {
    params: { lineage_space_id: lineageSpaceId, target_user_id: targetUserId },
  })
  return data
}

/**
 * 在当前家族空间范围内邀请对方加入我的家庭空间（只产生 pending）。
 *
 * 服务端复核：双方同族 + 该空间是我在该家族空间下的家庭空间。
 */
export async function inviteIntoFamilyHousehold(
  lineageSpaceId: number,
  spaceId: number,
  userId: number,
  relationLabel: string,
): Promise<SpaceMemberInfo> {
  const { data } = await apiClient.post<SpaceMemberInfo>('/spaces/family-invitations', {
    lineage_space_id: lineageSpaceId,
    space_id: spaceId,
    user_id: userId,
    relation_label: relationLabel,
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

/**
 * 房主批准一条待处理加入（09-20 审批链）。
 *
 * 申请人/发起人不得自批；`origin='invite'` 批准后仍需受邀人本人接受，
 * `join_request`/`code` 批准即生效。
 */
export async function approveMembership(memberId: number): Promise<SpaceMemberInfo> {
  const { data } = await apiClient.post<SpaceMemberInfo>(
    `/space-memberships/${memberId}/approve`,
  )
  return data
}

/**
 * 设置/清除我与某成员之间的关系词（自由文本；空串 = 清除标注）。
 *
 * 仅该对两端本人可改，改完即时生效，无需对方确认、无需房主审批。
 */
export async function setMemberRelationLabel(
  spaceId: number,
  otherUserId: number,
  label: string,
): Promise<MemberRelationLabelEdge | null> {
  const { data } = await apiClient.put<MemberRelationLabelEdge | null>(
    `/spaces/${spaceId}/member-relation-label`,
    { other_user_id: otherUserId, label },
  )
  return data
}

/** D8 断连轨：owner 移除活跃成员 或 本人退出；pending 时发起方撤回 */
export async function removeOrWithdrawMembership(memberId: number): Promise<void> {
  await apiClient.delete(`/space-memberships/${memberId}`)
}

/**
 * 申请加入对方在当前家族空间下的家庭空间（m2c；幂等）。
 *
 * 服务端复核：双方同族 + 该空间是对方在该家族空间下的家庭空间；只产生 pending，
 * 由该家庭空间管理员批准。
 */
export async function joinByUser(
  lineageSpaceId: number,
  targetUserId: number,
  relationLabel: string,
  spaceId?: number,
): Promise<SpaceMemberInfo> {
  const { data } = await apiClient.post<SpaceMemberInfo>('/spaces/join-by-user', {
    lineage_space_id: lineageSpaceId,
    target_user_id: targetUserId,
    relation_label: relationLabel,
    ...(spaceId === undefined ? {} : { space_id: spaceId }),
  })
  return data
}

/**
 * 家庭空间成员申请读取该家庭所属的家族空间（独立申请，由目标本人审批）。
 *
 * 家庭空间成员资格不等于家族空间成员资格：本调用只登记 pending，
 * 批准前家族树仍不可读。
 */
export async function requestLineageAccess(householdSpaceId: number): Promise<SpaceMemberInfo> {
  const { data } = await apiClient.post<SpaceMemberInfo>('/spaces/lineage-access-requests', {
    household_space_id: householdSpaceId,
  })
  return data
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
