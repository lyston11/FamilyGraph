"""Family-facing read-only platform capability status."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.models.account import Account
from app.models.user import User
from app.schemas.platform_features import PlatformFeatureFlagsOut
from app.services.platform_features import get_platform_feature_state

router = APIRouter(tags=["platform-features"])


@router.get("/platform-features", response_model=PlatformFeatureFlagsOut)
def platform_features(
    db: Session = Depends(get_db),
    _identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> PlatformFeatureFlagsOut:
    state = get_platform_feature_state(db)
    return PlatformFeatureFlagsOut(
        memory_enabled=state.memory_enabled,
        rag_enabled=state.rag_enabled,
    )
