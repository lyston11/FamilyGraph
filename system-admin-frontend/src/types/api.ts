/**
 * /admin-api 响应类型：与 backend/app/schemas/admin_auth.py、admin_read.py
 * 一一对应（人工同步，extra="forbid" 白名单）。
 *
 * 已知边界（architecture.md §12.1）：
 * - AdminProfileOut 无 updated_at（User 无此列）、无 avatar_path；
 * - 附件元数据无 size（Attachment 无此列），url_or_path/description 永不出现；
 * - Agent 诊断只有 error_code/component/stack_location/summary 四字段；
 * - 错误外壳 {"error": {code, message, detail?}} 对齐 app/errors.py。
 */

export const ADMIN_ACCESS_SESSION_HEADER = 'X-Admin-Access-Session'

// ---- 错误外壳（backend app/errors.py + main.py _error_envelope）----

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    detail?: unknown
  }
}

// ---- 认证（schemas/admin_auth.py）----

export type AdminAccountStatus = 'managed' | 'claimed'

/** AdminSessionOut：白名单精确集合，无任何家庭档案字段。 */
export interface AdminSessionOut {
  id: number
  username: string
  password_must_change: boolean
  status: AdminAccountStatus
}

export interface AdminTokenPairResponse {
  access_token: string
  refresh_token: string
  token_type: string
  admin: AdminSessionOut
}

export interface LogoutResponse {
  success: boolean
}

// ---- 统一分页 envelope（schemas/admin_read.py AdminPageOut）----

export interface AdminPageOut<T> {
  items: T[]
  page: number
  page_size: number
  total: number
  has_more: boolean
}

// ---- 概览 / 聚合根 ----

export type SpaceKind = 'household' | 'lineage'

export interface AdminOverviewTotalsOut {
  spaces_total: number
  healthy_spaces: number
  anomaly_spaces: number
  active_space_admins: number
  pending_applications: number
}

export interface AdminOverviewItemOut {
  space_id: number
  name: string
  kind: SpaceKind
  created_at: string
  manager_user_id: number | null
  manager_name: string | null
  member_count: number
  status: 'healthy' | 'anomaly'
  anomalies: string[]
}

export interface AdminOverviewPageOut extends AdminPageOut<AdminOverviewItemOut> {
  totals: AdminOverviewTotalsOut
}

export interface AdminSpaceAdminOut {
  admin_user_id: number
  name: string
  gender: string
  profile_status: 'provisional' | 'identity_confirmed'
  account_status: AdminAccountStatus
  avatar_available: boolean
  space_count: number
  created_at: string
}

export interface AdminSpaceSummaryOut {
  space_id: number
  name: string
  kind: SpaceKind
  created_at: string
  manager_user_id: number | null
  manager_name: string | null
}

export interface AdminSpaceDetailOut {
  space_id: number
  name: string
  kind: SpaceKind
  created_at: string
  manager_user_id: number | null
  manager_name: string | null
  member_count: number
  anomalies: string[]
}

// ---- 空间成员 / 档案 / 附件 ----

export interface AdminMemberOut {
  user_id: number
  name: string
  role: 'space_admin' | 'member'
  status: string
  created_at: string
  updated_at: string
}

/** 生卒结构化值（users.birth/death JSON：cal_type + date + mirror_date…）。 */
export interface AdminStructuredDate {
  cal_type: string
  date: string | null
  mirror_date?: string | null
  is_leap_month?: boolean
  original_text?: string | null
}

/** 基础档案投影：无 updated_at / avatar_path / privacy_mode / created_by / 凭据。 */
export interface AdminProfileOut {
  id: number
  name: string
  gender: string
  birth: AdminStructuredDate | null
  death: AdminStructuredDate | null
  bio: string | null
  avatar_available: boolean
  profile_status: 'provisional' | 'identity_confirmed'
  claim_status: AdminAccountStatus
  created_at: string
}

/** 附件安全元数据：无 size、无 url_or_path、无 description、无下载链接。 */
export interface AdminAttachmentMetadataOut {
  id: number
  type: 'image' | 'link' | 'location'
  title_safe: string | null
  created_at: string
}

// ---- 关系边与 confirmed 事实 ----

export type RelationDirClass = 'elder' | 'younger' | 'peer' | 'spouse'

export interface AdminRelationOut {
  id: number
  from_user_id: number
  from_user_name: string | null
  to_user_id: number
  to_user_name: string | null
  dir_class: RelationDirClass
  status: string
  space_id: number
  label_safe: string | null
  created_at: string
  updated_at: string
}

export type SourceFactType =
  | 'biological_parent'
  | 'adoptive_parent'
  | 'step_parent'
  | 'guardian'
  | 'spouse'
  | 'partner'
  | 'direct_sibling'

