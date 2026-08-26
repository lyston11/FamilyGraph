"""推荐资格矩阵（V2.4 PRD ST-5）：纯函数、确定性，LLM 不得改变 eligible/action 集。

输入为显式标量/集合（profile 确档、fact type/state、创建选择、披露同意、
现有共同成员资格/可申请 lineage/冷却），输出 eligible + 有序动作元组 +
机器可读原因。矩阵逐行合同：

- friend/colleague 等 SocialRelation 永不进入本矩阵（未知 fact_type 一律
  fail-closed 拒绝，AC-ST4）；
- 任一端 Profile 未 identity_confirmed 或事实非 confirmed → 不出卡（AC-ST4）；
- partner：双方确认且允许披露 → 仅共同 HouseholdSpace；绝不推荐 Lineage；
- spouse：共同 HouseholdSpace + 可分别申请对方指定 LineageSpace（不自动通过）；
- biological/adoptive/step parent-child、confirmed sibling：按创建选择
  （household/lineage/两者）出卡；guardian 默认 household（绝不 lineage）；
- 创建选择 no-space → 不出任何卡（不自动 membership，ST-5）。

动作词表：create_household（共同新建/加入 Household）与 request_lineage
（申请加入对方指定 LineageSpace）。发送申请永远发生在用户显式确认之后，
卡片本身不是发送动作。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.relationship_facts import PARENT_FACT_TYPES, SOURCE_FACT_STATES, SOURCE_FACT_TYPES

# ---- 动作词表 ----
ACTION_CREATE_HOUSEHOLD = "create_household"
ACTION_REQUEST_LINEAGE = "request_lineage"

# ---- 创建选择词表（§0.4 建档选择；choices 允许 {household,lineage} 组合=两者）----
CREATION_NO_SPACE = "no-space"
CREATION_HOUSEHOLD = "household"
CREATION_LINEAGE = "lineage"
CREATION_CHOICES = frozenset({CREATION_NO_SPACE, CREATION_HOUSEHOLD, CREATION_LINEAGE})

# ---- 不可出卡原因码（机器可读；文案由上层模板生成）----
REASON_OK = "ok"
REASON_UNKNOWN_FACT_TYPE = "unknown_fact_type"
REASON_UNKNOWN_FACT_STATE = "unknown_fact_state"
REASON_PROFILE_NOT_CONFIRMED = "profile_not_confirmed"
REASON_FACT_NOT_CONFIRMED = "fact_not_confirmed"
REASON_COOLDOWN_ACTIVE = "cooldown_active"
REASON_DISCLOSURE_NOT_ALLOWED = "disclosure_not_allowed"
REASON_CREATION_NO_SPACE = "creation_no_space"
REASON_ALREADY_CONNECTED = "already_connected"
REASON_NO_ELIGIBLE_ACTION = "no_eligible_action"

# 参与推荐的事实类型（SocialRelation 的 friend/colleague 不在其中）
_FAMILY_FACT_TYPES = frozenset(SOURCE_FACT_TYPES)
_GATED_BY_CREATION_CHOICES = frozenset(PARENT_FACT_TYPES) | {"direct_sibling"}


@dataclass(frozen=True)
class RecommendationInput:
    """一次配对判断的全部输入（由调用方从授权快照确定性推导）。"""

    fact_type: str
    fact_state: str
    subject_identity_confirmed: bool
    object_identity_confirmed: bool
    # 被创建者在本空间的创建选择；对称关系取双方并集；空集按 no-space 处理
    creation_choices: frozenset[str]
    mutual_disclosure_allowed: bool = False
    # 双方已在本 household 空间均为 active 成员（guest 不计，见 steward 组装）
    share_household_membership: bool = False
    # 对方指定 lineage 空间可申请：本空间为 lineage 且恰一端为 active 成员
    lineage_request_possible: bool = False
    in_cooldown: bool = False


@dataclass(frozen=True)
class RecommendationOutcome:
    """矩阵输出：eligible=False 时 actions 恒为空元组。"""

    eligible: bool
    actions: tuple[str, ...]
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"eligible": self.eligible, "actions": list(self.actions), "reason": self.reason}


def _ineligible(reason: str) -> RecommendationOutcome:
    return RecommendationOutcome(eligible=False, actions=(), reason=reason)


def evaluate_recommendation(inp: RecommendationInput) -> RecommendationOutcome:
    """逐行落实 ST-5 矩阵；纯函数，无 IO、无随机性。"""
    if inp.fact_type not in _FAMILY_FACT_TYPES:
        # SocialRelation（friend/colleague 等）与未知类型一律 fail-closed
        return _ineligible(REASON_UNKNOWN_FACT_TYPE)
    if inp.fact_state not in SOURCE_FACT_STATES:
        return _ineligible(REASON_UNKNOWN_FACT_STATE)
    if not (inp.subject_identity_confirmed and inp.object_identity_confirmed):
        return _ineligible(REASON_PROFILE_NOT_CONFIRMED)
    if inp.fact_state != "confirmed":
        return _ineligible(REASON_FACT_NOT_CONFIRMED)
    if inp.in_cooldown:
        return _ineligible(REASON_COOLDOWN_ACTIVE)

    choices = frozenset(c for c in inp.creation_choices if c in CREATION_CHOICES) - {
        CREATION_NO_SPACE
    }

    actions: list[str] = []
    if inp.fact_type == "partner":
        # 仅共同 HouseholdSpace；未披露不出卡；绝不 lineage
        if not inp.mutual_disclosure_allowed:
            return _ineligible(REASON_DISCLOSURE_NOT_ALLOWED)
        if not inp.share_household_membership:
            actions.append(ACTION_CREATE_HOUSEHOLD)
    elif inp.fact_type == "spouse":
        if not inp.share_household_membership:
            actions.append(ACTION_CREATE_HOUSEHOLD)
        if inp.lineage_request_possible:
            actions.append(ACTION_REQUEST_LINEAGE)
    elif inp.fact_type == "guardian":
        # 默认 household；即使选择含 lineage 也只出 household 卡
        if not choices:
            return _ineligible(REASON_CREATION_NO_SPACE)
        if CREATION_HOUSEHOLD in choices and not inp.share_household_membership:
            actions.append(ACTION_CREATE_HOUSEHOLD)
    elif inp.fact_type in _GATED_BY_CREATION_CHOICES:
        if not choices:
            return _ineligible(REASON_CREATION_NO_SPACE)
        if CREATION_HOUSEHOLD in choices and not inp.share_household_membership:
            actions.append(ACTION_CREATE_HOUSEHOLD)
        if CREATION_LINEAGE in choices and inp.lineage_request_possible:
            actions.append(ACTION_REQUEST_LINEAGE)

    if not actions:
        return _ineligible(REASON_ALREADY_CONNECTED)
    return RecommendationOutcome(eligible=True, actions=tuple(actions), reason=REASON_OK)
