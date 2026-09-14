"""System-admin governance for platform-level Memory and RAG switches."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.admin_deps import AdminPrincipal, require_admin_ready
from app.api.deps import get_db
from app.schemas.platform_features import (
    PlatformFeatureAdminOut,
    PlatformFeatureUpdateRequest,
    StewardAssistSwitchesOut,
)
from app.services import admin_audit, platform_features

router = APIRouter(prefix="/admin-api/v1", tags=["admin-platform-features"])
_ENDPOINT = "/admin-api/v1/platform-features"


def _assist_out(state: platform_features.PlatformFeatureState) -> StewardAssistSwitchesOut:
    return StewardAssistSwitchesOut(
        candidate=state.steward_assist_candidate,
        ranking=state.steward_assist_ranking,
        explanation=state.steward_assist_explanation,
        terminology=state.steward_assist_terminology,
        candidate_source=state.steward_assist_candidate_source,
        ranking_source=state.steward_assist_ranking_source,
        explanation_source=state.steward_assist_explanation_source,
        terminology_source=state.steward_assist_terminology_source,
    )


def _out(db: Session) -> PlatformFeatureAdminOut:
    state = platform_features.get_platform_feature_state(db)
    return PlatformFeatureAdminOut(
        memory_enabled=state.memory_enabled,
        rag_enabled=state.rag_enabled,
        memory_source=state.memory_source,
        rag_source=state.rag_source,
        steward_assist=_assist_out(state),
        updated_at=state.updated_at,
    )


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/platform-features", response_model=PlatformFeatureAdminOut)
def get_platform_features(
    request: Request,
    db: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> PlatformFeatureAdminOut:
    admin, _account = identity
    result = _out(db)
    admin_audit.record_access(
        db,
        action="platform_features.read",
        endpoint=_ENDPOINT,
        system_admin_id=admin.id,
        result_count=1,
        ip=_ip(request),
    )
    db.commit()
    return result


@router.put("/platform-features", response_model=PlatformFeatureAdminOut)
def update_platform_features(
    body: PlatformFeatureUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> PlatformFeatureAdminOut:
    admin, _account = identity
    state = platform_features.set_platform_feature_state(
        db,
        memory_enabled=body.memory_enabled,
        rag_enabled=body.rag_enabled,
        steward_assist_candidate=body.steward_assist_candidate,
        steward_assist_ranking=body.steward_assist_ranking,
        steward_assist_explanation=body.steward_assist_explanation,
        steward_assist_terminology=body.steward_assist_terminology,
        system_admin_id=admin.id,
    )
    admin_audit.record_access(
        db,
        action="platform_features.update",
        endpoint=_ENDPOINT,
        system_admin_id=admin.id,
        filters={
            "memory_enabled": body.memory_enabled,
            "rag_enabled": body.rag_enabled,
            "steward_assist_candidate": body.steward_assist_candidate,
            "steward_assist_ranking": body.steward_assist_ranking,
            "steward_assist_explanation": body.steward_assist_explanation,
            "steward_assist_terminology": body.steward_assist_terminology,
        },
        result_count=1,
        ip=_ip(request),
    )
    db.commit()
    return PlatformFeatureAdminOut(
        memory_enabled=state.memory_enabled,
        rag_enabled=state.rag_enabled,
        memory_source=state.memory_source,
        rag_source=state.rag_source,
        steward_assist=_assist_out(state),
        updated_at=state.updated_at,
    )
