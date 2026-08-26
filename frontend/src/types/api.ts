/**
 * 后端 API 类型定义：与 backend/app/schemas/auth.py 字段一一对应（人工同步）。
 * 错误结构见 spec/backend/error-handling.md。
 */

export interface UserOut {
  id: number
  name: string
  is_admin: boolean
  pin_must_change: boolean
  /** 账号生命周期：managed → claimed（唯一转换点=首登认领，v2 §0.3） */
  claim_status: ClaimStatus
  /** 档案确档状态：provisional → identity_confirmed（路由守卫判定源，v2 Gap2） */
  profile_status: ProfileStatus
}

export interface TokenPairResponse {
  access_token: string
  refresh_token: string
  token_type: string
  user: UserOut
}

/** 同名同 PIN 消歧 409 响应体（architecture.md §2 AD-2） */
export interface ChallengeResponse {
  challenge_id: string
  candidates: ChallengeCandidate[]
}

export interface ChallengeCandidate {
  id: number
  name: string
  /** m1a：候选档案的代管创建者名（managed 档案提示） */
  created_by_name?: string | null
}

export interface BootstrapStatusResponse {
  initialized: boolean
}

export interface InitializeResponse {
  user: UserOut
  one_time_pin: string
}

/** 统一错误外壳 */
export interface ApiErrorBody {
  error: {
    code: string
    message: string
    detail?: unknown
  }
}

export const ERROR_CODES = {
  AUTH_UNAUTHORIZED: 'AUTH_UNAUTHORIZED',
  AUTH_INVALID_CREDENTIALS: 'AUTH_INVALID_CREDENTIALS',
  ACCOUNT_LOCKED: 'ACCOUNT_LOCKED',
  CHALLENGE_INVALID: 'CHALLENGE_INVALID',
  INVALID_REFRESH_TOKEN: 'INVALID_REFRESH_TOKEN',
  PIN_CHANGE_REQUIRED: 'PIN_CHANGE_REQUIRED',
  BOOTSTRAP_ALREADY_INITIALIZED: 'BOOTSTRAP_ALREADY_INITIALIZED',
  USER_NOT_FOUND: 'USER_NOT_FOUND',
  CUSTODY_HANDOVER_DONE: 'CUSTODY_HANDOVER_DONE',
  CONFIRM_NAME_MISMATCH: 'CONFIRM_NAME_MISMATCH',
  SPACE_NOT_FOUND: 'SPACE_NOT_FOUND',
  IDENTITY_INVALID_TRANSITION: 'IDENTITY_INVALID_TRANSITION',
  OWNER_TRANSFER_REQUIRED: 'OWNER_TRANSFER_REQUIRED',
  OWNER_INVITATION_INVALID: 'OWNER_INVITATION_INVALID',
  OWNER_INVITATION_ACCOUNT_NOT_CLAIMED: 'OWNER_INVITATION_ACCOUNT_NOT_CLAIMED',
  OWNER_TRANSFER_INVALID: 'OWNER_TRANSFER_INVALID',
  DATA_RIGHT_REQUEST_NOT_FOUND: 'DATA_RIGHT_REQUEST_NOT_FOUND',
  DATA_RIGHT_INVALID_TRANSITION: 'DATA_RIGHT_INVALID_TRANSITION',
  DATA_RIGHT_EXPORT_NOT_READY: 'DATA_RIGHT_EXPORT_NOT_READY',
  DATA_RIGHT_REQUEST_EXPIRED: 'DATA_RIGHT_REQUEST_EXPIRED',
  CLAIM_DISPUTE_NOT_FOUND: 'CLAIM_DISPUTE_NOT_FOUND',
  BREAK_GLASS_NOTE_REQUIRED: 'BREAK_GLASS_NOTE_REQUIRED',
} as const

export type ErrorCode = (typeof ERROR_CODES)[keyof typeof ERROR_CODES]

// ---- m1a 成员档案域 ----

