"""PersonalFamilyView and cross-lineage bridge browser APIs."""

from __future__ import annotations

from datetime import UTC, timedelta

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
    PFVDemandIn,
    PFVDemandOut,
)
from app.services import family_projection, personal_family_bridge, personal_family_view
from app.utils.timeutil import utcnow

router = APIRouter(tags=["personal-family-view"])

# 最终载荷 ETag 的合同版本：topology_edges 进入响应后递增（design §4）。
_PFV_PAYLOAD_CONTRACT_VERSION = "personal-family-view-v4-publication"


def _require_enabled() -> None:
    if not config.PERSONAL_FAMILY_VIEW_ENABLED:
        raise_api_error(503, PERSONAL_FAMILY_VIEW_DISABLED, "个人家族视图功能未启用")


def _bridge_out(row: PersonalFamilyBridge) -> PersonalFamilyBridgeOut:
    return PersonalFamilyBridgeOut.model_validate(row, from_attributes=True)


@router.get(
    "/personal-family-view",
    response_model=PersonalFamilyViewOut,
    response_model_exclude_unset=True,
    dependencies=[Depends(_require_enabled)],
)
def read_personal_family_view(
    space_id: int,
    response: Response,
    if_none_match: str | None = Header(default=None),
    progressive: bool = False,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> PersonalFamilyViewOut | Response:
    from app.services import steward_snapshot, steward_views

    _actor, account = identity
    if space_id <= 0:
        raise_api_error(422, "VALIDATION_ERROR", "空间参数不合法")
    # Close a genuine read snapshot before the separate demand writer runs.
    with steward_snapshot.read_transaction(session.get_bind()) as read:
        payload, semantic_until = steward_views.payload_for(
            read, account=account, space_id=space_id, progressive=progressive
        )
        if payload is None:
            view = personal_family_view.get_current_view(read, account=account, space_id=space_id)
            payload = (
                personal_family_view._view_payload_for_view(
                    read, account=account, space_id=space_id, view=view
                )
                if view is not None
                else personal_family_view.empty_view_payload(space_id=space_id)
            )
            if progressive:
                payload = personal_family_view.attach_progress(
                    read, account=account, space_id=space_id, payload=payload, view=view
                )
    if payload["status"] != "current":
        personal_family_view.request_view_recompute(space_id=space_id, account=account)
    result = PersonalFamilyViewOut.model_validate(payload)
    etag = family_projection.etag_for_json(
        f"{_PFV_PAYLOAD_CONTRACT_VERSION}:{account.id}:{account.token_version}:{progressive}",
        result.model_dump_json(exclude_unset=True),
    )
    now = utcnow()
    display_until = now + timedelta(seconds=config.PERSONAL_FAMILY_VIEW_DISPLAY_TTL_SECONDS)
    if semantic_until is not None:
        display_until = min(display_until, semantic_until)
    headers = {
        "ETag": etag,
        "X-PFV-Display-Until": str(int(display_until.replace(tzinfo=UTC).timestamp())),
        "X-PFV-Validated-At": str(int(now.replace(tzinfo=UTC).timestamp())),
        "Access-Control-Expose-Headers": "ETag, X-PFV-Display-Until, X-PFV-Validated-At, Date",
        "Cache-Control": "private, no-cache",
    }
    # Preview 304 is safe only after this request's complete authorization fence.
    if if_none_match == etag and (
        payload["status"] == "current" or (progressive and bool(payload["nodes"]))
    ):
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return result


@router.post(
    "/personal-family-view/demand",
    response_model=PFVDemandOut,
    dependencies=[Depends(_require_enabled)],
)
def demand_personal_family_view(
    request: PFVDemandIn,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> PFVDemandOut:
    from app.services import steward_demand

    _actor, account = identity
    status = steward_demand.register(
        account=account,
        space_id=request.space_id,
        focus_user_id=request.focus_user_id,
        retry=request.retry,
    )
    return PFVDemandOut(
        status="queued" if status == "queued" else "already_active",
        focus_user_id=request.focus_user_id,
    )


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
