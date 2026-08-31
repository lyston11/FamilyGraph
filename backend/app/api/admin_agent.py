"""Agent Provider 治理端点（RT-5；platform_operator 专属，前缀 /api/admin/agent）。

- Provider 注册/列表/更新：secret 只写不读，任何响应只含 has_secret 布尔，
  永不含明文或密文（密钥经 utils/secretbox 加密落库）；
- 空间级设置：model 必须在该 Provider allowed_models 内；provider_id=None
  清除该空间选择；策略结果由 services/agent_provider 推导，不落库；
- 全部操作写审计（operator 归属）；feature flag 关闭时一律 503。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.api.deps import get_db, require_authenticated_user
from app.errors import (
    AGENT_PROVIDER_NOT_FOUND,
    AGENT_RUNTIME_DISABLED,
    SPACE_NOT_FOUND,
    VALIDATION_ERROR,
    raise_api_error,
)
from app.models.account import Account
from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
from app.models.space import FamilySpace
from app.models.user import User
from app.schemas.agent import (
    AgentProviderCreateRequest,
    AgentProviderOut,
    AgentProviderPatchRequest,
    AgentSpaceProviderSettingsOut,
    AgentSpaceProviderSettingsRequest,
)
from app.services import agent_provider, audit
from app.services.platform_roles import require_platform_operator
from app.utils import secretbox, timeutil


def _require_runtime_enabled() -> None:
    if not config.AGENT_RUNTIME_ENABLED:
        raise_api_error(503, AGENT_RUNTIME_DISABLED, "Agent Runtime 未启用")


router = APIRouter(tags=["admin-agent"], dependencies=[Depends(_require_runtime_enabled)])


def _require_operator(
    db: Session,
    identity: tuple[User, Account],
) -> User:
    actor, account = identity
    require_platform_operator(db, account)
    return actor


def _provider_out(row: AgentProvider) -> AgentProviderOut:
    return AgentProviderOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        api=row.api,
        base_url=row.base_url,
        compat=dict(row.compat_json or {}),
        context_window=row.context_window,
        max_tokens=row.max_tokens,
        reasoning=bool(row.reasoning),
        input_modalities=list(row.input_modalities_json or []),
        thinking_levels=list(row.thinking_levels_json or []),
        has_secret=row.secret_ciphertext is not None,
        allowed_models=list(row.allowed_models_json or []),
        enabled=bool(row.enabled),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/providers", response_model=AgentProviderOut, status_code=201)
def register_provider(
    body: AgentProviderCreateRequest,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> AgentProviderOut:
    actor = _require_operator(db, identity)
    if body.kind == "openai_compatible" and not (body.base_url or "").strip():
        raise_api_error(422, VALIDATION_ERROR, "openai_compatible Provider 必须提供 base_url")
    now = timeutil.utcnow()
    row = AgentProvider(
        name=body.name,
        kind=body.kind,
        api=body.api,
        base_url=body.base_url,
        compat_json=dict(body.compat),
        context_window=body.context_window,
        max_tokens=body.max_tokens,
        reasoning=body.reasoning,
        input_modalities_json=list(body.input_modalities),
        thinking_levels_json=list(body.thinking_levels),
        secret_ciphertext=secretbox.encrypt_secret(body.secret) if body.secret else None,
        allowed_models_json=list(body.allowed_models),
        enabled=body.enabled,
        created_at=now,
        updated_at=now,
    )
    profile_error = agent_provider.provider_profile_error(row)
    if profile_error is not None:
        raise_api_error(
            422,
            VALIDATION_ERROR,
            "云 Provider 必须使用受控的 liu-dada/gpt-5.6-sol Pi profile",
            {"reason": profile_error},
        )
    db.add(row)
    db.commit()
    audit.write_audit(
        db,
        action="agent_provider_registered",
        actor_id=actor.id,
        target_id=row.id,
        detail={"name": row.name, "kind": row.kind},
    )
    db.commit()
    return _provider_out(row)


@router.get("/providers", response_model=list[AgentProviderOut])
def list_providers(
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> list[AgentProviderOut]:
    _require_operator(db, identity)
    rows = db.scalars(select(AgentProvider).order_by(AgentProvider.id.asc())).all()
    return [_provider_out(r) for r in rows]


@router.patch("/providers/{provider_id}", response_model=AgentProviderOut)
def update_provider(
    provider_id: int,
    body: AgentProviderPatchRequest,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> AgentProviderOut:
    """部分更新；secret 仅在显式提供时轮换（空字符串清除）。永不回读。"""
    actor = _require_operator(db, identity)
    row = db.get(AgentProvider, provider_id)
    if row is None:
        raise_api_error(404, AGENT_PROVIDER_NOT_FOUND, "Provider 不存在")
    provided = body.model_fields_set
    if "api" in provided and body.api is not None:
        row.api = body.api
    if "base_url" in provided:
        row.base_url = body.base_url
    if "compat" in provided and body.compat is not None:
        row.compat_json = dict(body.compat)
    for field, attr in (
        ("context_window", "context_window"),
        ("max_tokens", "max_tokens"),
        ("reasoning", "reasoning"),
    ):
        if field in provided:
            setattr(row, attr, getattr(body, field))
    if "input_modalities" in provided and body.input_modalities is not None:
        row.input_modalities_json = list(body.input_modalities)
    if "thinking_levels" in provided and body.thinking_levels is not None:
        row.thinking_levels_json = list(body.thinking_levels)
    if "allowed_models" in provided and body.allowed_models is not None:
        row.allowed_models_json = list(body.allowed_models)
    if "enabled" in provided and body.enabled is not None:
        row.enabled = body.enabled
    if "secret" in provided:
        # 只写语义：非空轮换密文；空字符串表示清除本地无密钥形态
        row.secret_ciphertext = secretbox.encrypt_secret(body.secret) if body.secret else None
    profile_error = agent_provider.provider_profile_error(row)
    if profile_error is not None:
        raise_api_error(
            422,
            VALIDATION_ERROR,
            "云 Provider 必须使用受控的 liu-dada/gpt-5.6-sol Pi profile",
            {"reason": profile_error},
        )
    row.updated_at = timeutil.utcnow()
    db.commit()
    audit.write_audit(
        db,
        action="agent_provider_updated",
        actor_id=actor.id,
        target_id=row.id,
        detail={"fields": sorted(provided)},
    )
    db.commit()
    return _provider_out(row)


@router.put("/spaces/{space_id}/provider-settings", response_model=AgentSpaceProviderSettingsOut)
def upsert_space_provider_settings(
    space_id: int,
    body: AgentSpaceProviderSettingsRequest,
    db: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> AgentSpaceProviderSettingsOut:
    """空间级 Provider 选择与开关；model 必须在所选 Provider 的 allowlist 内。"""
    actor = _require_operator(db, identity)
    space = db.get(FamilySpace, space_id)
    if space is None:
        raise_api_error(404, SPACE_NOT_FOUND, "空间不存在")

    existing = db.scalar(
        select(AgentSpaceProviderSetting).where(AgentSpaceProviderSetting.space_id == space_id)
    )
    if body.provider_id is None:
        # 清除空间选择：解析回到 POLICY_DENIED（no_space_setting），绝不静默替补
        if existing is not None:
            db.delete(existing)
            db.commit()
        audit.write_audit(
            db,
            action="agent_space_provider_cleared",
            actor_id=actor.id,
            target_id=space_id,
            detail={},
        )
        db.commit()
        return AgentSpaceProviderSettingsOut(
            space_id=space_id,
            provider_id=None,
            model=None,
            cloud_allowed=False,
            local_required=False,
            enabled=False,
        )

    provider = db.get(AgentProvider, body.provider_id)
    if provider is None:
        raise_api_error(404, AGENT_PROVIDER_NOT_FOUND, "Provider 不存在")
    if not body.model:
        raise_api_error(422, VALIDATION_ERROR, "选择 Provider 时必须指定 model")
    if body.model not in list(provider.allowed_models_json or []):
        raise_api_error(
            422,
            VALIDATION_ERROR,
            "model 不在该 Provider 的 allowed_models 内",
            {"allowed_models": list(provider.allowed_models_json or [])},
        )

    if existing is None:
        existing = AgentSpaceProviderSetting(space_id=space_id, provider_id=provider.id, model="")
        db.add(existing)
    existing.provider_id = provider.id
    existing.model = body.model
    existing.cloud_allowed = body.cloud_allowed
    existing.local_required = body.local_required
    existing.enabled = True
    db.commit()
    audit.write_audit(
        db,
        action="agent_space_provider_settings_updated",
        actor_id=actor.id,
        target_id=space_id,
        detail={
            "provider_id": provider.id,
            "cloud_allowed": existing.cloud_allowed,
            "local_required": existing.local_required,
        },
    )
    db.commit()
    return AgentSpaceProviderSettingsOut(
        space_id=space_id,
        provider_id=existing.provider_id,
        model=existing.model,
        cloud_allowed=existing.cloud_allowed,
        local_required=existing.local_required,
        enabled=existing.enabled,
    )
