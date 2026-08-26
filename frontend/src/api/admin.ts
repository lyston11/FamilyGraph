import type {
  ClaimDispute,
  DataRightRequest,
  OwnerInvitation,
  OwnerInvitationCreated,
} from '@/types/api'

import { apiClient } from './client'

export interface AdminUserRow {
  id: number
  name: string
  is_admin: boolean
  gender: string
  privacy_mode: string
  /** 账号状态（managed|claimed；无凭据为 null） */
  claim_status: string | null
  /** 档案确档状态（provisional|identity_confirmed） */
  profile_status: string | null
  created_by: number | null
  locked_until: string | null
  created_at: string
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

export async function fetchAdminUsers(): Promise<AdminUserRow[]> {
  const { data } = await apiClient.get<AdminUserRow[]>('/admin/users')
  return data
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
