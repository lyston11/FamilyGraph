"""合并请求与关系操作路由（m1b）。

connection_request = AD-4 合并语义：relation pending + 可选 space_members
pending 同事务。space_members 表属 m1c，本任务对 space_membership 字段
返回 422 SPACE_MEMBERSHIP_DEFERRED_M1C（TODO(m1c) 放开）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.errors import (
    CONNECTION_ALREADY_RESOLVED,
    RELATION_NOT_FOUND,
    SPACE_MEMBERSHIP_DEFERRED_M1C,
    USER_NOT_FOUND,
    raise_api_error,
)
from app.models.account import Account
from app.models.relation import Relation
from app.models.user import User
from app.schemas.relation import (
    ConnectionRequestCreate,
    RelationOut,
    RelationViewOut,
)
from app.services import audit, relation_fsm
from app.services.kinship import display_relation

router = APIRouter(tags=["connections"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


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
        view=view,
    )


def _get_edge_for_actor(session: Session, edge_id: int, actor_id: int) -> Relation:
    edge = session.get(Relation, edge_id)
    if edge is None or actor_id not in (edge.from_user, edge.to_user):
        # 无权/不存在同一 404 语义（防枚举）
        raise_api_error(404, RELATION_NOT_FOUND, "关系不存在")
    return edge


@router.post("/connection-requests", status_code=201, response_model=RelationOut)
def create_connection_request(
    payload: ConnectionRequestCreate,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> RelationOut:
    """向已有账号发起合并请求：relation pending（+可选空间成员，m1c）。"""
    actor, _account = identity
    if payload.space_membership is not None:  # TODO(m1c)：合并 space_members 行同事务
        raise_api_error(
            422,
            SPACE_MEMBERSHIP_DEFERRED_M1C,
            "空间成员邀请将在家庭空间功能上线后可用",
        )
    target = session.get(User, payload.target_id)
    if target is None:
        raise_api_error(404, USER_NOT_FOUND, "对方档案不存在")

    edge = relation_fsm.create_relation(
        session,
        from_user=actor.id,
        to_user=payload.target_id,
        dir_class=payload.dir_class,
        label=payload.label,
        status="pending",
    )
    audit.write_audit(
        session,
        action="connection_requested",
        actor_id=actor.id,
        target_id=payload.target_id,
        ip=_client_ip(request),
        detail={"relation_id": edge.id, "dir_class": payload.dir_class},
    )
    session.commit()
    session.refresh(edge)
    return _relation_out(edge, actor.id)


@router.post("/connection-requests/{edge_id}/accept", response_model=RelationOut)
def accept_connection(
    edge_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> RelationOut:
    actor, _account = identity
    edge = _get_edge_for_actor(session, edge_id, actor.id)
    if edge.status != "pending":
        raise_api_error(
            409, CONNECTION_ALREADY_RESOLVED, "该请求已处理", detail={"status": edge.status}
        )
    relation_fsm.transition(edge, "accept", actor.id, session)
    audit.write_audit(
        session,
        action="connection_accepted",
        actor_id=actor.id,
        target_id=edge.from_user if actor.id == edge.to_user else edge.to_user,
        ip=_client_ip(request),
        detail={"relation_id": edge.id},
    )
    session.commit()
    session.refresh(edge)
    return _relation_out(edge, actor.id)


@router.post("/connection-requests/{edge_id}/reject", response_model=RelationOut)
def reject_connection(
    edge_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> RelationOut:
    actor, _account = identity
    edge = _get_edge_for_actor(session, edge_id, actor.id)
    if edge.status != "pending":
        raise_api_error(
            409, CONNECTION_ALREADY_RESOLVED, "该请求已处理", detail={"status": edge.status}
        )
    relation_fsm.transition(edge, "reject", actor.id, session)
    audit.write_audit(
        session,
        action="connection_rejected",
        actor_id=actor.id,
        target_id=edge.from_user if actor.id == edge.to_user else edge.to_user,
        ip=_client_ip(request),
        detail={"relation_id": edge.id},
    )
    session.commit()
    session.refresh(edge)
    return _relation_out(edge, actor.id)


@router.post("/connection-requests/{edge_id}/cancel", response_model=RelationOut)
def cancel_connection(
    edge_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> RelationOut:
    actor, _account = identity
    edge = _get_edge_for_actor(session, edge_id, actor.id)
    relation_fsm.transition(edge, "cancel", actor.id, session)
    session.commit()
    session.refresh(edge)
    return _relation_out(edge, actor.id)


@router.post("/relations/{edge_id}/revoke", response_model=RelationOut)
def revoke_relation(
    edge_id: int,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> RelationOut:
    """断连轨（D8）：任一方即可，不动档案。"""
    actor, _account = identity
    edge = _get_edge_for_actor(session, edge_id, actor.id)
    relation_fsm.transition(edge, "revoke", actor.id, session)
    audit.write_audit(
        session,
        action="relation_revoked",
        actor_id=actor.id,
        target_id=edge.from_user if actor.id == edge.to_user else edge.to_user,
        ip=_client_ip(request),
        detail={"relation_id": edge.id},
    )
    session.commit()
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