export type GenderType = 'm' | 'f' | 'unknown'
export type CalType = 'solar' | 'lunar' | 'none'
export type PrivacyMode = 'perpetual' | 'handover'
/** 账号状态机：managed → claimed（唯一转换点=首登认领，v2 §0.3） */
export type ClaimStatus = 'managed' | 'claimed'
/** 档案状态机：provisional → identity_confirmed（本人「这是我」确认） */
export type ProfileStatus = 'provisional' | 'identity_confirmed'
/** v2 四级可见性（§0.1；none 在 API 层转 404，不出现于载荷） */
export type VisibilityLevel =
  | 'self_private'
  | 'household_detail'
  | 'lineage_summary'
  | 'none'

/** 生卒结构化值（D7）：历别 + YYYY-MM-DD + 原文备注（换算 m1d 接入） */
export interface StructuredDate {
  cal_type: CalType
  date: string | null
  original_text?: string | null
}

/** AD-9 家族空间外披露开关（五类，默认全 false） */
export interface ClanDisclosure {
  avatar: boolean
  photos: boolean
  dates: boolean
  bio: boolean
  attachments: boolean
}

export type SpaceKind = 'household' | 'lineage'
/** 空间角色（§0.2）：owner/space_admin/member；household 可另有 guest */
export type SpaceRole = 'owner' | 'space_admin' | 'member' | 'guest'

/** 当前主体对该档案的可用操作（resolve_relation 投影） */
export interface MemberPermissions {
  edit: boolean
  delete: boolean
}

export interface Member {
  id: number
  name: string
  is_admin: boolean
  gender: GenderType
  birth: StructuredDate | null
  death: StructuredDate | null
  bio: string | null
  avatar_path: string | null
  privacy_mode: PrivacyMode
  claim_status: ClaimStatus
  created_by: number | null
  created_at: string
  clan_disclosure: ClanDisclosure
  permissions: MemberPermissions
}

export interface MemberCreatePayload {
  name: string
  gender?: GenderType
  birth?: StructuredDate | null
  death?: StructuredDate | null
  bio?: string | null
  privacy_mode?: PrivacyMode
  /** AD-4 新建例外：managed 新档由代管人创建 → 空间成员直接 active */
  space_membership?: { space_id: number } | null
}

export interface MemberUpdatePayload {
  name?: string
  gender?: GenderType
  birth?: StructuredDate | null
  death?: StructuredDate | null
  bio?: string | null
}

/** 建档响应：PIN 明文仅此一次，之后任何接口不可再取 */
export interface MemberCreateResponse {
  user: Member
  pin: string
}

// ---- v2 待确档最小引用（AC-F2 可观测性；后端 SpaceProfileRefOut） ----

/** 仅名字投影：无日期/简介/头像等任何档案字段 */
export interface SpaceProfileRefInfo {
  profile_id: number
  name: string
  added_at: string
}

// ---- m1b 关系域（与后端 schemas/relation.py 一一对应） ----

export type DirClass = 'elder' | 'younger' | 'peer' | 'spouse'
export type RelationStatus = 'pending' | 'active' | 'rejected' | 'cancelled' | 'revoked'

export interface RelationView {
  /** viewer 视角的结构类：from_user 原样，to_user 反译 elder<->younger（D3） */
  dir_class: DirClass
  /** 恒为创建者视角原文 */
  label: string | null
  label_from_creator: boolean
}

export interface Relation {
  id: number
  from_user: number
  to_user: number
  dir_class: DirClass
  label: string | null
  status: RelationStatus
  created_by: number
  view: RelationView
}

export interface GraphNode {
  id: number
  name: string
  gender: GenderType
  /** v2 四级可见性（none 节点不返回）；lineage_summary 节点仅基线字段 */
  visibility: Exclude<VisibilityLevel, 'none'>
}

export interface GraphData {
  nodes: GraphNode[]
  edges: Relation[]
  scope: 'family' | 'clan'
}

export interface ConnectionRequestPayload {
  target_id: number
  dir_class: DirClass
  label?: string | null
}

// ---- m1c 家庭空间域（与后端 schemas/space.py 一一对应） ----

export interface FamilySpace {
  id: number
  name: string
  owner_id: number
  kind: 'household' | 'lineage'
  created_at: string
  pending_count: number
  member_count: number
}

