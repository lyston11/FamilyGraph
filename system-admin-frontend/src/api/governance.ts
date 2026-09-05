/**
 * /admin-api/v1 审批唯一写例外（backend/app/api/admin_governance.py）。
 *
 * approve：理由可选；reject：理由必填非空；两者必须显式 confirm: true。
 * 终态重复裁决 409；未知申请 404。这是整个后台前端仅有的业务写调用。
 */

import { adminRequest } from '@/api/client'
import {
  expectLiteral,
  expectLiteralOrNull,
  expectNumber,
  expectNumberOrNull,
  expectObject,
  expectString,
  expectStringOrNull,
} from '@/api/decode'
import type {
  AdminManagerApplicationOut,
  SpaceKind,
} from '@/types/api'

function decodeApplication(raw: unknown): AdminManagerApplicationOut {
  const obj = expectObject(raw, 'manager application')
  return {
    id: expectNumber(obj['id'], 'id'),
    applicant_user_id: expectNumber(obj['applicant_user_id'], 'applicant_user_id'),
    applicant_name: expectStringOrNull(obj['applicant_name'], 'applicant_name'),
    space_id: expectNumber(obj['space_id'], 'space_id'),
    space_name: expectStringOrNull(obj['space_name'], 'space_name'),
    space_kind: expectLiteralOrNull(
      obj['space_kind'],
      ['household', 'lineage'] as const,
      'space_kind',
    ),
    request_kind: expectLiteral(obj['request_kind'], ['space_admin'] as const, 'request_kind'),
    status: expectLiteral(
      obj['status'],
      ['pending', 'approved', 'rejected'] as const,
      'status',
    ),
    decision_note: expectStringOrNull(obj['decision_note'], 'decision_note'),
    transfer_consent_id: expectNumberOrNull(obj['transfer_consent_id'], 'transfer_consent_id'),
    transfer_consent_status: expectLiteralOrNull(
      obj['transfer_consent_status'],
      ['pending', 'accepted', 'rejected', 'expired'] as const,
      'transfer_consent_status',
    ),
    created_at: expectString(obj['created_at'], 'created_at'),
    decided_at: expectStringOrNull(obj['decided_at'], 'decided_at'),
    system_admin_decided_by: expectNumberOrNull(
      obj['system_admin_decided_by'],
      'system_admin_decided_by',
    ),
  } satisfies AdminManagerApplicationOut & { space_kind: SpaceKind | null }
}

export function apiApproveManagerApplication(
  applicationId: number,
  note: string | null,
): Promise<AdminManagerApplicationOut> {
  return adminRequest<unknown>({
    method: 'post',
    url: `/v1/manager-applications/${applicationId}/approve`,
    // confirm: true 是后端 schema 强制的二次确认字段；缺失/false 一律 422
    data: { confirm: true, ...(note ? { note } : {}) },
  }).then(decodeApplication)
}

export function apiRejectManagerApplication(
  applicationId: number,
  note: string,
): Promise<AdminManagerApplicationOut> {
  return adminRequest<unknown>({
    method: 'post',
    url: `/v1/manager-applications/${applicationId}/reject`,
    data: { confirm: true, note },
  }).then(decodeApplication)
}
