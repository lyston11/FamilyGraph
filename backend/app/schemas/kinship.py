"""Kinship TermRegistry 浏览器 API 的请求/响应模型（V2.3 Block E3）。

输入模型一律 extra="forbid"（fail-closed，与 schemas/agent.py 同一纪律）。
path step 沿用 DerivedFact 缓存的 JSON 形状（dict 直传，合同见
services/relationship_resolver.py PathStep.to_json）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- 个人称谓 ----


class PersonalTermPutRequest(_Strict):
    space_id: int = Field(ge=1)
    # 空/超长/空白归一由 services.terms 校验（统一 TERM_INVALID / CONCEPT_CODE_INVALID）
    concept_code: str
    term: str


class ResolvedTermOut(BaseModel):
    term: str | None
    source_level: Literal["personal", "space", "locale", "system", "structural"] | None
    entry_id: int | None


class MyTermOut(BaseModel):
    entry_id: int
    concept_code: str
    term: str
    revision: int
    updated_at: datetime
    # 仅 GET 带 space_id 时返回：该空间语境的实时生效解析
    resolved: ResolvedTermOut | None = None


# ---- resolve 合成视图 ----


class FactStateOut(BaseModel):
    confirmed: int
    proposed: int
    disputed: int
    revoked: int
    evidence_fact_ids: list[int]


class AltPathOut(BaseModel):
    path: list[dict[str, Any]]
    description: str | None
    concept_code: str | None
    term: str | None
    term_source_level: Literal["personal", "space", "locale", "system", "structural"] | None = None
    term_entry_id: int | None = None


class KinshipResolveOut(BaseModel):
    found: bool
    viewer_user_id: int
    target_user_id: int
    space_id: int
    path_class: str
    concept_code: str | None
    explanation_structural: str | None
    term: str | None
    term_source_level: Literal["personal", "space", "locale", "system", "structural"] | None
    term_entry_id: int | None
    main_path: list[dict[str, Any]]
    alt_paths: list[AltPathOut]
    fact_state: FactStateOut
    cache_hit: bool
    algorithm_version: str


# ---- 自由文本关系解析（V2.3 Block E4a，KI-3）----


class KinshipParseRequest(_Strict):
    space_id: int = Field(ge=1)
    # 1..80 字；原文另存 raw_relation_inputs（append-only），此处只是解析载荷
    text: str = Field(min_length=1, max_length=80)


class ParseCandidateOut(BaseModel):
    concept_code: str | None
    term: str | None
    term_source_level: Literal["personal", "space", "locale", "system", "structural"] | None


class GraphProofOut(BaseModel):
    found: bool
    explanation_structural: str | None


class SourceFactProposalOut(_Strict):
    kind: Literal["source_fact"]
    # SOURCE_FACT_TYPES 词汇（services/source_facts.py）；写入仍需用户显式确认
    fact_type: str
    summary: str


class ParseResultOut(BaseModel):
    """四级 resolution 结果合同（浏览器 parse API 与 Agent 工具共用形状）。

    proposals 仅在 supported/conflicting（已解析出合法候选码）时非空；
    ambiguous 追问恰好一个问题；conflicting 另附 conflicts 冲突列表。
    """

    raw_text_id: int
    normalized_text: str
    resolution_class: Literal["determined", "supported", "ambiguous", "conflicting"]
    candidate: ParseCandidateOut
    graph_proof: GraphProofOut
    proposals: list[SourceFactProposalOut]
    conflicts: list[str]
    clarifying_question: str | None
    evidence_morphemes: list[str]


# ---- 使用证据（两人晋升输入）----


class UsagePostRequest(_Strict):
    space_id: int = Field(ge=1)
    concept_code: str
    term: str
    source_event: Literal["assistant_query", "manual_select"]


class UsageCreatedOut(BaseModel):
    usage_id: int
    entry_id: int
    created: bool  # false = 幂等去重（同账号同词条同空间只计一次）
    promotion: SpacePromotionOut


class SpacePromotionOut(BaseModel):
    promoted: bool
    demoted: bool
    eligible_accounts: int
