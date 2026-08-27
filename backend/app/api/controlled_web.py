"""Controlled web browser endpoints and operator/space policy administration."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.errors import (
    WEB_CITATION_NOT_FOUND,
    SPACE_FORBIDDEN_ACTOR,
    SPACE_NOT_FOUND,
    raise_api_error,
)
from app.models.account import Account
from app.models.controlled_web import WebCitation, WebPlatformConfig, WebSpaceConfig, WebRequestUsage
from app.models.space import FamilySpace, SpaceMember
from app.models.user import User
from app.schemas.controlled_web import (
    WebCitationOut,
    WebFetchOut,
    WebFetchRequest,
    WebPlatformConfigOut,
    WebPlatformConfigRequest,
    WebSearchOut,
    WebSearchRequest,
    WebSpaceConfigOut,
    WebSpaceConfigRequest,
    WebUsageOut,
)
from app.services import audit, controlled_web
from app.services.platform_roles import require_platform_operator
from app.utils import secretbox, timeutil

router = APIRouter(prefix="/web", tags=["controlled-web"])
admin_router = APIRouter(prefix="/admin/web", tags=["admin-controlled-web"])


def _raise_gateway(exc: controlled_web.WebGatewayError) -> None:
    raise_api_error(exc.status_code, exc.code, exc.message, exc.detail)


def _actor_account(identity: tuple[User, Account]) -> tuple[User, Account]:
    return identity


def _space_member(db: Session, identity: tuple[User, Account], space_id: int) -> SpaceMember:
    actor, account = identity
    member = db.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == space_id,
            SpaceMember.user_id == actor.id,
            SpaceMember.status == "active",
        )
    )
    if member is None or member.role == "guest":
        raise_api_error(403, SPACE_FORBIDDEN_ACTOR, "当前账号无权访问该空间")
    return member


def _space_admin(db: Session, identity: tuple[User, Account], space_id: int) -> SpaceMember:
    member = _space_member(db, identity, space_id)
    if member.role not in {"owner", "admin"}:
        raise_api_error(403, SPACE_FORBIDDEN_ACTOR, "只有空间 owner/admin 可以修改联网策略")
    return member


def _platform_request_to_model(body: WebPlatformConfigRequest, actor: User, account: Account) -> WebPlatformConfig:
    now = timeutil.utcnow()
    secret: str | None = None
    if body.provider_secret is not None:
        secret_value = body.provider_secret.get_secret_value()
        secret = secretbox.encrypt_secret(secret_value) if secret_value else None
    return WebPlatformConfig(
        id=1,
        enabled=body.enabled,
        search_provider=body.search_provider,
        search_endpoint=str(body.search_endpoint) if body.search_endpoint else None,
        provider_secret_ciphertext=secret,
        allowed_domains_json=list(body.allowed_domains),
        denied_domains_json=list(body.denied_domains),
        max_results=body.max_results,
        max_fetch_bytes=body.max_fetch_bytes,
        max_requests_per_minute=body.max_requests_per_minute,
        monthly_budget_cents=body.monthly_budget_cents,
        updated_at=now,
        updated_by_account_id=account.id,
    )


@admin_router.get("/platform", response_model=WebPlatformConfigOut)
def get_platform_config(
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> WebPlatformConfigOut:
    _actor, account = _actor_account(identity)
    require_platform_operator(db, account)
    row = db.get(WebPlatformConfig, 1)
    if row is None:
        row = WebPlatformConfig(
            id=1,
            enabled=False,
            search_provider="configured",
            allowed_domains_json=[],
            denied_domains_json=[],
            max_results=10,
            max_fetch_bytes=1_000_000,
            max_requests_per_minute=30,
            monthly_budget_cents=0,
            updated_at=timeutil.utcnow(),
        )
        db.add(row)
        db.commit()
    return WebPlatformConfigOut.model_validate(controlled_web.platform_config_out(row))


@admin_router.put("/platform", response_model=WebPlatformConfigOut)
def update_platform_config(
    body: WebPlatformConfigRequest,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> WebPlatformConfigOut:
    actor, account = _actor_account(identity)
    require_platform_operator(db, account)
    row = db.get(WebPlatformConfig, 1)
    if row is None:
        row = _platform_request_to_model(body, actor, account)
        db.add(row)
    else:
        row.enabled = body.enabled
        row.search_provider = body.search_provider
        row.search_endpoint = str(body.search_endpoint) if body.search_endpoint else None
        row.allowed_domains_json = list(body.allowed_domains)
        row.denied_domains_json = list(body.denied_domains)
        row.max_results = body.max_results
        row.max_fetch_bytes = body.max_fetch_bytes
        row.max_requests_per_minute = body.max_requests_per_minute
        row.monthly_budget_cents = body.monthly_budget_cents
        row.updated_at = timeutil.utcnow()
        row.updated_by_account_id = account.id
        if body.provider_secret is not None:
            secret_value = body.provider_secret.get_secret_value()
            row.provider_secret_ciphertext = secretbox.encrypt_secret(secret_value) if secret_value else None
    db.commit()
    audit.write_audit(
        db,
        action="controlled_web_platform_updated",
        actor_id=actor.id,
        target_id=1,
        detail={"enabled": bool(row.enabled), "provider": row.search_provider},
    )
    db.commit()
    return WebPlatformConfigOut.model_validate(controlled_web.platform_config_out(row))


@router.get("/spaces/{space_id}/config", response_model=WebSpaceConfigOut)
def get_space_config(
    space_id: int,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> WebSpaceConfigOut:
    _space_member(db, identity, space_id)
    row = db.scalar(select(WebSpaceConfig).where(WebSpaceConfig.space_id == space_id))
    if row is None:
        raise_api_error(404, SPACE_NOT_FOUND, "空间联网配置不存在")
    return WebSpaceConfigOut.model_validate(controlled_web.space_config_out(row))


@router.put("/spaces/{space_id}/config", response_model=WebSpaceConfigOut)
def update_space_config(
    space_id: int,
    body: WebSpaceConfigRequest,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> WebSpaceConfigOut:
    actor, account = _actor_account(identity)
    _space_admin(db, identity, space_id)
    if db.get(FamilySpace, space_id) is None:
        raise_api_error(404, SPACE_NOT_FOUND, "空间不存在")
    row = db.scalar(select(WebSpaceConfig).where(WebSpaceConfig.space_id == space_id))
    if row is None:
        row = WebSpaceConfig(
            space_id=space_id,
            enabled=body.enabled,
            allowed_use_cases_json=list(body.allowed_use_cases),
            max_results=body.max_results,
            max_fetch_bytes=body.max_fetch_bytes,
            max_requests_per_minute=body.max_requests_per_minute,
            monthly_budget_cents=body.monthly_budget_cents,
            updated_at=timeutil.utcnow(),
            updated_by_account_id=account.id,
        )
        db.add(row)
    else:
        row.enabled = body.enabled
        row.allowed_use_cases_json = list(body.allowed_use_cases)
        row.max_results = body.max_results
        row.max_fetch_bytes = body.max_fetch_bytes
        row.max_requests_per_minute = body.max_requests_per_minute
        row.monthly_budget_cents = body.monthly_budget_cents
        row.updated_at = timeutil.utcnow()
        row.updated_by_account_id = account.id
    db.commit()
    audit.write_audit(
        db,
        action="controlled_web_space_updated",
        actor_id=actor.id,
        target_id=space_id,
        detail={"enabled": bool(row.enabled), "use_cases": list(row.allowed_use_cases_json)},
    )
    db.commit()
    return WebSpaceConfigOut.model_validate(controlled_web.space_config_out(row))


@router.post("/spaces/{space_id}/search", response_model=WebSearchOut)
def search_space_web(
    space_id: int,
    body: WebSearchRequest,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> WebSearchOut:
    _space_member(db, identity, space_id)
    _actor, account = _actor_account(identity)
    try:
        result = controlled_web.search_web(
            db,
            account_id=account.id,
            space_id=space_id,
            run_id=None,
            query=body.query,
            use_case=body.use_case,
            limit=body.limit,
        )
        db.commit()
    except controlled_web.WebGatewayError as exc:
        db.rollback()
        _raise_gateway(exc)
    return WebSearchOut.model_validate(result)


@router.post("/spaces/{space_id}/fetch", response_model=WebFetchOut)
def fetch_space_web(
    space_id: int,
    body: WebFetchRequest,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> WebFetchOut:
    _space_member(db, identity, space_id)
    _actor, account = _actor_account(identity)
    try:
        result = controlled_web.fetch_approved_page(
            db,
            account_id=account.id,
            space_id=space_id,
            run_id=None,
            approved_token=body.approved_token,
        )
        db.commit()
    except controlled_web.WebGatewayError as exc:
        db.rollback()
        _raise_gateway(exc)
    return WebFetchOut.model_validate(result)


@router.get("/spaces/{space_id}/usage", response_model=list[WebUsageOut])
def list_space_usage(
    space_id: int,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[WebUsageOut]:
    _space_admin(db, identity, space_id)
    rows = db.scalars(
        select(WebRequestUsage)
        .where(WebRequestUsage.space_id == space_id)
        .order_by(WebRequestUsage.created_at.desc())
        .limit(100)
    ).all()
    return [WebUsageOut.model_validate(row) for row in rows]


@router.get("/spaces/{space_id}/citations/{citation_id}", response_model=WebCitationOut)
def get_citation(
    space_id: int,
    citation_id: int,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> WebCitationOut:
    _space_member(db, identity, space_id)
    row = db.scalar(
        select(WebCitation).where(WebCitation.id == citation_id, WebCitation.space_id == space_id)
    )
    if row is None:
        raise_api_error(404, WEB_CITATION_NOT_FOUND, "引用不存在")
    return WebCitationOut.model_validate(row, from_attributes=True)
