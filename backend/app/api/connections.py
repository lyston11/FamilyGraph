"""合并请求与关系操作路由（m1b）。

v2 D2：写路径全部走应用命令层（app.commands.connections，AC-F7）；
connection_request = AD-4 合并语义：relation pending + 可选 space_members
pending 同事务。路由只做 schema 解析 + 认证 + 命令调用 + 序列化。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.commands import connections as connection_commands
from app.commands.context import ActorContext
from app.models.account import Account
from app.models.relation import Relation
from app.models.user import User
from app.schemas.relation import (
    ConnectionRequestCreate,
    RelationOut,
    RelationViewOut,
)
from app.services.kinship import display_relation

router = APIRouter(tags=["connections"])


def _relation_out(edge: Relation, viewer_id: int) -> RelationOut:
    dir_class, label, from_creator = display_relation(edge, viewer_id)
    view = RelationViewOut(dir_class=dir_class, label=label, label_from_creator=from_creator)  # type: ignore[arg-type]
    return RelationOut(
        id=edge.id,
        from_user=edge.from_user,
        to_user=edge.to_user,
        dir_class=edge.dir_class,  # type: ignore[arg-type]
        label=edge.label,
        status=edge.status,  # type: ignore[arg-type]
        created_by=edge.created_by,
        pending_space_id=edge.pending_space_id,
        view=view,
    )


@router.post("/connection-requests", status_code=201, response_model=RelationOut)
def create_connection_request(
    payload: ConnectionRequestCreate,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> RelationOut:
    """向已有账号发起合并请求：relation pending（+可选空间成员，m1c）。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    edge = connection_commands.create_connection_request(
        session,
        ctx,
        target_id=payload.target_id,
        dir_class=payload.dir_class,
        label=payload.label,
        space_membership_space_id=(
            payload.space_membership.space_id if payload.space_membership else None
        ),
    )
    session.refresh(edge)
    return _relation_out(edge, actor.id)


@router.post("/connection-requests/{edge_id}/accept", response_model=RelationOut)
def accept_connection(
    edge_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> RelationOut:
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    edge = connection_commands.decide_connection_request(session, ctx, edge_id, accept=True)
    session.refresh(edge)
    return _relation_out(edge, actor.id)


@router.post("/connection-requests/{edge_id}/reject", response_model=RelationOut)
def reject_connection(
    edge_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> RelationOut:
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    edge = connection_commands.decide_connection_request(session, ctx, edge_id, accept=False)
    session.refresh(edge)
    return _relation_out(edge, actor.id)


@router.post("/connection-requests/{edge_id}/cancel", response_model=RelationOut)
def cancel_connection(
    edge_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> RelationOut:
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account)
    edge = connection_commands.cancel_connection(session, ctx, edge_id)
    session.refresh(edge)
    return _relation_out(edge, actor.id)


@router.post("/relations/{edge_id}/revoke", response_model=RelationOut)
def revoke_relation(
    edge_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> RelationOut:
    """断连轨（D8）：任一方即可，不动档案。撤权传播经 relation.revoked 事件。"""
    actor, account = identity
    ctx = ActorContext.from_identity(actor, account, ip=_client_ip(request))
    edge = connection_commands.revoke_relation(session, ctx, edge_id)
    session.refresh(edge)
    return _relation_out(edge, actor.id)


@router.get("/connections/incoming", response_model=list[RelationOut])
def list_incoming_connections(
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[RelationOut]:
    """发给我的 pending 请求（审批 UI 归 m2c，本端点供列表/红点）。"""
    actor, _account = identity
    edges = (
        session.query(Relation)
        .filter(Relation.to_user == actor.id, Relation.status == "pending")
        .order_by(Relation.created_at.desc())
        .all()
    )
    return [_relation_out(e, actor.id) for e in edges]


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
