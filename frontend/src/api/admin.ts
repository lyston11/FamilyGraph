import type {
  ClaimDispute,
  DataRightRequest,
  OwnerInvitation,
  OwnerInvitationCreated,
  SpaceManagerApplication,
} from '@/types/api'

import { apiClient } from './client'

export interface AdminAccountMetadata {
  account_id: number
  subject_id: number
  subject_type: string
  status: string
  locked_until: string | null
  created_at: string
}

export interface AdminSpaceManagerMetadata {
  space_id: number
  space_name: string
  space_kind: string
  manager_user_id: number
  manager_account_id: number | null
  manager_name: string
}

export interface AdminSpaceMetadata {
  id: number
  name: string
  kind: string
  status: string
  created_at: string
  manager_user_id: number | null
  manager_account_id: number | null
  manager_name: string | null
}

export async function fetchAdminAccounts(): Promise<AdminAccountMetadata[]> {
  const { data } = await apiClient.get<AdminAccountMetadata[]>('/admin/accounts')
  return data
}

export async function fetchAdminSpaceManagers(): Promise<AdminSpaceManagerMetadata[]> {
  const { data } = await apiClient.get<AdminSpaceManagerMetadata[]>('/admin/space-managers')
  return data
}

export async function fetchAdminSpaces(): Promise<AdminSpaceMetadata[]> {
  const { data } = await apiClient.get<AdminSpaceMetadata[]>('/admin/spaces')
  return data
}

export interface AdminSpaceMemberMetadata {
  user_id: number
  account_id: number | null
  name: string
  role: string
  status: string
  created_at: string
  updated_at: string
}

/** 单个空间的成员构成（最小元数据；不含家庭内容）。未知空间返回空数组。 */
export async function fetchAdminSpaceMembers(
  spaceId: number,
): Promise<AdminSpaceMemberMetadata[]> {
  const { data } = await apiClient.get<AdminSpaceMemberMetadata[]>(
    `/admin/spaces/${spaceId}/members`,
  )
  return data
}

export interface AdminTransferConsentMetadata {
  id: number
  application_id: number
  space_id: number
  space_name: string
  space_kind: string
  applicant_user_id: number
  applicant_name: string
  current_manager_user_id: number
  current_manager_name: string
  status: string
  requested_at: string
  responded_at: string | null
  response_reason: string | null
}

/** 原管理员同意工单全量视图：系统管理员据此判断申请卡在哪一步 */
export async function fetchAdminTransferConsents(): Promise<AdminTransferConsentMetadata[]> {
  const { data } = await apiClient.get<AdminTransferConsentMetadata[]>(
    '/admin/manager-transfer-consents',
  )
  return data
}

export interface AdminUserRow {
  id: number
  is_admin: boolean
  claim_status: string | null
  profile_status: string | null
  locked_until: string | null
  created_at: string
}

export interface AdminUserLookupRow {
  id: number
  name: string
  claim_status: string | null
  profile_status: string | null
}

export async function fetchAdminUsers(): Promise<AdminUserRow[]> {
  const { data } = await apiClient.get<AdminUserRow[]>('/admin/users')
  return data
}

/** break-glass：按名字前缀还原账号标识（仅供重置 PIN / 数据兑底定位，服务端审计）。 */
export async function fetchAdminUserLookup(name: string): Promise<AdminUserLookupRow[]> {
  const { data } = await apiClient.get<AdminUserLookupRow[]>('/admin/users/lookup', {
    params: { name },
  })
  return data
}

export interface AuditRow {
  id: number
  actor_id: number | null
  action: string
  target_id: number | null
  ip: string | null
  detail_json: string | null
  created_at: string | null
}

export async function fetchAuditLogs(limit = 200): Promise<AuditRow[]> {
  const { data } = await apiClient.get<AuditRow[]>('/admin/audit-logs', { params: { limit } })
  return data
}

export async function adminResetPin(userId: number): Promise<{ pin: string }> {
  const { data } = await apiClient.post<{ pin: string }>(
    `/admin/users/${userId}/reset-pin`,
    { confirm: true },
  )
  return data
}

// ---- v2 operator 治理：owner 邀请 / 数据权利决议 / 争议决议（均 break-glass 审计）----

export async function createOwnerInvitation(): Promise<OwnerInvitationCreated> {
  const { data } = await apiClient.post<OwnerInvitationCreated>('/admin/owner-invitations')
  return data
}

export async function fetchOwnerInvitations(): Promise<OwnerInvitation[]> {
  const { data } = await apiClient.get<OwnerInvitation[]>('/admin/owner-invitations')
  return data
}

export async function revokeOwnerInvitation(invitationId: number): Promise<OwnerInvitation> {
  const { data } = await apiClient.post<OwnerInvitation>(
    `/admin/owner-invitations/${invitationId}/revoke`,
  )
  return data
}

export async function fetchAdminDataRights(params?: {
  status?: string
  type?: string
}): Promise<DataRightRequest[]> {
  const { data } = await apiClient.get<DataRightRequest[]>('/admin/data-rights', { params })
  return data
}

/** 决议更正申请：approve + break-glass 理由必填（后端 BREAK_GLASS_NOTE_REQUIRED 兼底） */
export async function resolveCorrection(
  requestId: number,
  approve: boolean,
  note: string,
): Promise<DataRightRequest> {
  const { data } = await apiClient.post<DataRightRequest>(
    `/admin/data-rights/${requestId}/resolve-correction`,
    { approve, note },
  )
  return data
}

export interface AdminClaimDisputeRow {
  id: number
  profile_id: number
  raised_by_account_id: number
  status: ClaimDispute['status']
  created_at: string
  resolved_at: string | null
  resolution_note: string | null
}

export async function fetchAdminClaimDisputes(status?: string): Promise<AdminClaimDisputeRow[]> {
  const { data } = await apiClient.get<AdminClaimDisputeRow[]>('/admin/claim-disputes', {
    params: status ? { status } : {},
  })
  return data
}

/** 决议认领争议：outcome + break-glass 理由必填；证据原文永不覆盖 */
export async function resolveClaimDispute(
  disputeId: number,
  outcome: 'resolved_claim' | 'resolved_reject',
  note: string,
): Promise<{ id: number; status: string; resolution_note: string | null }> {
  const { data } = await apiClient.post(`/admin/claim-disputes/${disputeId}/resolve`, {
    outcome,
    note,
  })
  return data
}

// ---- 空间管理者申请审批（任务 08-30-space-manager-approval）----

/** 审批队列（status 缺省返回全部；仅申请人/类型/目标最小数据） */
export async function fetchManagerApplications(
  status?: 'pending',
): Promise<SpaceManagerApplication[]> {
  const { data } = await apiClient.get<SpaceManagerApplication[]>('/admin/manager-applications', {
    params: status ? { status } : {},
  })
  return data
}

/** 裁决：approve 效果由后端事务完成；reject 理由必填（后端兜底 422） */
export async function decideManagerApplication(
  applicationId: number,
  decision: 'approve' | 'reject',
  note?: string,
): Promise<SpaceManagerApplication> {
  const { data } = await apiClient.post<SpaceManagerApplication>(
    `/admin/manager-applications/${applicationId}/decision`,
    { decision, note: note || null },
  )
  return data
}
