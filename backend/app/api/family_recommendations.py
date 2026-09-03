from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.models.account import Account
from app.models.user import User
from app.schemas.family_recommendations import (
    FamilyRecommendationDismissIn,
    FamilyRecommendationsOut,
)
from app.services import family_recommendations

router = APIRouter(tags=["family-recommendations"])


@router.get("/family-recommendations", response_model=FamilyRecommendationsOut)
def read_family_recommendations(
    space_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> FamilyRecommendationsOut:
    _actor, account = identity
    return FamilyRecommendationsOut.model_validate(
        family_recommendations.recommendations_payload(session, account=account, space_id=space_id)
    )


@router.post("/family-recommendations/dismiss", status_code=204)
def dismiss_family_recommendation(
    payload: FamilyRecommendationDismissIn,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> Response:
    _actor, account = identity
    family_recommendations.dismiss_recommendation(
        session,
        account=account,
        space_id=payload.space_id,
        target_user_id=payload.target_user_id,
        category=payload.category,
    )
    session.commit()
    return Response(status_code=204)
