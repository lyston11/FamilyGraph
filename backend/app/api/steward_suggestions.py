"""Steward 建议审核 API（任务 09-11-steward-candidate-review；前缀 /api/steward-suggestions）。

合同（design.md）：
- 列表只返回安全显示字段 + kind/state/revision + 证据摘要 + allowed_actions，
  绝不返回 raw model payload；只显示当前 active 成员有权处理且证据端点对其
  可见的建议；分页默认 20、最大 100（keyset cursor）。
- dismiss：expected_revision CAS、幂等、按收件人冷却（冷却只作用于同一证据
  版本）；submitted/resolved 终态 409、过期 410、未知/不可见统一 404。
- submit：expected_revision + evidence_hash + 显式 confirm=true + Idempotency-Key；
  动作/对象完全由服务端建议决定。relation_proposal 返回 202（只生成提案，
  绝不显示为已确认）；term_preference 仅本人可提交（200，resolved）；
  identity_duplicate/missing_information v1 无 submit（422）。证据变化 409 /
  过期 410 均无正式写入；同 Idempotency-Key 重试返回同一关联对象。
- 确认/拒绝关系提案走 /api/relationship-proposals/{fact_id}/confirm|reject：
  仅有权当事人（端点本人或合法代管人）可操作，路由绝不直接调用
  ``transition_source_fact`` 跳过确认资格。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app import config
from app.api.deps import get_db, require_authenticated_user
from app.commands.context import ActorContext
from app.commands.relationship_proposals import (
    confirm_relationship_proposal,
    reject_relationship_proposal,
)
from app.errors import (
    ACTION_CARD_FLAG_DISABLED,
    VALIDATION_ERROR,
    raise_api_error,
)
from app.models.account import Account
from app.models.user import User
from app.services import steward_suggestions

router = APIRouter(tags=["steward-suggestions"])

_CONTRACT_VERSION = "steward-suggestions-v1"


class SuggestionItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    space_id: int
    kind: str
    origin: str
    state: str
    revision: int
    evidence_hash: str
    subject_user_id: int
    object_user_id: int | None
    subject_name: str | None
    object_name: str | None
    subject_display: dict[str, Any] | None = None
    object_display: dict[str, Any] | None = None
    presentation: dict[str, Any] | None = None
    source_state: str | None = None
    recipient_state: str | None = None
    value: dict[str, Any]
    evidence_summary: dict[str, Any]
    allowed_actions: list[str]
    expires_at: Any
    created_at: Any


class SuggestionsPageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: int
    items: list[SuggestionItemOut]
    next_cursor: int | None


class SuggestionDismissIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


class SuggestionDismissOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    state: str
    revision: int
    dismissed_at: Any
    cooldown_until: Any


class SuggestionSubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    evidence_hash: str = Field(min_length=16, max_length=64)
    confirm: bool


class ProposalConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


class ProposalOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_fact_id: int
    revision: int
    state: str
    fact_type: str


def _gate() -> None:
    if not config.STEWARD_ENABLED:
        raise_api_error(503, ACTION_CARD_FLAG_DISABLED, "Steward 功能未启用")


def _actor(identity: tuple[User, Account], request_ip: str | None) -> ActorContext:
    user, account = identity
    return ActorContext(
        user_id=user.id, account_id=account.id, account_status=account.status, ip=request_ip
    )


@router.get("/steward-suggestions", response_model=SuggestionsPageOut)
def list_suggestions(
    space_id: int,
    cursor: int | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SuggestionsPageOut:
    _gate()
    _user, account = identity
    payload = steward_suggestions.list_suggestions_page(
        session, account=account, space_id=space_id, cursor=cursor, limit=limit
    )
    return SuggestionsPageOut.model_validate(payload)


@router.get("/steward-suggestions/{suggestion_id}", response_model=SuggestionItemOut)
def get_suggestion_detail(
    suggestion_id: int,
    space_id: int = Query(...),
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SuggestionItemOut:
    """按 ID 详情（A-R5）：旧通知不依赖首页缓存；与列表同授权/状态/序列化。"""
    _gate()
    _user, account = identity
    payload = steward_suggestions.get_suggestion_detail(
        session, account=account, space_id=space_id, suggestion_id=suggestion_id
    )
    return SuggestionItemOut.model_validate(payload)


@router.post("/steward-suggestions/{suggestion_id}/dismiss", response_model=SuggestionDismissOut)
def dismiss_suggestion(
    suggestion_id: int,
    request: SuggestionDismissIn,
    space_id: int = Query(...),
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SuggestionDismissOut:
    _gate()
    _user, account = identity
    result = steward_suggestions.dismiss_suggestion(
        session,
        account=account,
        space_id=space_id,
        suggestion_id=suggestion_id,
        expected_revision=request.expected_revision,
    )
    return SuggestionDismissOut.model_validate(result)


@router.post("/steward-suggestions/{suggestion_id}/submit")
def submit_suggestion(
    suggestion_id: int,
    request: SuggestionSubmitIn,
    space_id: int = Query(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> Any:
    _gate()
    _user, account = identity
    if idempotency_key is None or not idempotency_key.strip():
        raise_api_error(422, VALIDATION_ERROR, "缺少 Idempotency-Key 请求头")
    status_code, payload = steward_suggestions.submit_suggestion(
        session,
        _actor(identity, None),
        account=account,
        space_id=space_id,
        suggestion_id=suggestion_id,
        expected_revision=request.expected_revision,
        evidence_hash=request.evidence_hash,
        confirm=request.confirm,
        idempotency_key=idempotency_key,
    )
    # 服务层返回的 payload 含 datetime；JSONResponse 原样 json.dumps 会失败。
    # 经 jsonable_encoder 归一（datetime→ISO 字符串），与列表端点及幂等重放
    # （submit_result_json 已 _jsonable）的响应形态保持一致。
    return JSONResponse(status_code=status_code, content=jsonable_encoder(payload))


@router.post("/relationship-proposals/{fact_id}/confirm", response_model=ProposalOut)
def confirm_proposal(
    fact_id: int,
    request: ProposalConfirmIn,
    space_id: int = Query(...),
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> ProposalOut:
    _gate()
    fact = confirm_relationship_proposal(
        session,
        _actor(identity, None),
        fact_id,
        expected_revision=request.expected_revision,
    )
    return ProposalOut.model_validate(
        {
            "source_fact_id": fact.id,
            "revision": fact.revision,
            "state": fact.state,
            "fact_type": fact.fact_type,
        }
    )


@router.post("/relationship-proposals/{fact_id}/reject", response_model=ProposalOut)
def reject_proposal(
    fact_id: int,
    space_id: int = Query(...),
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> ProposalOut:
    _gate()
    fact_type = reject_relationship_proposal(session, _actor(identity, None), fact_id)
    return ProposalOut.model_validate(
        {
            "source_fact_id": fact_id,
            "revision": 0,
            "state": "rejected",
            "fact_type": fact_type,
        }
    )
