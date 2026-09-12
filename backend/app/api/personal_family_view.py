"""PersonalFamilyView and cross-lineage bridge browser APIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy.orm import Session

from app import config
from app.api.deps import get_db, require_authenticated_user
from app.errors import PERSONAL_FAMILY_VIEW_DISABLED, raise_api_error
from app.models.account import Account
from app.models.personal_family_view import PersonalFamilyBridge
from app.models.user import User
from app.schemas.personal_family_view import (
    PersonalFamilyBridgeConsent,
    PersonalFamilyBridgeCreate,
    PersonalFamilyBridgeOut,
    PersonalFamilyViewOut,
)
from app.services import personal_family_bridge, personal_family_view

router = APIRouter(tags=["personal-family-view"])


def _require_enabled() -> None:
    if not config.PERSONAL_FAMILY_VIEW_ENABLED:
        raise_api_error(503, PERSONAL_FAMILY_VIEW_DISABLED, "个人家族视图功能未启用")


def _bridge_out(row: PersonalFamilyBridge) -> PersonalFamilyBridgeOut:
    return PersonalFamilyBridgeOut.model_validate(row, from_attributes=True)


@router.get(
    "/personal-family-view",
    response_model=PersonalFamilyViewOut,
    dependencies=[Depends(_require_enabled)],
)
def read_personal_family_view(
    space_id: int,
    response: Response,
    if_none_match: str | None = Header(default=None),
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> PersonalFamilyViewOut | Response:
    _actor, account = identity
    if space_id <= 0:
        raise_api_error(422, "VALIDATION_ERROR", "空间参数不合法")
    # 1. 授权与只读读取（get_current_view 复核空间/成员资格，404 先于一切缓存判断；
    #    GET 全程只读，不建行不隐式重算——R5）。
    view = personal_family_view.get_current_view(session, account=account, space_id=space_id)
    if view is None:
        # 尚无投影行：安全空态 + 显式短事务登记后台重算
        personal_family_view.request_view_recompute(space_id=space_id)
        return PersonalFamilyViewOut.model_validate(
            personal_family_view.empty_view_payload(space_id=space_id)
        )
    etag = personal_family_view.etag_for(view, account=account)
    current = personal_family_view.view_is_current(
        session, view=view, account=account, space_id=space_id
    )
    # 2. 只有完整权限复核 + 新鲜度（事实/词典/策略/计算版本）通过才允许 304；
    #    stale/版本漂移的旧 ETag 绝不命中（R5）。
    if if_none_match == etag and current:
        return Response(status_code=304, headers={"ETag": etag})
    if not current:
        # 显式短事务登记重算（独立事务；GET 自身事务不承担入队写）
        personal_family_view.request_view_recompute(space_id=space_id)
    payload = personal_family_view.view_payload(session, account=account, space_id=space_id)
    response.headers["ETag"] = etag
    result = PersonalFamilyViewOut.model_validate(payload)
    return result


@router.post(
    "/personal-family-bridges",
    response_model=PersonalFamilyBridgeOut,
    dependencies=[Depends(_require_enabled)],
)
def create_personal_family_bridge(
    request: PersonalFamilyBridgeCreate,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> PersonalFamilyBridgeOut:
    actor, account = identity
    row = personal_family_bridge.create_bridge(
        session,
        account=account,
        anchor_user_id=request.anchor_user_id or actor.id,
        other_space_id=request.other_space_id,
        other_anchor_user_id=request.other_anchor_user_id,
        scope=request.scope,
        expires_at=request.expires_at,
    )
    session.commit()
    return _bridge_out(row)


@router.post(
    "/personal-family-bridges/{bridge_id}/consent",
    response_model=PersonalFamilyBridgeOut,
    dependencies=[Depends(_require_enabled)],
)
def consent_personal_family_bridge(
    bridge_id: int,
    request: PersonalFamilyBridgeConsent,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> PersonalFamilyBridgeOut:
    _actor, account = identity
    row = personal_family_bridge.consent_bridge(
        session, bridge_id=bridge_id, account=account, revision=request.revision
    )
    session.commit()
    return _bridge_out(row)


@router.post(
    "/personal-family-bridges/{bridge_id}/revoke",
    response_model=PersonalFamilyBridgeOut,
    dependencies=[Depends(_require_enabled)],
)
def revoke_personal_family_bridge(
    bridge_id: int,
    request: PersonalFamilyBridgeConsent,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> PersonalFamilyBridgeOut:
    _actor, account = identity
    row = personal_family_bridge.revoke_bridge(
        session, bridge_id=bridge_id, account=account, revision=request.revision
    )
    session.commit()
    return _bridge_out(row)
