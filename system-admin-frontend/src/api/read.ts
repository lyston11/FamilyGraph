/**
 * /admin-api/v1 只读模型 + 访问会话（backend/app/api/admin_read.py）。
 *
 * - 普通读取：无需票据；敏感读取（profile/avatar/attachments/relations/facts）
 *   带 adminAccessTarget，由 client 拦截器附 X-Admin-Access-Session；
 * - 列表统一 AdminPageOut envelope；全部经轻量运行时解码。
 */

import { adminRequest } from '@/api/client'
import {
  expectArray,
  expectBoolean,
  expectLiteral,
  expectLiteralOrNull,
  expectNumber,
  expectNumberOrNull,
  expectObject,
  expectString,
  expectStringArray,
  expectStringOrNull,
  isRecord,
} from '@/api/decode'
import type {
  AdminAccessSessionCreatePayload,
  AdminAccessSessionOut,
  AdminAgentJobOut,
  AdminAgentRunOut,
  AdminAttachmentMetadataOut,
  AdminAuditAccessOut,
  AdminFactOut,
  AdminMemberOut,
  AdminNotificationOut,
  AdminOperationsQueueItemOut,
  AdminOverviewPageOut,
  AdminPageOut,
  AdminProfileOut,
  AdminRelationOut,
  AdminSpaceAdminOut,
  AdminSpaceDetailOut,
  AdminSpaceSummaryOut,
  AccessTargetType,
  SpaceKind,
  SourceFactType,
} from '@/types/api'

// ---- envelope ----

function decodePage<T>(raw: unknown, decodeItem: (item: unknown) => T): AdminPageOut<T> {
  const obj = expectObject(raw, 'page')
  return {
    items: expectArray(obj['items'], 'items').map((item) => decodeItem(item)),
    page: expectNumber(obj['page'], 'page'),
    page_size: expectNumber(obj['page_size'], 'page_size'),
    total: expectNumber(obj['total'], 'total'),
    has_more: expectBoolean(obj['has_more'], 'has_more'),
  }
}

export interface ListQuery {
  page?: number
  pageSize?: number
  search?: string | null
  status?: string | null
}

function listParams(query: ListQuery): Record<string, string | number | null | undefined> {
  return {
    page: query.page ?? 1,
    page_size: query.pageSize ?? 50,
    search: query.search || null,
    status: query.status || null,
  }
}

const SPACE_KINDS = ['household', 'lineage'] as const

function decodeSpaceKind(raw: unknown, label: string): SpaceKind {
  return expectLiteral(raw, SPACE_KINDS, label)
}

// ---- overview ----

function decodeOverviewItem(raw: unknown): import('@/types/api').AdminOverviewItemOut {
  const obj = expectObject(raw, 'overview item')
  return {
    space_id: expectNumber(obj['space_id'], 'space_id'),
    name: expectString(obj['name'], 'name'),
    kind: decodeSpaceKind(obj['kind'], 'kind'),
    created_at: expectString(obj['created_at'], 'created_at'),
    manager_user_id: expectNumberOrNull(obj['manager_user_id'], 'manager_user_id'),
    manager_name: expectStringOrNull(obj['manager_name'], 'manager_name'),
    member_count: expectNumber(obj['member_count'], 'member_count'),
    status: expectLiteral(obj['status'], ['healthy', 'anomaly'] as const, 'status'),
    anomalies: expectStringArray(obj['anomalies'], 'anomalies'),
  }
}

export function apiOverview(query: ListQuery): Promise<AdminOverviewPageOut> {
  return adminRequest<unknown>({ method: 'get', url: '/v1/overview', params: listParams(query) }).then(
    (raw) => {
      const obj = expectObject(raw, 'overview page')
      const totals = expectObject(obj['totals'], 'totals')
      return {
        ...decodePage(obj, decodeOverviewItem),
        totals: {
          spaces_total: expectNumber(totals['spaces_total'], 'spaces_total'),
          healthy_spaces: expectNumber(totals['healthy_spaces'], 'healthy_spaces'),
          anomaly_spaces: expectNumber(totals['anomaly_spaces'], 'anomaly_spaces'),
          active_space_admins: expectNumber(totals['active_space_admins'], 'active_space_admins'),
          pending_applications: expectNumber(totals['pending_applications'], 'pending_applications'),
        },
      }
    },
  )
}

