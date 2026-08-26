"""ActionCard 生命周期 API 的请求/响应模型（V2.4 Block S2）。

与前端 types/actionCard.ts 逐字段对齐（人工同步）：
- CardOut 的字段名与形状与 ActionCard 接口一致；kind 取后端存储的
  CARD_KINDS（household_link | lineage_request，CHECK 约束兜底），前端在
  展示侧按 kind 映射文案，不在此处做词表转换；
- evidence 只含 fact_ids / path_summary / evidence_version，绝不携带 masked
  原值（PRD ST-4 安全红线）；S1 不落 path_summary，序列化为 null；
- proposed_action 把后端存储的 proposed_action_json（{"action": <verb>, ...}）
  归一为 {type: <action>, params: <其余>}，与前端 ProposedAction 形状对齐。

输入模型一律 extra="forbid"（fail-closed，与 schemas/kinship.py 同一纪律）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.steward import CARD_KINDS, CARD_STATES


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionCardUserRef(BaseModel):
    id: int
    name: str


class ActionCardEvidence(BaseModel):
    fact_ids: list[int]
    path_summary: str | None = None
    evidence_version: int


class ProposedActionOut(BaseModel):
    type: str
    params: dict[str, Any]


class CardOut(BaseModel):
    """GET /api/action-cards 条目（前端 ActionCard 接口对齐）。"""

    id: int
    kind: str
    space_id: int
    subject_user: ActionCardUserRef
    object_user: ActionCardUserRef | None
    reason_text: str
    evidence: ActionCardEvidence
    proposed_action: ProposedActionOut
    privacy_effect: str
    state: str
    expires_at: str | None
    created_at: str
    revision: int


class CardStateOut(BaseModel):
    """POST view/dismiss/accept 响应（compare-and-set revision 回填）。"""

    id: int
    state: str
    revision: int


class ExecuteResponse(BaseModel):
    """POST execute 成功响应（前端 ActionCardExecuteResponse 对齐）。"""

    id: int
    state: str


class ExecuteRequest(_Strict):
    """执行载荷：字段随卡种取最小集合，后续扩展仍 forbid 额外字段。

    - create_household（共同 HouseholdSpace）：可选 name 覆盖默认空间名；
    - request_lineage（申请加入对方家族空间）：可选 target_space_id，但必须与卡片
      已接受快照中的目标空间一致（缺省由快照推导）。
    """

    name: str | None = None
    target_space_id: int | None = Field(default=None, ge=1)


# ---- 卡片状态/种类词表（仅供路由层做 422 早校验，DB CHECK 为最终权威）----

_VALID_STATES = frozenset(CARD_STATES)
_VALID_KINDS = frozenset(CARD_KINDS)


def is_valid_state(value: str) -> bool:
    return value in _VALID_STATES


def is_valid_kind(value: str) -> bool:
    return value in _VALID_KINDS