export interface SpaceMemberInfo {
  id: number
  space_id: number
  user_id: number
  /** 列表端点附带的名字投影（后端 SpaceMemberOut.user_name） */
  user_name?: string | null
  added_by: number | null
  role: SpaceRole
  status: 'pending' | 'active' | 'rejected' | 'withdrawn' | 'removed'
  updated_at: string
}

// ---- v2 Foundation 治理域（与 backend/app/schemas/v2_foundation.py 一一对应） ----

/** 「这是我」合并确认结果（F-1 唯一合法联动） */
export interface IdentityConfirmResult {
  account_claimed: boolean
  profile_confirmed: boolean
}

/** 确档清单项：proposed → confirmed | disputed（终态） */
export interface FactReview {
  id: number
  item_type: string
  item_ref_json: Record<string, unknown>
  status: 'proposed' | 'confirmed' | 'disputed'
  decided_at: string | null
  created_at: string
}

export type FactReviewDecision = 'confirmed' | 'disputed'

/** owner onboarding 邀请（服务端只存 hash；token 明文仅签发响应返回一次） */
export interface OwnerInvitation {
  id: number
  expires_at: string
  used_at: string | null
  revoked_at: string | null
  created_at: string
}

export interface OwnerInvitationCreated extends OwnerInvitation {
  token: string
}

/** owner 移交 FSM：pending → accepted/cancelled/expired */
export interface OwnershipTransfer {
  id: number
  space_id: number
  from_user: number
  to_user: number
  status: 'pending' | 'accepted' | 'cancelled' | 'expired'
  created_at: string
  decided_at: string | null
}

/** 数据权利请求：export/correct/delete 统一状态机 */
export interface DataRightRequest {
  id: number
  type: 'export' | 'correct' | 'delete'
  status: 'pending' | 'processing' | 'completed' | 'rejected' | 'expired'
  scope: string
  policy_version: string
  payload_json: Record<string, unknown> | null
  expires_at: string | null
  created_at: string
  finished_at: string | null
}

/** 更正可申请字段白名单（commands/data_rights.CORRECTABLE_FIELDS） */
export type CorrectableField = 'name' | 'gender' | 'birth' | 'death' | 'bio'

/** 认领争议：证据原文保留，决议走 operator break-glass */
export interface ClaimDispute {
  id: number
  profile_id: number
  raised_by_account_id: number
  evidence_json: Record<string, unknown>
  status: 'open' | 'resolved_claim' | 'resolved_reject' | 'withdrawn'
  resolution_note?: string | null
  created_at: string
  resolved_at: string | null
}

/** 全部披露类别（users.DISCLOSURE_KEYS；高敏感类任何层级不得自动开放） */
export const DISCLOSURE_CATEGORIES = [
  'avatar',
  'photos',
  'dates',
  'bio',
  'attachments',
  'health',
  'address',
  'school',
  'contact',
  'private_notes',
] as const

export type DisclosureCategory = (typeof DISCLOSURE_CATEGORIES)[number]
/** 高敏感类别：仅显式授权投影可见，UI 中恒为占位（接口后续任务提供） */
export const HIGH_RISK_DISCLOSURE_CATEGORIES: readonly DisclosureCategory[] = [
  'health',
  'address',
  'school',
  'contact',
  'private_notes',
]

export const DISCLOSURE_CATEGORY_LABELS: Record<DisclosureCategory, string> = {
  avatar: '头像',
  photos: '相册照片',
  dates: '生卒日期',
  bio: '简介',
  attachments: '链接附件',
  health: '健康信息',
  address: '住址',
  school: '学校',
  contact: '联系方式',
  private_notes: '私人描述',
}

/** 披露偏好合并矩阵（GET /users/{id}/disclosure；v2 Gap3） */
export interface SpaceDisclosure {
  space_id: number
  allowed: Record<DisclosureCategory, boolean>
}

export interface DisclosureMatrix {
  global: Record<DisclosureCategory, boolean>
  spaces: SpaceDisclosure[]
}

// ---- m3a 附件域 ----
export interface Attachment {
  id: number
  user_id: number
  type: 'image' | 'link' | 'location'
  title: string | null
  description: string | null
  url_or_path: string | null
  created_at: string
}
