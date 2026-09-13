"""Steward 推测边操作 API（任务 09-13-steward-inferred-tree-layer；前缀
/api/steward-inferred-edges）。

合同（design.md §5）：
- 确认：有权当事人（端点本人 ∪ 合法代管人）→ 创建提案并同事务确认转正
  （200，confirmed SourceFact 入图）；非当事人 → 仅代为创建提案（202 语义，
  推测边保持 proposed，待对方确认）。确认走 relationship_proposals 现行
  consent 合同，路由绝不直接 transition_source_fact。
- 驳回：proposed → rejected（同证据冷却由投影侧判定）；撤销驳回：rejected
  → proposed（活跃上限内）。
- 幂等语义：状态幂等（重复确认/驳回/撤销返回既有状态，不产生第二副作用）。
- 全部端点：revision CAS（409）；superseded 终态/不可见统一 404 防枚举；
  状态转换写 domain event + 命令层审计。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app import config
from app.api.deps import get_db, require_authenticated_user
from app.commands.context import ActorContext
from app.errors import ACTION_CARD_FLAG_DISABLED, VALIDATION_ERROR, raise_api_error
from app.models.account import Account
from app.models.user import User
from app.services import steward_inferred

router = APIRouter(tags=["steward-inferred"])


def _gate() -> None:
    if not config.STEWARD_ENABLED:
        raise_api_error(503, ACTION_CARD_FLAG_DISABLED, "Steward 功能未启用")


def _actor(identity: tuple[User, Account], request_ip: str | None) -> ActorContext:
    user, account = identity
    return ActorContext(
        user_id=user.id, account_id=account.id, account_status=account.status, ip=request_ip
    )


class InferredEdgeActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


class InferredEdgeActionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    status: str
    revision: int


class InferredEdgeConfirmOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge: InferredEdgeActionOut
    linked_proposal: dict[str, Any] | None
    pending_confirmations: list[dict[str, int]]


@router.post("/steward-inferred-edges/{edge_id}/confirm")
def confirm_inferred_edge(
    edge_id: int,
    request: InferredEdgeActionIn,
    space_id: int = Query(...),
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> Any:
    _gate()
    _user, account = identity
    status_code, payload = steward_inferred.confirm_edge(
        session,
        _actor(identity, None),
        account=account,
        space_id=space_id,
        edge_id=edge_id,
        expected_revision=request.expected_revision,
    )
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content=jsonable_encoder(payload))


@router.post("/steward-inferred-edges/{edge_id}/dismiss", response_model=InferredEdgeActionOut)
def dismiss_inferred_edge(
    edge_id: int,
    request: InferredEdgeActionIn,
    space_id: int = Query(...),
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> InferredEdgeActionOut:
    _gate()
    _user, account = identity
    result = steward_inferred.dismiss_edge(
        session,
        account=account,
        space_id=space_id,
        edge_id=edge_id,
        expected_revision=request.expected_revision,
    )
    return InferredEdgeActionOut.model_validate(result)


@router.post("/steward-inferred-edges/{edge_id}/reinstate", response_model=InferredEdgeActionOut)
def reinstate_inferred_edge(
    edge_id: int,
    request: InferredEdgeActionIn,
    space_id: int = Query(...),
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> InferredEdgeActionOut:
    _gate()
    _user, account = identity
    if request.expected_revision < 1:
        raise_api_error(422, VALIDATION_ERROR, "revision 不合法")
    result = steward_inferred.reinstate_edge(
        session,
        account=account,
        space_id=space_id,
        edge_id=edge_id,
        expected_revision=request.expected_revision,
    )
    return InferredEdgeActionOut.model_validate(result)
