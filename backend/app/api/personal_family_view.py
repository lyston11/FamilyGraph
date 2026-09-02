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
    view = personal_family_view.get_view(session, account=account, space_id=space_id)
    etag = personal_family_view.etag_for(view)
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
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
