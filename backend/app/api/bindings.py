"""绑定请求路由（09-05 Chunk D，全部挂在 8000 家庭 listener）。

薄路由惯例（AC-F7）：schema 解析 + 认证上下文构造 → 命令层 → 序列化；
授权、PIN 复验、FSM、人物并回、事件与审计在命令层同一短事务内完成
（commands/bindings.py）。

- GET    /api/bindings            被绑定人视角：我的绑定请求（含已决议历史）；
- POST   /api/bindings/{id}/confirm  被绑定人本人 + PIN 复验 + 「这是我」；
- POST   /api/bindings/{id}/reject   被绑定人拒绝（终态）；
- DELETE /api/bindings/{id}          发起人取消 pending（终态）。

防枚举：非本人与不存在同一 404；终态再处理 409。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.commands import bindings as binding_commands
from app.commands.context import ActorContext
from app.models.account import Account
from app.models.account_binding import AccountBinding
from app.models.user import User
from app.schemas.binding import BindingOut, ConfirmBindingRequest

router = APIRouter(tags=["bindings"])


def _ctx(request: Request, identity: tuple[User, Account]) -> ActorContext:
    actor, account = identity
    ip = request.client.host if request.client else None
    return ActorContext.from_identity(actor, account, ip=ip)


def _serialize(summary: binding_commands.BindingSummary) -> BindingOut:
    binding: AccountBinding = summary.binding
    # status 为 DB CHECK 保证枚举合法的 str；model_validate 运行时复核 Literal
    return BindingOut.model_validate(
        {
            "id": binding.id,
            "initiator_name": summary.initiator_name,
            "person_name": summary.person_name,
            "status": binding.status,
            "created_at": binding.created_at,
            "resolved_at": binding.resolved_at,
        }
    )


def _serialize_binding(binding: AccountBinding) -> BindingOut:
    return BindingOut.model_validate(
        {
            "id": binding.id,
            "initiator_name": None,
            "person_name": None,
            "status": binding.status,
            "created_at": binding.created_at,
            "resolved_at": binding.resolved_at,
        }
    )


@router.get("/bindings", response_model=list[BindingOut])
def list_my_bindings(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[BindingOut]:
    """我的绑定请求（被绑定人视角；确认前发起人侧零数据可见的镜像面）。"""
    summaries = binding_commands.list_incoming_bindings(
        session, ActorContext.from_identity(identity[0], identity[1])
    )
    return [_serialize(summary) for summary in summaries]


@router.post("/bindings/{binding_id}/confirm", response_model=BindingOut)
def confirm_binding(
    binding_id: int,
    payload: ConfirmBindingRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> BindingOut:
    """「这是我」确认：被绑定人本人 + PIN 复验 + 人物并回既有 user（§0.9）。

    identity_confirmed 经 identity_fsm 唯一转换点获得（决策 5：身份确认不自证——
    家人建档断言 + 本人 PIN 复验共同构成确认）；重复确认 409（终态不可逆）。
    """
    binding = binding_commands.confirm_binding(
        session, _ctx(request, identity), binding_id, pin=payload.pin
    )
    return _serialize_binding(binding)


@router.post("/bindings/{binding_id}/reject", response_model=BindingOut)
def reject_binding(
    binding_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> BindingOut:
    """被绑定人拒绝（终态）：撞名建档产生的人物随之废弃。"""
    binding = binding_commands.reject_binding(session, _ctx(request, identity), binding_id)
    return _serialize_binding(binding)


@router.delete("/bindings/{binding_id}", response_model=BindingOut)
def cancel_binding(
    binding_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> BindingOut:
    """发起人取消 pending 绑定（终态）；仅发起人本人可操作。"""
    binding = binding_commands.cancel_binding(session, _ctx(request, identity), binding_id)
    return _serialize_binding(binding)
