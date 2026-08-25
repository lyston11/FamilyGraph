/**
 * 后端 API 类型定义：与 backend/app/schemas/auth.py 字段一一对应（人工同步）。
 * 错误结构见 spec/backend/error-handling.md。
 */

export interface UserOut {
  id: number
  name: string
  is_admin: boolean
  pin_must_change: boolean
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
} as const

export type ErrorCode = (typeof ERROR_CODES)[keyof typeof ERROR_CODES]

// ---- m1a 成员档案域 ----

export type GenderType = 'm' | 'f' | 'unknown'
export type CalType = 'solar' | 'lunar' | 'none'
export type PrivacyMode = 'perpetual' | 'handover'
export type ClaimStatus = 'managed' | 'claimed'

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
  visibility: 'full' | 'summary'
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
  created_at: string
  pending_count: number
  member_count: number
}

export interface SpaceMemberInfo {
  id: number
  space_id: number
  user_id: number
  added_by: number | null
  role: 'owner' | 'member'
  status: 'pending' | 'active' | 'rejected' | 'withdrawn' | 'removed'
  updated_at: string
}
