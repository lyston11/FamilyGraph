"""Agent Provider 治理端点（系统管理员域；仅 admin_app :8002，前缀 /admin-api/v1）。

职责边界（09-06 治理迁移 D1/D3/D6，替代旧家庭 platform_operator 端点）：
- Provider 注册/列表/更新：secret 只写不读，任何响应只含 has_secret 布尔，
  永不含明文或密文（密钥经 utils/secretbox 加密落库）；
- 平台默认模型设置（agent_platform_defaults 单行表）：只决定通道与档位，
  绝不替 owner 打开云同意（空间 cloud_allowed 归 owner，design D3/D4）；
- 空间模型设置只读排查视图：不代替 owner 选择（owner 侧端点在
  api/space_model_settings.py，家庭 listener）。

信任边界：
- 鉴权 ADMIN_JWT 独立签发域 + require_admin_ready 门禁（api/admin_deps）；
  家庭令牌在此一律 401；旧家庭挂载 /api/admin/agent 已删除（404）；
- AGENT_RUNTIME_ENABLED 关闭时全部 503（router 级门禁，admin_app 侧首个）；
- 写操作审计走 services/admin_audit.record_access（admin_access_audits，
  actor=system_admin；与领域事务同提交；filters 仅白名单字段，secret 永不
  入审计——admin_sanitizer 黑名单之外也不主动传密钥相关键）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.api.admin_deps import AdminPrincipal, require_admin_ready
from app.api.deps import get_db
from app.errors import (
    AGENT_PROVIDER_NOT_FOUND,
    AGENT_RUNTIME_DISABLED,
    SPACE_NOT_FOUND,
    VALIDATION_ERROR,
    raise_api_error,
)
from app.models.agent_provider import AgentPlatformDefault, AgentProvider, AgentSpaceProviderSetting
from app.models.space import FamilySpace
from app.schemas.agent import (
    AdminSpaceProviderSettingsOut,
    AgentPlatformDefaultsOut,
    AgentPlatformDefaultsRequest,
    AgentProviderCreateRequest,
    AgentProviderOut,
    AgentProviderPatchRequest,
    SpaceAgentSettingOut,
    SpaceModelSettingsKindsOut,
)
from app.services import admin_audit, agent_provider
from app.utils import secretbox, timeutil


def _require_runtime_enabled() -> None:
    if not config.AGENT_RUNTIME_ENABLED:
        raise_api_error(503, AGENT_RUNTIME_DISABLED, "Agent Runtime 未启用")


router = APIRouter(
    prefix="/admin-api/v1",
    tags=["admin-agent"],
    dependencies=[Depends(_require_runtime_enabled)],
)

_ENDPOINT = "/admin-api/v1/agent"


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


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


def _platform_defaults_out(row: AgentPlatformDefault | None) -> AgentPlatformDefaultsOut:
    """平台默认投影：provider/model 成对非空才视为已设置（孤 model 无效）。"""
    if row is None:
        return AgentPlatformDefaultsOut()
    assistant = (
        {"provider_id": row.assistant_provider_id, "model": row.assistant_model}
        if row.assistant_provider_id is not None and row.assistant_model
        else None
    )
    steward = (
        {"provider_id": row.steward_provider_id, "model": row.steward_model}
        if row.steward_provider_id is not None and row.steward_model
        else None
    )
    return AgentPlatformDefaultsOut(
        assistant=assistant, steward=steward, updated_at=row.updated_at
    )


# ---- Provider 注册表 ----


@router.post("/agent/providers", response_model=AgentProviderOut, status_code=201)
def register_provider(
    body: AgentProviderCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> AgentProviderOut:
    """注册 Provider；openai_compatible 必填 base_url；strict 门禁在生产生效。"""
    admin, _account = identity
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
    db.flush()
    admin_audit.record_access(
        db,
        action="agent.provider.create",
        endpoint=f"{_ENDPOINT}/providers",
        system_admin_id=admin.id,
        # admin_access_audits.target_type 词表仅 user/space（既有 CHECK 约束），
        # provider 主体以 filters.provider_id 表达
        filters={"provider_id": row.id, "name": row.name, "kind": row.kind, "enabled": row.enabled},
        ip=_client_ip(request),
    )
    db.commit()
    return _provider_out(row)


@router.get("/agent/providers", response_model=list[AgentProviderOut])
def list_providers(
    request: Request,
    db: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> list[AgentProviderOut]:
    admin, _account = identity
    rows = db.scalars(select(AgentProvider).order_by(AgentProvider.id.asc())).all()
    admin_audit.record_access(
        db,
        action="agent.provider.list",
        endpoint=f"{_ENDPOINT}/providers",
        system_admin_id=admin.id,
        result_count=len(rows),
        ip=_client_ip(request),
    )
    db.commit()
    return [_provider_out(r) for r in rows]


@router.patch("/agent/providers/{provider_id}", response_model=AgentProviderOut)
def update_provider(
    provider_id: int,
    body: AgentProviderPatchRequest,
    request: Request,
    db: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> AgentProviderOut:
    """部分更新（model_fields_set 区分未提供与显式 null）；secret 空串=清除、非空=轮换。"""
    admin, _account = identity
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
    admin_audit.record_access(
        db,
        action="agent.provider.update",
        endpoint=f"{_ENDPOINT}/providers/{row.id}",
        system_admin_id=admin.id,
        # target_type 词表仅 user/space：provider 主体以 filters.provider_id 表达
        filters={
            "provider_id": row.id,
            "fields": sorted(provided),
            **({"secret_rotated": True} if "secret" in provided and body.secret else {}),
        },
        ip=_client_ip(request),
    )
    db.commit()
    return _provider_out(row)


# ---- 平台默认模型 ----


@router.get("/agent/platform-defaults", response_model=AgentPlatformDefaultsOut)
def get_platform_defaults(
    request: Request,
    db: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> AgentPlatformDefaultsOut:
    admin, _account = identity
    row = db.get(AgentPlatformDefault, 1)
    admin_audit.record_access(
        db,
        action="agent.platform_defaults.read",
        endpoint=f"{_ENDPOINT}/platform-defaults",
        system_admin_id=admin.id,
        ip=_client_ip(request),
    )
    db.commit()
    return _platform_defaults_out(row)


@router.put("/agent/platform-defaults", response_model=AgentPlatformDefaultsOut)
def put_platform_defaults(
    body: AgentPlatformDefaultsRequest,
    request: Request,
    db: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> AgentPlatformDefaultsOut:
    """全量覆盖平台默认；成对校验在 schema 结构层，allowlist 校验在 service 层。"""
    admin, _account = identity
    row = agent_provider.set_platform_defaults(
        db,
        assistant=(body.assistant.provider_id, body.assistant.model)
        if body.assistant is not None
        else None,
        steward=(body.steward.provider_id, body.steward.model)
        if body.steward is not None
        else None,
        updated_by_admin_id=admin.id,
    )
    admin_audit.record_access(
        db,
        action="agent.platform_defaults.update",
        endpoint=f"{_ENDPOINT}/platform-defaults",
        system_admin_id=admin.id,
        # target_type 词表仅 user/space：维度默认以 filters 中的 provider_id 表达
        filters={
            "assistant_provider_id": body.assistant.provider_id if body.assistant else None,
            "steward_provider_id": body.steward.provider_id if body.steward else None,
        },
        ip=_client_ip(request),
    )
    db.commit()
    return _platform_defaults_out(row)


# ---- 空间设置只读排查视图 ----


@router.get(
    "/agent/spaces/{space_id}/provider-settings",
    response_model=AdminSpaceProviderSettingsOut,
)
def get_space_provider_settings(
    space_id: int,
    request: Request,
    db: Session = Depends(get_db),
    identity: AdminPrincipal = Depends(require_admin_ready),
) -> AdminSpaceProviderSettingsOut:
    """两 agent 维度的行级设置（原始存储态）+ 平台默认状态；只读排查用途。"""
    admin, _account = identity
    if db.get(FamilySpace, space_id) is None:
        raise_api_error(404, SPACE_NOT_FOUND, "空间不存在")
    rows = {
        row.agent_kind: row
        for row in db.scalars(
            select(AgentSpaceProviderSetting).where(
                AgentSpaceProviderSetting.space_id == space_id
            )
        )
    }
    admin_audit.record_access(
        db,
        action="agent.space_settings.read",
        endpoint=f"{_ENDPOINT}/spaces/{space_id}/provider-settings",
        system_admin_id=admin.id,
        target_type="space",
        target_id=space_id,
        ip=_client_ip(request),
    )
    db.commit()
    return AdminSpaceProviderSettingsOut(
        space_id=space_id,
        settings=SpaceModelSettingsKindsOut(
            assistant=_setting_out(rows.get("assistant")),
            steward=_setting_out(rows.get("steward")),
        ),
        platform_default=_platform_defaults_out(db.get(AgentPlatformDefault, 1)),
    )


def _setting_out(row: AgentSpaceProviderSetting | None) -> SpaceAgentSettingOut | None:
    if row is None:
        return None
    return SpaceAgentSettingOut(
        agent_kind=row.agent_kind,
        provider_id=row.provider_id,
        model=row.model,
        cloud_allowed=bool(row.cloud_allowed),
        local_required=bool(row.local_required),
        enabled=bool(row.enabled),
    )
