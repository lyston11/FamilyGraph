/**
 * Kinship（关系智能 V2.3）域类型：与 backend/app/schemas/kinship.py
 * 及 /api/kinship/parse 合同一一对应（人工同步）。
 *
 * 信任边界（任务 PRD KI-3/KI-4）：前端只消费后端算法结果做展示，
 * 不自行推断概念/路径真值；个人称谓纠正只写 personal term，不改结构关系。
 */

/** 四级来源：personal > space > locale > system；structural = 无词条时的结构默认称谓 */
export type TermSourceLevel = 'personal' | 'space' | 'locale' | 'system' | 'structural'

/** 自由文本解析等级（KI-3）：只有 determined 允许直接更新展示层 */
export type ResolutionClass = 'determined' | 'supported' | 'ambiguous' | 'conflicting'

/** 路径单步 JSON 形状（services/relationship_resolver.py PathStep.to_json） */
export interface PathStep {
  from: number
  to: number
  edge_type: string
  subtype: string | null
  direction: string
  fact_id: number
}

/** 词条在当前语境的实时生效解析 */
export interface ResolvedTerm {
  term: string | null
  source_level: TermSourceLevel | null
  entry_id: number | null
}

/** GET/PUT /kinship/terms/my 行 */
export interface MyTerm {
  entry_id: number
  concept_code: string
  term: string
  revision: number
  updated_at: string
  /** 仅 GET 带 space_id 时返回 */
  resolved?: ResolvedTerm | null
}

/** resolve 附带的事实状态摘要 */
export interface FactState {
  confirmed: number
  proposed: number
  disputed: number
  revoked: number
  evidence_fact_ids: number[]
}

/** 替代路径投影 */
export interface AltPath {
  path: PathStep[]
  description: string | null
  concept_code: string | null
  term: string | null
  term_source_level?: TermSourceLevel | null
  term_entry_id?: number | null
}

/** GET /kinship/resolve 合成视图 */
export interface KinshipResolve {
  found: boolean
  viewer_user_id: number
  target_user_id: number
  space_id: number
  path_class: string
  concept_code: string | null
  explanation_structural: string | null
  term: string | null
  term_source_level: TermSourceLevel | null
  term_entry_id: number | null
  main_path: PathStep[]
  alt_paths: AltPath[]
  fact_state: FactState
  cache_hit: boolean
  algorithm_version: string
}

/** POST /kinship/usages 的空间晋升摘要 */
export interface SpacePromotion {
  promoted: boolean
  demoted: boolean
  eligible_accounts: number
}

/** POST /kinship/usages 响应 */
export interface UsageCreated {
  usage_id: number
  entry_id: number
  /** false = 幂等去重（同账号同词条同空间只计一次） */
  created: boolean
  promotion: SpacePromotion
}

/** parse 候选概念（ambiguous/conflicting 时可能缺失） */
export interface ParseCandidate {
  concept_code: string
  term: string
  term_source_level: TermSourceLevel
}

/** parse 的图证据摘要 */
export interface GraphProof {
  found: boolean
  explanation_structural: string | null
}

/** parse 生成的原子事实提案（supported 级别展示用） */
export interface ParseProposal {
  kind: string
  fact_type: string
  summary: string
}

/** POST /kinship/parse 结果（原文另存 raw_relation_inputs，任何产物不覆盖） */
export interface ParseResult {
  raw_text_id: number
  normalized_text: string
  resolution_class: ResolutionClass
  candidate: ParseCandidate | null
  graph_proof: GraphProof
  proposals: ParseProposal[]
  conflicts: string[]
  clarifying_question: string | null
  evidence_morphemes: string[]
}
