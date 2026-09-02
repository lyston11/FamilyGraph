"""Household card browser API（GET /household-card?space_id=）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.models.account import Account
from app.models.user import User
from app.schemas.household_card import HouseholdCardOut
from app.services import family_projection, household_card

router = APIRouter(tags=["household-card"])

_CONTRACT_VERSION = "household-card-v1"


@router.get("/household-card", response_model=HouseholdCardOut)
def read_household_card(
    space_id: int,
    response: Response,
    if_none_match: str | None = Header(default=None),
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> HouseholdCardOut | Response:
    """household 空间的授权最小投影；条件请求先复核授权再比较 ETag。"""
    family_projection.require_pfv_enabled()
    _actor, account = identity
    payload = household_card.household_card_payload(session, account=account, space_id=space_id)
    etag = family_projection.etag_for_json(
        _CONTRACT_VERSION, HouseholdCardOut.model_validate(payload).model_dump_json()
    )
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return HouseholdCardOut.model_validate(payload)