export interface AdminFactOut {
  id: number
  fact_type: SourceFactType
  subject_user_id: number
  subject_name: string | null
  object_user_id: number
  object_name: string | null
  space_id: number | null
  state: 'confirmed'
  provenance: string
  revision: number
  created_at: string
  updated_at: string
}

// ---- 运营队列 / 通知 ----

export interface AdminOperationsQueueItemOut {
  kind: 'space_anomaly' | 'manager_application'
  status: string
  reference_id: number
  space_id: number | null
  space_name: string | null
  space_kind: SpaceKind | null
  anomaly: string | null
  applicant_user_id: number | null
  applicant_name: string | null
  request_kind: string | null
  created_at: string
}

export interface AdminNotificationOut {
  id: number
  kind: string
  space_id: number
  recipient_account_id: number
  actor_user_id: number | null
  title: string
  summary: string | null
  created_at: string
  read_at: string | null
}

// ---- Agent 运行诊断（二次脱敏：只有四个安全字段）----

export interface AdminAgentErrorOut {
  error_code: string | null
  component: string | null
  stack_location: string | null
  summary: string | null
}

export type AgentStatus =
  | 'queued'
  | 'leased'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'expired'

export interface AdminAgentRunOut {
  id: number
  session_id: number
  job_id: number | null
  space_id: number
  account_id: number
  kind: string
  status: string
  attempt: number
  max_attempts: number
  lease_expires_at: string | null
  heartbeat_at: string | null
  cancel_requested: boolean
  error_code: string | null
  error: AdminAgentErrorOut | null
  created_at: string
  updated_at: string
  settled_at: string | null
}

export interface AdminAgentJobOut {
  id: number
  run_id: number
  space_id: number | null
  account_id: number | null
  kind: string
  status: string
  attempt: number
  max_attempts: number
  lease_expires_at: string | null
  heartbeat_at: string | null
  cancel_requested: boolean
  error: AdminAgentErrorOut | null
  created_at: string
  updated_at: string
}

// ---- 审计时间线 ----

export interface AdminAuditAccessOut {
  id: number
  system_admin_id: number | null
  session_id: number | null
  action: string
  target_type: 'user' | 'space' | null
  target_id: number | null
  endpoint: string
  filters: Record<string, unknown>
  result_count: number | null
  request_id: string | null
  ip: string | null
  created_at: string
}

// ---- 访问会话 ----

export type AccessTargetType = 'user' | 'space'

export interface AdminAccessSessionCreatePayload {
  target_type: AccessTargetType
  target_id: number
  reason: string
}

/** 明文票据只在创建响应出现一次；前端只存内存。 */
export interface AdminAccessSessionOut {
  session_id: string
  target_type: AccessTargetType
  target_id: number
  allowed_scopes: string[]
  issued_at: string
  expires_at: string
}

// ---- 审批唯一写例外 ----

export interface AdminApplicationApprovePayload {
  confirm: true
  note?: string
}

export interface AdminApplicationRejectPayload {
  confirm: true
  note: string
}

export interface AdminManagerApplicationOut {
  id: number
  applicant_user_id: number
  applicant_name: string | null
  space_id: number
  space_name: string | null
  space_kind: SpaceKind | null
  request_kind: 'space_admin'
  status: 'pending' | 'approved' | 'rejected'
  decision_note: string | null
  transfer_consent_id: number | null
  transfer_consent_status: 'pending' | 'accepted' | 'rejected' | 'expired' | null
  created_at: string
  decided_at: string | null
  system_admin_decided_by: number | null
}

// ---- 后端错误码（app/errors.py 中 admin 相关子集）----

export const ADMIN_ERROR_CODES = {
  INVALID_CREDENTIALS: 'ADMIN_INVALID_CREDENTIALS',
  UNAUTHORIZED: 'ADMIN_UNAUTHORIZED',
  PASSWORD_CHANGE_REQUIRED: 'ADMIN_PASSWORD_CHANGE_REQUIRED',
  PASSWORD_TOO_WEAK: 'ADMIN_PASSWORD_TOO_WEAK',
  USERNAME_TAKEN: 'ADMIN_USERNAME_TAKEN',
  ACCESS_SESSION_INVALID: 'ADMIN_ACCESS_SESSION_INVALID',
  TARGET_NOT_FOUND: 'ADMIN_TARGET_NOT_FOUND',
  RESOURCE_NOT_FOUND: 'ADMIN_RESOURCE_NOT_FOUND',
  VALIDATION_ERROR: 'VALIDATION_ERROR',
  APPLICATION_DECIDED: 'SPACE_MANAGER_APPLICATION_DECIDED',
  APPLICATION_NOTE_REQUIRED: 'SPACE_MANAGER_APPLICATION_NOTE_REQUIRED',
} as const
