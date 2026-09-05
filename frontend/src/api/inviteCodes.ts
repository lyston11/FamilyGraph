import type { CreateInviteCodePayload, InviteCode } from '@/types/api'

import { apiClient } from './client'

/**
 * 邀请码 client（09-05 Chunk C 合同；字段与 backend/app/schemas/invite_code.py 一一对应）。
 * 错误为统一外壳：INVITE_CODE_INVALID（400 字段级文案）/ INVITE_CODE_FORBIDDEN（403，
 * provisional 或非空间成员）/ INVITE_CODE_STATE_CONFLICT（409 已撤销）/ 409 已是成员。
 */

/** 我的码列表（创建者视角；明文码随行，渲染 …/register?code=XXX） */
export async function fetchMyInviteCodes(): Promise<InviteCode[]> {
  const { data } = await apiClient.get<InviteCode[]>('/invite-codes')
  return data
}

/** 创建码：household/lineage 须带所在空间；stranger 可设使用上限；资格由服务端裁定 */
export async function createInviteCode(payload: CreateInviteCodePayload): Promise<InviteCode> {
  const body: Record<string, unknown> = { kind: payload.kind }
  if (payload.space_id != null) {
    body.space_id = payload.space_id
  }
  if (payload.max_uses != null) {
    body.max_uses = payload.max_uses
  }
  if (payload.ttl_days != null) {
    body.ttl_days = payload.ttl_days
  }
  const { data } = await apiClient.post<InviteCode>('/invite-codes', body)
  return data
}

/** 撤销码：创建者本人，或该码所在空间的 active 空间管理员（陌生人码仅创建者） */
export async function revokeInviteCode(codeId: number): Promise<InviteCode> {
  const { data } = await apiClient.delete<InviteCode>(`/invite-codes/${codeId}`)
  return data
}

/**
 * 设置页填码加入空间：与注册填码同一加入语义（决策 8）。
 * 陌生人码在登录态兑换被 400 明确拒绝（INVITE_CODE_STRANGER_REGISTER_ONLY）。
 */
export async function redeemInviteCode(rawCode: string): Promise<InviteCode> {
  const { data } = await apiClient.post<InviteCode>('/me/invite-codes/redeem', {
    code: rawCode,
  })
  return data
}
