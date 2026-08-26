import type {
  ClaimDispute,
  DataRightRequest,
  FactReview,
  FactReviewDecision,
  FamilySpace,
  IdentityConfirmResult,
} from '@/types/api'

import { apiClient } from './client'

// ---- 「这是我」合并确认与确档清单（F-1）----

/** 本人确认「这是我」：Account managed→claimed 与本人 Profile 确认合并转换。 */
export async function confirmIdentity(): Promise<IdentityConfirmResult> {
  const { data } = await apiClient.post<IdentityConfirmResult>('/me/identity/confirm')
  return data
}

export async function fetchFactReviews(): Promise<FactReview[]> {
  const { data } = await apiClient.get<FactReview[]>('/me/fact-reviews')
  return data
}

export async function decideFactReview(
  reviewId: number,
  decision: FactReviewDecision,
  note?: string | null,
): Promise<FactReview> {
  const { data } = await apiClient.post<FactReview>(`/me/fact-reviews/${reviewId}/decide`, {
    decision,
    note: note || null,
  })
  return data
}

// ---- owner 移交（AC-F5）——见 api/spaces.ts（空间域）----

// ---- owner onboarding 兑换（AC-F3）----

/** 兑换 owner 邀请：创建独立 LineageSpace 并授予 owner，不连接其他空间。 */
export async function redeemOwnerInvitation(token: string): Promise<FamilySpace> {
  const { data } = await apiClient.post<FamilySpace>('/owner-invitations/redeem', { token })
  return data
}

// ---- 数据权利（AC-F6）----

export async function requestExport(): Promise<DataRightRequest> {
  const { data } = await apiClient.post<DataRightRequest>('/data-rights/export')
  return data
}

export async function requestCorrection(fields: Record<string, unknown>): Promise<DataRightRequest> {
  const { data } = await apiClient.post<DataRightRequest>('/data-rights/correct', { fields })
  return data
}

export async function requestDeletion(): Promise<DataRightRequest> {
  const { data } = await apiClient.post<DataRightRequest>('/data-rights/delete')
  return data
}

export async function fetchDataRights(): Promise<DataRightRequest[]> {
  const { data } = await apiClient.get<DataRightRequest[]>('/data-rights')
  return data
}

/** 执行删除类请求：confirm_name 为二次确认（与档案名字一致）。 */
export async function executeDelete(requestId: number, confirmName: string): Promise<void> {
  await apiClient.post(`/data-rights/${requestId}/execute-delete`, { confirm_name: confirmName })
}

/** 下载导出文件：走授权端点（blob），过期后端返回 410。 */
export async function downloadExport(requestId: number): Promise<Blob> {
  const { data } = await apiClient.get<Blob>(`/data-rights/${requestId}/export-file`, {
    responseType: 'blob',
  })
  return data
}

// ---- 认领争议 ----

export async function raiseClaimDispute(
  profileId: number,
  evidence: Record<string, unknown>,
): Promise<ClaimDispute> {
  const { data } = await apiClient.post<ClaimDispute>('/claim-disputes', {
    profile_id: profileId,
    evidence,
  })
  return data
}

export async function fetchMyClaimDisputes(): Promise<ClaimDispute[]> {
  const { data } = await apiClient.get<ClaimDispute[]>('/me/claim-disputes')
  return data
}

export async function withdrawClaimDispute(disputeId: number): Promise<ClaimDispute> {
  const { data } = await apiClient.post<ClaimDispute>(`/claim-disputes/${disputeId}/withdraw`)
  return data
}