// ---- space admins ----

function decodeSpaceAdmin(raw: unknown): AdminSpaceAdminOut {
  const obj = expectObject(raw, 'space admin')
  return {
    admin_user_id: expectNumber(obj['admin_user_id'], 'admin_user_id'),
    name: expectString(obj['name'], 'name'),
    gender: expectString(obj['gender'], 'gender'),
    profile_status: expectLiteral(
      obj['profile_status'],
      ['provisional', 'identity_confirmed'] as const,
      'profile_status',
    ),
    account_status: expectLiteral(
      obj['account_status'],
      ['managed', 'claimed'] as const,
      'account_status',
    ),
    avatar_available: expectBoolean(obj['avatar_available'], 'avatar_available'),
    space_count: expectNumber(obj['space_count'], 'space_count'),
    created_at: expectString(obj['created_at'], 'created_at'),
  }
}

export function apiSpaceAdmins(query: ListQuery): Promise<AdminPageOut<AdminSpaceAdminOut>> {
  return adminRequest<unknown>({ method: 'get', url: '/v1/space-admins', params: listParams(query) }).then(
    (raw) => decodePage(raw, decodeSpaceAdmin),
  )
}

function decodeSpaceSummary(raw: unknown): AdminSpaceSummaryOut {
  const obj = expectObject(raw, 'space summary')
  return {
    space_id: expectNumber(obj['space_id'], 'space_id'),
    name: expectString(obj['name'], 'name'),
    kind: decodeSpaceKind(obj['kind'], 'kind'),
    created_at: expectString(obj['created_at'], 'created_at'),
    manager_user_id: expectNumberOrNull(obj['manager_user_id'], 'manager_user_id'),
    manager_name: expectStringOrNull(obj['manager_name'], 'manager_name'),
  }
}

export function apiSpaceAdminSpaces(
  adminUserId: number,
  query: Omit<ListQuery, 'search' | 'status'>,
): Promise<AdminPageOut<AdminSpaceSummaryOut>> {
  return adminRequest<unknown>({
    method: 'get',
    url: `/v1/space-admins/${adminUserId}/spaces`,
    params: { page: query.page ?? 1, page_size: query.pageSize ?? 50 },
  }).then((raw) => decodePage(raw, decodeSpaceSummary))
}

// ---- space detail / members ----

export function apiSpaceDetail(spaceId: number): Promise<AdminSpaceDetailOut> {
  return adminRequest<unknown>({ method: 'get', url: `/v1/spaces/${spaceId}` }).then((raw) => {
    const obj = expectObject(raw, 'space detail')
    return {
      space_id: expectNumber(obj['space_id'], 'space_id'),
      name: expectString(obj['name'], 'name'),
      kind: decodeSpaceKind(obj['kind'], 'kind'),
      created_at: expectString(obj['created_at'], 'created_at'),
      manager_user_id: expectNumberOrNull(obj['manager_user_id'], 'manager_user_id'),
      manager_name: expectStringOrNull(obj['manager_name'], 'manager_name'),
      member_count: expectNumber(obj['member_count'], 'member_count'),
      anomalies: expectStringArray(obj['anomalies'], 'anomalies'),
    }
  })
}

function decodeMember(raw: unknown): AdminMemberOut {
  const obj = expectObject(raw, 'member')
  return {
    user_id: expectNumber(obj['user_id'], 'user_id'),
    name: expectString(obj['name'], 'name'),
    role: expectLiteral(obj['role'], ['space_admin', 'member'] as const, 'role'),
    status: expectString(obj['status'], 'status'),
    created_at: expectString(obj['created_at'], 'created_at'),
    updated_at: expectString(obj['updated_at'], 'updated_at'),
  }
}

export function apiSpaceMembers(
  spaceId: number,
  query: ListQuery,
): Promise<AdminPageOut<AdminMemberOut>> {
  return adminRequest<unknown>({
    method: 'get',
    url: `/v1/spaces/${spaceId}/members`,
    params: listParams(query),
  }).then((raw) => decodePage(raw, decodeMember))
}

