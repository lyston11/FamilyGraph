/**
 * 后端 API 类型定义：与 backend/app/schemas/auth.py 字段一一对应（人工同步）。
 * 错误结构见 spec/backend/error-handling.md。
 */

export interface UserOut {
  id: number
  name: string
  /**
   * 09-04：家庭认证响应只描述 family_user 主体；系统管理员的身份模型
   * 完全不在家庭前端出现（backend/app/schemas/auth.py 同步注释）。
   */
  principal_type?: 'family_user'
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
  /**
   * REGISTRATION_ENABLED 运行时投影（09-05 决策 2）：前端据此显隐注册入口，
   * 不使用任何 VITE_ 构建期变量（运行时信号优于构建期 env）。
   */
  registration_enabled: boolean
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
  /** 闰月标记，恒描述农历那一侧：cal_type=lunar 指 date，solar 指 mirror_date */
  is_leap_month?: boolean
  /** 另一历的 ISO 镜像，服务端写入，请求携带的值会被覆写 */
  mirror_date?: string | null
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

/**
 * 披露开关载荷（09-05 高敏感策略放开）：基础五类必填（整体替换语义），
 * 高敏感五类选填——省略 = 本次不修改该类别（后端 exclude_unset 语义）。
 */
export interface DisclosureFlags extends ClanDisclosure {
  health?: boolean
  address?: boolean
  school?: boolean
  contact?: boolean
  private_notes?: boolean
}

export type SpaceKind = 'household' | 'lineage'
/**
 * 空间角色：产品层只有一个「空间管理员」（`space_admin`）和普通成员（`member`）。
 *
 * 旧 `owner` 已在迁移 0022 归一化为 `space_admin`，后端 `SpaceMemberOut` 不再
 * 输出它，因此前端不保留该字面量。
 */
export type SpaceRole = 'space_admin' | 'member'

/** 当前主体对该档案的可用操作（resolve_relation 投影） */
export interface MemberPermissions {
  edit: boolean
  delete: boolean
}

export interface Member {
  id: number
  name: string
  /** platform_operator 派生（v2 兼容键，见 UserOut.is_admin 注释）。 */
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
  /** F-1 必填：与创建者的关系（AD-4 新建例外 → 直接 active） */
  relation_dir_class: DirClass
  relation_label?: string | null
  relation_text?: string | null
  /**
   * 重复建档消歧（architecture.md §0.9）：仅在收到 PERSON_DUPLICATE_AMBIGUOUS
   * 后由用户明确「这是另一个人」时携带。强匹配 PERSON_DUPLICATE_IN_SPACE 不受其
   * 影响，始终拒绝。可复用同一 Idempotency-Key 重放（该标记不进 request_hash）。
   */
  allow_duplicate_person?: boolean
}

export interface MemberUpdatePayload {
  name?: string
  gender?: GenderType
  birth?: StructuredDate | null
  death?: StructuredDate | null
  bio?: string | null
}

/** 建档响应：PIN 明文仅此一次；幂等重放时 pin=null 且 replayed=true */
export interface MemberCreateResponse {
  user: Member
  pin: string | null
  replayed: boolean
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

/** 字段级遮罩哨兵（backend visibility.MASKED）；判别联合的唯一入口 */
export interface MaskedValue {
  __masked__: true
}

/** 受可见性控制的字段：要么明文值，要么遮罩哨兵，不存在 undefined 中间态 */
export type Maskable<T> = T | MaskedValue

export function isMasked(value: unknown): value is MaskedValue {
  return typeof value === 'object' && value !== null && '__masked__' in value
}

/**
 * PersonalFamilyView 节点可展示投影（backend visibility.payload_from_decision）。
 * BASELINE_FIELDS（id/name）在任何非 none 层级恒明文，缺失即由 API 层丢弃该节点；
 * CONTENT_FIELDS 随层级与披露收紧为 MaskedValue。
 */
export interface PersonalFamilyViewDisplay {
  id: number
  name: string
  gender: Maskable<GenderType>
  birth: Maskable<StructuredDate | null>
  death: Maskable<StructuredDate | null>
  bio: Maskable<string | null>
  avatar_path: Maskable<string | null>
  privacy_mode: Maskable<PrivacyMode>
  claim_status: Maskable<ClaimStatus>
}

export interface PersonalFamilyViewNode {
  user_id: number
  display: PersonalFamilyViewDisplay
  /** none 节点不会出现在载荷中；lineage_summary 只读且不可展开 */
  visibility_level: Exclude<VisibilityLevel, 'none'>
  inclusion_reason_code: string
}

/** 关系路径单步（backend relationship_resolver.PathStep.to_json） */
export interface PersonalFamilyViewPathStep {
  from: number
  to: number
  edge_type: string
  subtype: string | null
  direction: string
  fact_id: number
}

export interface PersonalFamilyViewEdge {
  from_user_id: number
  to_user_id: number
  edge_kind: string
  path: PersonalFamilyViewPathStep[]
  alternative_paths: PersonalFamilyViewPathStep[][]
  path_class: string
  concept_code: string | null
  term: string | null
  inclusion_reason_code: string
}

export type PersonalFamilyViewStatus =
  | 'never_computed'
  | 'queued'
  | 'running'
  | 'current'
  | 'stale'
  | 'failed'

export interface FamilyRecommendationItem {
  category: string
  target_user_id: number
  display: Record<string, unknown>
  reason_code: string
  term?: string | null
  concept_code?: string | null
  path_class?: string | null
  path_summary?: string[] | null
  proposed_fact_type?: string | null
}

export interface FamilyRecommendationsData {
  space_id: number
  view_status: string
  view_version: number
  generated_from_view_version: number
  items: FamilyRecommendationItem[]
  truncated: boolean
}

export interface PersonalFamilyViewData {
  space_id: number
  status: PersonalFamilyViewStatus
  view_version: number
  computed_at: string | null
  nodes: PersonalFamilyViewNode[]
  edges: PersonalFamilyViewEdge[]
  truncated: boolean
  next_cursor: string | null
  stale_reason: string | null
}

/** 带 ETag 的安全快照：304 时复用上一份 data，不重建对象 */
export interface PersonalFamilyViewSnapshot {
  data: PersonalFamilyViewData
  etag: string | null
}

export interface PersonalFamilyBridge {
  id: number
  lineage_space_a_id: number
  lineage_space_b_id: number
  anchor_a_user_id: number
  anchor_b_user_id: number
  status: 'pending' | 'active' | 'revoked' | 'expired' | 'rejected'
  revision: number
  expires_at: string | null
  created_at: string
  updated_at: string
}

// ---- m1c 家庭空间域（与后端 schemas/space.py 一一对应） ----

export interface FamilySpace {
  id: number
  name: string
  owner_id: number
  kind: 'household' | 'lineage'
  /**
   * household → 所属 lineage 空间的显式配对（PUT /spaces/{id}/lineage-link）。
   * 可选兼容旧载荷：缺省/undefined/null 都按「未配对」处理，前端回退 owner
   * 唯一匹配推断（spaces store 的 lineageForSpace / householdForLineage）。
   */
  lineage_space_id?: number | null
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

// ---- 空间管理者申请（任务 08-30-space-manager-approval；与后端 schemas/space.py 对应） ----

export type ManagerRequestKind = 'space_admin'
export type ManagerApplicationStatus = 'pending' | 'approved' | 'rejected'

export type ManagerTransferConsentStatus = 'pending' | 'accepted' | 'rejected' | 'expired'

/** 空间管理员申请：指向申请人所在的目标 lineage 家族空间 */
export interface SpaceManagerApplication {
  id: number
  applicant_user_id: number
  /** 队列/本人列表附带的名字投影 */
  applicant_name?: string | null
  space_id: number
  space_name?: string | null
  space_kind?: SpaceKind | null
  /** 目标空间现任唯一管理员（交接对象） */
  current_manager_user_id?: number | null
  current_manager_name?: string | null
  /** 原管理员同意工单；approve 首阶段由服务端创建 */
  transfer_consent_id?: number | null
  transfer_consent_status?: ManagerTransferConsentStatus | null
  request_kind: ManagerRequestKind
  status: ManagerApplicationStatus
  /** 平台备注（reject 必填，approve 可选） */
  decision_note: string | null
  created_at: string
  decided_at: string | null
  /** 系统管理员裁决人（家庭用户裁决路径不写该字段） */
  system_admin_decided_by?: number | null
}

/** 可申请管理员的目标空间；资格由服务端裁定 */
export interface EligibleManagerTarget {
  space_id: number
  space_name: string
  space_kind: 'lineage'
  current_manager_user_id: number | null
  current_manager_name: string | null
  has_pending_application: boolean
}

/** 原管理员交接同意工单：自带目标空间与申请人标识 */
export interface ManagerTransferConsent {
  id: number
  application_id: number
  space_id: number
  space_name?: string | null
  space_kind?: SpaceKind | null
  applicant_user_id?: number | null
  applicant_name?: string | null
  current_manager_user_id: number
  status: ManagerTransferConsentStatus
  requested_at: string
  responded_at: string | null
  response_reason: string | null
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
/** 高敏感类别：默认关闭，本人可显式开启（需二次确认）；未成年人档案始终最小披露 */
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

// ---- 09-01 PersonalFamilyView 前端 Phase 1：HouseholdCard / Notification / SpaceStats ----
// 服务端合同已落地（09-11 复核：GET /api/household-card、/api/stats?space_id、
// /api/notifications* 均已挂载）；decoder 在 api/ 层保持 fail-closed。

/**
 * HouseholdCard 成员条目（design.md §4.2）：仅服务端确认的 active household 成员。
 * display 与 PersonalFamilyViewDisplay 同一后端投影口径（visibility.payload_from_decision），
 * 字段级遮罩复用 Maskable 哨兵；`none` 成员不进入投影。
 */
export interface HouseholdCardMember {
  user_id: number
  display: PersonalFamilyViewDisplay
  /** 服务端授权的家庭内标签（如「管理员」「成员」）；前端不自行推导 */
  household_label: string
  /** 该成员的字段级可见性层级说明 */
  visibility_level: Exclude<VisibilityLevel, 'none'>
}

/**
 * 空状态/允许动作提示：由服务端按当前认证主体授权给出
 * （空成员时的创建家庭/邀请家人入口），前端不本地判定资格。
 */
export interface HouseholdCardActions {
  can_invite_members: boolean
  can_create_household: boolean
  /** 服务端空状态提示文案；无提示为 null */
  empty_state_hint: string | null
}

/**
 * HouseholdCard 投影（design.md §4.2）：household 空间的服务端授权大卡片数据。
 * 不包含 lineage 节点数组、隐藏成员数量或其他空间资料；
 * 成员不经 `/users`/旧 members 列表拼装，也不能从 PersonalFamilyView 路径推导。
 */
export interface HouseholdCardData {
  space_id: number
  space_kind: 'household'
  space_name: string
  view_version: number
  computed_at: string | null
  /** viewer 本人的授权 profile display */
  viewer: PersonalFamilyViewDisplay
  /** confirmed active household members；pending/removed/普通亲属不入列 */
  members: HouseholdCardMember[]
  allowed_actions: HouseholdCardActions
}

/** 带 ETag 的安全快照：304 时复用上一份 data，不重建对象（同 PersonalFamilyViewSnapshot） */
export interface HouseholdCardSnapshot {
  data: HouseholdCardData
  etag: string | null
}

/** 通知种类（决定 ActionCard 引用与跳转上下文） */
export type NotificationKind =
  | 'action_card'
  | 'space_membership'
  | 'bridge'
  | 'relation'
  | 'steward_suggestion'

/**
 * 通知引用的领域对象状态：跨领域 FSM 的最小集合。
 * 与通知已读状态（read_at）、ActionCard revision 三者严格独立：
 * 已读操作不得变更领域状态，ActionCard 处理状态以 actionCards store 为准。
 */
export type NotificationDomainStatus =
  | 'pending'
  | 'active'
  | 'accepted'
  | 'rejected'
  | 'cancelled'
  | 'revoked'
  | 'expired'
  | 'withdrawn'
  | 'removed'
  | 'done'

/** ActionCard 引用：仅卡号与 revision；卡片状态/操作一律走 actionCards store */
export interface NotificationActionCardRef {
  card_id: number
  revision: number
}

/** Steward 建议引用：仅建议号；建议详情/操作一律走 stewardSuggestions store */
export interface NotificationSuggestionRef {
  suggestion_id: number
}

/** 通知安全载荷：服务端脱敏后的最小展示字段，不含私人记忆/隐藏节点数据 */
export interface NotificationPayload {
  title: string
  /** 脱敏摘要；masked 哨兵表示当前主体不可见，null 表示不适用 */
  summary: Maskable<string> | null
  /** 相关人物名字投影；masked 表示不可见 */
  actor_name: Maskable<string> | null
  /** 相关空间名（跨空间 bridge 通知可能被遮蔽） */
  space_name: Maskable<string> | null
}

export interface NotificationItem {
  id: number
  space_id: number
  kind: NotificationKind
  payload: NotificationPayload
  /** 通知引用的领域对象状态；已读操作不得变更该状态 */
  domain_status: NotificationDomainStatus
  /** ActionCard 引用；kind='action_card' 时必须存在 */
  action_card: NotificationActionCardRef | null
  /** Steward 建议引用；kind='steward_suggestion' 时必须存在 */
  suggestion: NotificationSuggestionRef | null
  created_at: string
  /** 已读时间；null=未读。已读与领域状态/ActionCard revision 严格分离 */
  read_at: string | null
}

/** 通知列表载荷：按账号 + space_id 过滤（design.md §4.4） */
export interface NotificationsPage {
  space_id: number
  items: NotificationItem[]
  /** 服务端统计的未读数，前端不从 items 推导 */
  unread_count: number
}

/** 带 ETag 的安全快照：304 时复用上一份 data */
export interface NotificationsSnapshot {
  data: NotificationsPage
  etag: string | null
}

/** POST /notifications/{id}/read 响应：仅已读确认，不含任何领域状态变更 */
export interface NotificationReadResult {
  id: number
  read_at: string
}

/** POST /notifications/read-all 响应 */
export interface NotificationReadAllResult {
  space_id: number
  marked_count: number
}

// ---- 09-11 Steward 建议审核（candidate-review；/api/steward-suggestions*）----
// 建议是服务端受控投影：kind/state/allowed_actions 全部由服务端给出，
// 前端绝不本地推导授权，也绝不展示 raw model payload（后端不下发）。

export type SuggestionKind =
  | 'relation_proposal'
  | 'term_preference'
  | 'identity_duplicate'
  | 'missing_information'

export type SuggestionOrigin = 'deterministic' | 'model'

export type SuggestionState = 'proposed' | 'submitted' | 'resolved' | 'dismissed' | 'expired'

export type SuggestionAction = 'open_details' | 'submit' | 'dismiss'

/** 证据摘要：仅计数与白名单 fact id/revision（不含模型自由文本） */
export interface SuggestionEvidenceSummary {
  fact_count: number
  facts: Array<{ fact_id: number; revision: number }>
}

export interface SuggestionItem {
  id: number
  space_id: number
  kind: SuggestionKind
  origin: SuggestionOrigin
  state: SuggestionState
  revision: number
  evidence_hash: string
  subject_user_id: number
  object_user_id: number | null
  subject_name: string | null
  object_name: string | null
  /** 结构化建议值（relation 的 fact_type / finding 的 code 等；封闭字段） */
  value: Record<string, unknown>
  evidence_summary: SuggestionEvidenceSummary
  allowed_actions: SuggestionAction[]
  expires_at: string | null
  created_at: string
}

export interface SuggestionsPage {
  space_id: number
  items: SuggestionItem[]
  next_cursor: number | null
}

export interface SuggestionDismissResult {
  id: number
  state: SuggestionState
  revision: number
  dismissed_at: string | null
  cooldown_until: string | null
}

export interface SuggestionLinkedProposal {
  source_fact_id: number
  revision: number
  state: string
  fact_type: string
}

export interface SuggestionLinkedPreference {
  term_id: number
  concept_code: string
  term: string
}

/** POST /steward-suggestions/{id}/submit 202 响应（relation_proposal） */
export interface SuggestionSubmitProposalResult {
  suggestion: SuggestionItem
  linked_proposal: SuggestionLinkedProposal
  pending_confirmations: Array<{ account_id: number }>
}

/** POST /steward-suggestions/{id}/submit 200 响应（term_preference） */
export interface SuggestionSubmitPreferenceResult {
  suggestion: SuggestionItem
  linked_preference: SuggestionLinkedPreference
}

/** 空间化统计的视图状态机：与 PersonalFamilyView 一致 */
export type SpaceStatsStatus = PersonalFamilyViewStatus

/** 关系分布切片：dir_class 维度的服务端授权计数 */
export interface SpaceStatsRelationSlice {
  dir_class: DirClass
  count: number
}

/**
 * 空间化统计（design.md §4.4）：按 space_id 的服务端授权聚合。
 * 隐藏对象/未授权分支不计入，前端不得从 PersonalFamilyView 节点数组推导统计。
 */
export interface SpaceStatsData {
  space_id: number
  space_kind: SpaceKind
  status: SpaceStatsStatus
  view_version: number | null
  /** 授权范围内的聚合 */
  node_count: number
  edge_count: number
  member_count: number
  /** 关系分布（仅 dir_class 维度） */
  relation_distribution: SpaceStatsRelationSlice[]
  /** 待确认计数：待处理 ActionCard 与空间成员申请（服务端口径） */
  pending_action_cards: number
  pending_memberships: number
  computed_at: string | null
  stale_reason: string | null
}

/** 带 ETag 的安全快照：304 时复用上一份 data */
export interface SpaceStatsSnapshot {
  data: SpaceStatsData
  etag: string | null
}

// ---- 09-05 注册与邀请码域（与 backend/app/schemas/invite_code.py 一一对应） ----

/** 码类型：家庭空间码 / 家族空间码 / 陌生人拉新码（决策 7） */
export type InviteCodeKindFull = 'household' | 'lineage' | 'stranger'

/** 我的码投影：创建者可见明文码（分享渲染为 …/register?code=XXX，决策 12/14） */
export interface InviteCode {
  id: number
  code: string
  kind: InviteCodeKindFull
  /** household/lineage 必有；stranger 恒 null */
  space_id: number | null
  /** 列表端点附带的空间名投影 */
  space_name: string | null
  /** 陌生人码可设上限；null=不限次；家庭/家族码恒 1 */
  max_uses: number | null
  used_count: number
  expires_at: string
  revoked_at: string | null
  created_at: string
}

export interface CreateInviteCodePayload {
  kind: InviteCodeKindFull
  /** household/lineage 必填（选择所在空间）；stranger 不带 */
  space_id?: number | null
  /** 陌生人码使用上限；null/缺省=不限次 */
  max_uses?: number | null
  /** 有效期天数；缺省=7（决策 11） */
  ttl_days?: number | null
}

// ---- 09-05 并流绑定域（与 backend/app/schemas/binding.py 一一对应；决策 16） ----

export type BindingStatus = 'pending' | 'confirmed' | 'rejected' | 'cancelled'

/** 被绑定人视角投影：仅「这是我」判断所需最小字段（发起人名 + 建档人物名） */
export interface Binding {
  id: number
  initiator_name: string | null
  person_name: string | null
  status: BindingStatus
  created_at: string
  resolved_at: string | null
}