// ---- 敏感：档案 / 附件（user 票据）----

function decodeStructuredDate(raw: unknown, label: string): AdminProfileOut['birth'] {
  if (raw === null || raw === undefined) return null
  const obj = expectObject(raw, label)
  return {
    cal_type: expectString(obj['cal_type'], `${label}.cal_type`),
    date: expectStringOrNull(obj['date'], `${label}.date`),
    mirror_date:
      obj['mirror_date'] === undefined
        ? undefined
        : expectStringOrNull(obj['mirror_date'], `${label}.mirror_date`),
    is_leap_month:
      obj['is_leap_month'] === undefined
        ? undefined
        : expectBoolean(obj['is_leap_month'], `${label}.is_leap_month`),
    original_text:
      obj['original_text'] === undefined
        ? undefined
        : expectStringOrNull(obj['original_text'], `${label}.original_text`),
  }
}

function decodeProfile(raw: unknown): AdminProfileOut {
  const obj = expectObject(raw, 'profile')
  return {
    id: expectNumber(obj['id'], 'id'),
    name: expectString(obj['name'], 'name'),
    gender: expectString(obj['gender'], 'gender'),
    birth: decodeStructuredDate(obj['birth'], 'birth'),
    death: decodeStructuredDate(obj['death'], 'death'),
    bio: expectStringOrNull(obj['bio'], 'bio'),
    avatar_available: expectBoolean(obj['avatar_available'], 'avatar_available'),
    profile_status: expectLiteral(
      obj['profile_status'],
      ['provisional', 'identity_confirmed'] as const,
      'profile_status',
    ),
    claim_status: expectLiteral(obj['claim_status'], ['managed', 'claimed'] as const, 'claim_status'),
    created_at: expectString(obj['created_at'], 'created_at'),
  }
}

export function apiUserProfile(userId: number): Promise<AdminProfileOut> {
  return adminRequest<unknown>({
    method: 'get',
    url: `/v1/users/${userId}/profile`,
    accessTarget: { targetType: 'user', targetId: userId },
  }).then(decodeProfile)
}

/** 头像缩略图：带票据取二进制（no-store）；调用方负责 revoke object URL。 */
export function apiUserAvatarThumbnail(userId: number): Promise<Blob> {
  return adminRequest<Blob>({
    method: 'get',
    url: `/v1/users/${userId}/avatar/thumbnail`,
    accessTarget: { targetType: 'user', targetId: userId },
    responseType: 'blob',
  }).then((raw) => {
    if (raw instanceof Blob) return raw
    throw new Error('avatar thumbnail is not a blob')
  })
}

function decodeAttachment(raw: unknown): AdminAttachmentMetadataOut {
  const obj = expectObject(raw, 'attachment')
  return {
    id: expectNumber(obj['id'], 'id'),
    type: expectLiteral(obj['type'], ['image', 'link', 'location'] as const, 'type'),
    title_safe: expectStringOrNull(obj['title_safe'], 'title_safe'),
    created_at: expectString(obj['created_at'], 'created_at'),
  }
}

export function apiUserAttachments(
  userId: number,
  query: Omit<ListQuery, 'search' | 'status'>,
): Promise<AdminPageOut<AdminAttachmentMetadataOut>> {
  return adminRequest<unknown>({
    method: 'get',
    url: `/v1/users/${userId}/attachments`,
    params: { page: query.page ?? 1, page_size: query.pageSize ?? 50 },
    accessTarget: { targetType: 'user', targetId: userId },
  }).then((raw) => decodePage(raw, decodeAttachment))
}

// ---- 敏感：关系 / confirmed 事实（space 票据）----

function decodeRelation(raw: unknown): AdminRelationOut {
  const obj = expectObject(raw, 'relation')
  return {
    id: expectNumber(obj['id'], 'id'),
    from_user_id: expectNumber(obj['from_user_id'], 'from_user_id'),
    from_user_name: expectStringOrNull(obj['from_user_name'], 'from_user_name'),
    to_user_id: expectNumber(obj['to_user_id'], 'to_user_id'),
    to_user_name: expectStringOrNull(obj['to_user_name'], 'to_user_name'),
    dir_class: expectLiteral(
      obj['dir_class'],
      ['elder', 'younger', 'peer', 'spouse'] as const,
      'dir_class',
    ),
    status: expectString(obj['status'], 'status'),
    space_id: expectNumber(obj['space_id'], 'space_id'),
    label_safe: expectStringOrNull(obj['label_safe'], 'label_safe'),
    created_at: expectString(obj['created_at'], 'created_at'),
    updated_at: expectString(obj['updated_at'], 'updated_at'),
  }
}

export function apiSpaceRelations(
  spaceId: number,
  query: Omit<ListQuery, 'search'>,
): Promise<AdminPageOut<AdminRelationOut>> {
  return adminRequest<unknown>({
    method: 'get',
    url: `/v1/spaces/${spaceId}/relations`,
    params: { page: query.page ?? 1, page_size: query.pageSize ?? 50, status: query.status || null },
    accessTarget: { targetType: 'space', targetId: spaceId },
  }).then((raw) => decodePage(raw, decodeRelation))
}

const FACT_TYPES: readonly SourceFactType[] = [
  'biological_parent',
  'adoptive_parent',
  'step_parent',
  'guardian',
  'spouse',
  'partner',
  'direct_sibling',
]

function decodeFact(raw: unknown): AdminFactOut {
  const obj = expectObject(raw, 'fact')
  return {
    id: expectNumber(obj['id'], 'id'),
    fact_type: expectLiteral(obj['fact_type'], FACT_TYPES, 'fact_type'),
    subject_user_id: expectNumber(obj['subject_user_id'], 'subject_user_id'),
    subject_name: expectStringOrNull(obj['subject_name'], 'subject_name'),
    object_user_id: expectNumber(obj['object_user_id'], 'object_user_id'),
    object_name: expectStringOrNull(obj['object_name'], 'object_name'),
    space_id: expectNumberOrNull(obj['space_id'], 'space_id'),
    state: expectLiteral(obj['state'], ['confirmed'] as const, 'state'),
    provenance: expectString(obj['provenance'], 'provenance'),
    revision: expectNumber(obj['revision'], 'revision'),
    created_at: expectString(obj['created_at'], 'created_at'),
    updated_at: expectString(obj['updated_at'], 'updated_at'),
  }
}

export function apiSpaceFacts(
  spaceId: number,
  query: Omit<ListQuery, 'search' | 'status'>,
): Promise<AdminPageOut<AdminFactOut>> {
  return adminRequest<unknown>({
    method: 'get',
    url: `/v1/spaces/${spaceId}/facts`,
    params: { page: query.page ?? 1, page_size: query.pageSize ?? 50 },
    accessTarget: { targetType: 'space', targetId: spaceId },
  }).then((raw) => decodePage(raw, decodeFact))
}

// ---- 运营队列 / 通知 ----

function decodeQueueItem(raw: unknown): AdminOperationsQueueItemOut {
  const obj = expectObject(raw, 'queue item')
  return {
    kind: expectLiteral(obj['kind'], ['space_anomaly', 'manager_application'] as const, 'kind'),
    status: expectString(obj['status'], 'status'),
    reference_id: expectNumber(obj['reference_id'], 'reference_id'),
    space_id: expectNumberOrNull(obj['space_id'], 'space_id'),
    space_name: expectStringOrNull(obj['space_name'], 'space_name'),
    space_kind: expectLiteralOrNull(obj['space_kind'], SPACE_KINDS, 'space_kind'),
    anomaly: expectStringOrNull(obj['anomaly'], 'anomaly'),
    applicant_user_id: expectNumberOrNull(obj['applicant_user_id'], 'applicant_user_id'),
    applicant_name: expectStringOrNull(obj['applicant_name'], 'applicant_name'),
    request_kind: expectStringOrNull(obj['request_kind'], 'request_kind'),
    created_at: expectString(obj['created_at'], 'created_at'),
  }
}

export function apiOperationsQueue(
  query: ListQuery & { kind?: 'space_anomaly' | 'manager_application' | null },
): Promise<AdminPageOut<AdminOperationsQueueItemOut>> {
  return adminRequest<unknown>({
    method: 'get',
    url: '/v1/operations/queue',
    params: { ...listParams(query), kind: query.kind || null },
  }).then((raw) => decodePage(raw, decodeQueueItem))
}

function decodeNotification(raw: unknown): AdminNotificationOut {
  const obj = expectObject(raw, 'notification')
  return {
    id: expectNumber(obj['id'], 'id'),
    kind: expectString(obj['kind'], 'kind'),
    space_id: expectNumber(obj['space_id'], 'space_id'),
    recipient_account_id: expectNumber(obj['recipient_account_id'], 'recipient_account_id'),
    actor_user_id: expectNumberOrNull(obj['actor_user_id'], 'actor_user_id'),
    title: expectString(obj['title'], 'title'),
    summary: expectStringOrNull(obj['summary'], 'summary'),
    created_at: expectString(obj['created_at'], 'created_at'),
    read_at: expectStringOrNull(obj['read_at'], 'read_at'),
  }
}

export function apiOperationsNotifications(
  query: Omit<ListQuery, 'search' | 'status'> & { spaceId?: number | null; read?: boolean | null },
): Promise<AdminPageOut<AdminNotificationOut>> {
  return adminRequest<unknown>({
    method: 'get',
    url: '/v1/operations/notifications',
    params: {
      page: query.page ?? 1,
      page_size: query.pageSize ?? 50,
      space_id: query.spaceId ?? null,
      read: query.read ?? null,
    },
  }).then((raw) => decodePage(raw, decodeNotification))
}

// ---- Agent 运行诊断 ----

/** 二次脱敏诊断解码：只接受四个安全字段，其余字段即使后端误发也不进入视图。 */
export function decodeAgentError(raw: unknown): AdminAgentRunOut['error'] {
  if (raw === null || raw === undefined) return null
  const obj = expectObject(raw, 'agent error')
  return {
    error_code: expectStringOrNull(obj['error_code'], 'error_code'),
    component: expectStringOrNull(obj['component'], 'component'),
    stack_location: expectStringOrNull(obj['stack_location'], 'stack_location'),
    summary: expectStringOrNull(obj['summary'], 'summary'),
  }
}

function decodeAgentRun(raw: unknown): AdminAgentRunOut {
  const obj = expectObject(raw, 'agent run')
  return {
    id: expectNumber(obj['id'], 'id'),
    session_id: expectNumber(obj['session_id'], 'session_id'),
    job_id: expectNumberOrNull(obj['job_id'], 'job_id'),
    space_id: expectNumber(obj['space_id'], 'space_id'),
    account_id: expectNumber(obj['account_id'], 'account_id'),
    kind: expectString(obj['kind'], 'kind'),
    status: expectString(obj['status'], 'status'),
    attempt: expectNumber(obj['attempt'], 'attempt'),
    max_attempts: expectNumber(obj['max_attempts'], 'max_attempts'),
    lease_expires_at: expectStringOrNull(obj['lease_expires_at'], 'lease_expires_at'),
    heartbeat_at: expectStringOrNull(obj['heartbeat_at'], 'heartbeat_at'),
    cancel_requested: expectBoolean(obj['cancel_requested'], 'cancel_requested'),
    error_code: expectStringOrNull(obj['error_code'], 'error_code'),
    error: decodeAgentError(obj['error']),
    created_at: expectString(obj['created_at'], 'created_at'),
    updated_at: expectString(obj['updated_at'], 'updated_at'),
    settled_at: expectStringOrNull(obj['settled_at'], 'settled_at'),
  }
}

export function apiAgentRuns(
  query: Omit<ListQuery, 'search'> & { spaceId?: number | null; signal?: AbortSignal },
): Promise<AdminPageOut<AdminAgentRunOut>> {
  return adminRequest<unknown>({
    method: 'get',
    url: '/v1/agent/runs',
    params: {
      page: query.page ?? 1,
      page_size: query.pageSize ?? 50,
      space_id: query.spaceId ?? null,
      status: query.status || null,
    },
    signal: query.signal,
  }).then((raw) => decodePage(raw, decodeAgentRun))
}

function decodeAgentJob(raw: unknown): AdminAgentJobOut {
  const obj = expectObject(raw, 'agent job')
  return {
    id: expectNumber(obj['id'], 'id'),
    run_id: expectNumber(obj['run_id'], 'run_id'),
    space_id: expectNumberOrNull(obj['space_id'], 'space_id'),
    account_id: expectNumberOrNull(obj['account_id'], 'account_id'),
    kind: expectString(obj['kind'], 'kind'),
    status: expectString(obj['status'], 'status'),
    attempt: expectNumber(obj['attempt'], 'attempt'),
    max_attempts: expectNumber(obj['max_attempts'], 'max_attempts'),
    lease_expires_at: expectStringOrNull(obj['lease_expires_at'], 'lease_expires_at'),
    heartbeat_at: expectStringOrNull(obj['heartbeat_at'], 'heartbeat_at'),
    cancel_requested: expectBoolean(obj['cancel_requested'], 'cancel_requested'),
    error: decodeAgentError(obj['error']),
    created_at: expectString(obj['created_at'], 'created_at'),
    updated_at: expectString(obj['updated_at'], 'updated_at'),
  }
}

export function apiAgentJobs(
  query: Omit<ListQuery, 'search'> & { spaceId?: number | null; signal?: AbortSignal },
): Promise<AdminPageOut<AdminAgentJobOut>> {
  return adminRequest<unknown>({
    method: 'get',
    url: '/v1/agent/jobs',
    params: {
      page: query.page ?? 1,
      page_size: query.pageSize ?? 50,
      space_id: query.spaceId ?? null,
      status: query.status || null,
    },
    signal: query.signal,
  }).then((raw) => decodePage(raw, decodeAgentJob))
}

// ---- 审计时间线 ----

function decodeAuditEntry(raw: unknown): AdminAuditAccessOut {
  const obj = expectObject(raw, 'audit entry')
  const filters = obj['filters']
  return {
    id: expectNumber(obj['id'], 'id'),
    system_admin_id: expectNumberOrNull(obj['system_admin_id'], 'system_admin_id'),
    session_id: expectNumberOrNull(obj['session_id'], 'session_id'),
    action: expectString(obj['action'], 'action'),
    target_type: expectLiteralOrNull(obj['target_type'], ['user', 'space'] as const, 'target_type'),
    target_id: expectNumberOrNull(obj['target_id'], 'target_id'),
    endpoint: expectString(obj['endpoint'], 'endpoint'),
    filters: isRecord(filters) ? filters : {},
    result_count: expectNumberOrNull(obj['result_count'], 'result_count'),
    request_id: expectStringOrNull(obj['request_id'], 'request_id'),
    ip: expectStringOrNull(obj['ip'], 'ip'),
    created_at: expectString(obj['created_at'], 'created_at'),
  }
}

export function apiAccessAudits(
  query: Omit<ListQuery, 'search' | 'status'> & {
    targetType?: AccessTargetType | null
    targetId?: number | null
  },
): Promise<AdminPageOut<AdminAuditAccessOut>> {
  return adminRequest<unknown>({
    method: 'get',
    url: '/v1/audit/access',
    params: {
      page: query.page ?? 1,
      page_size: query.pageSize ?? 50,
      target_type: query.targetType ?? null,
      target_id: query.targetId ?? null,
    },
  }).then((raw) => decodePage(raw, decodeAuditEntry))
}

// ---- 访问会话 ----

export function apiCreateAccessSession(
  payload: AdminAccessSessionCreatePayload,
): Promise<AdminAccessSessionOut> {
  return adminRequest<unknown>({ method: 'post', url: '/v1/access-sessions', data: payload }).then(
    (raw) => {
      const obj = expectObject(raw, 'access session')
      return {
        session_id: expectString(obj['session_id'], 'session_id'),
        target_type: expectLiteral(obj['target_type'], ['user', 'space'] as const, 'target_type'),
        target_id: expectNumber(obj['target_id'], 'target_id'),
        allowed_scopes: expectStringArray(obj['allowed_scopes'], 'allowed_scopes'),
        issued_at: expectString(obj['issued_at'], 'issued_at'),
        expires_at: expectString(obj['expires_at'], 'expires_at'),
      }
    },
  )
}
