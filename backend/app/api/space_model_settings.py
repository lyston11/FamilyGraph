"""家庭域空间模型设置端点（owner 侧；仅家庭 listener，前缀 /api）。

职责边界（09-06 治理迁移 D3/D5）：空间 owner（active space_admin）为本空间的
assistant/steward 分别选择模型与云同意；系统管理员只维护 Provider 注册表与
平台默认（api/admin_agent.py，仅 admin_app），绝不替 owner 打开云同意。

- 权限依赖与 PATCH /spaces/{space_id} 同款（commands/spaces._require_space_manager
  语义）：空间不存在 404 SPACE_NOT_FOUND，非当前空间管理员 403
  SPACE_FORBIDDEN_ACTOR；platform_operator 不因平台角色获得任何空间写权限；
- PUT 幂等 upsert 单维度行：enabled=true 必须成对给 provider_id+model 且
  model ∈ Provider allowlist；enabled=false 显式停用（provider/model 可空）；
  DELETE 删行 = 恢复平台默认继承（解析回退链在 services/agent_provider）；
- 目录与平台默认投影只含 enabled Provider 与解析同口径的有效默认，
  无任何密钥形态字段（secret 只存在于 agent_providers 密文列）；
- 写操作审计落家庭 audit_log（actor=space owner 的 user id）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_authenticated_user
from app.errors import (
    SPACE_FORBIDDEN_ACTOR,
    SPACE_NOT_FOUND,
    VALIDATION_ERROR,
    raise_api_error,
)
from app.models.account import Account
from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
from app.models.space import FamilySpace
from app.models.user import User
from app.schemas.agent import (
    AgentModelCatalogEntryOut,
    AgentPlatformDefaultsOut,
    AgentSpaceModelSettingsRequest,
    SpaceAgentSettingOut,
    SpaceModelSettingsKindsOut,
    SpaceModelSettingsOut,
)
from app.services import agent_provider, audit, space_fsm

router = APIRouter(tags=["space-model-settings"])

AGENT_MODEL_KINDS = agent_provider.AGENT_KINDS


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _require_space_manager(session: Session, space_id: int, user_id: int) -> FamilySpace:
    """与 commands/spaces._require_space_manager 同语义：不存在 404、非管理员 403。"""
    space = session.get(FamilySpace, space_id)
    if space is None:
        raise_api_error(404, SPACE_NOT_FOUND, "家庭空间不存在")
    if not space_fsm.is_space_manager(session, space_id, user_id):
        raise_api_error(403, SPACE_FORBIDDEN_ACTOR, "仅当前空间管理员可执行该操作")
    return space


def _setting_row(
    session: Session, space_id: int, agent_kind: str
) -> AgentSpaceProviderSetting | None:
    return session.scalar(
        select(AgentSpaceProviderSetting).where(
            AgentSpaceProviderSetting.space_id == space_id,
            AgentSpaceProviderSetting.agent_kind == agent_kind,
        )
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


def _catalog_out(session: Session) -> list[AgentModelCatalogEntryOut]:
    """管理员允许目录：仅 enabled Provider；名称/接口/allowlist，无密钥字段。"""
    rows = session.scalars(
        select(AgentProvider)
        .where(AgentProvider.enabled.is_(True))
        .order_by(AgentProvider.id.asc())
    ).all()
    return [
        AgentModelCatalogEntryOut(
            provider_id=row.id,
            name=row.name,
            kind=row.kind,
            api=row.api,
            models=list(row.allowed_models_json or []),
        )
        for row in rows
    ]


def _valid_platform_defaults_out(session: Session) -> AgentPlatformDefaultsOut:
    """解析同口径的有效平台默认（provider 存在且 enabled）；孤 model 不外露。"""
    pairs = {
        kind: agent_provider.valid_platform_default(session, kind) for kind in AGENT_MODEL_KINDS
    }
    return AgentPlatformDefaultsOut(
        assistant={"provider_id": pairs["assistant"][0], "model": pairs["assistant"][1]}
        if pairs["assistant"] is not None
        else None,
        steward={"provider_id": pairs["steward"][0], "model": pairs["steward"][1]}
        if pairs["steward"] is not None
        else None,
    )


def _validate_selection(session: Session, provider_id: int, model: str) -> AgentProvider:
    """选择校验：Provider 存在且 enabled、model 在 allowlist 内（fail-closed 422）。"""
    provider = session.get(AgentProvider, provider_id)
    if provider is None or not provider.enabled:
        raise_api_error(
            422, VALIDATION_ERROR, "Provider 不存在或未启用", {"provider_id": provider_id}
        )
    if model not in list(provider.allowed_models_json or []):
        raise_api_error(
            422,
            VALIDATION_ERROR,
            "model 不在该 Provider 的 allowed_models 内",
            {"allowed_models": list(provider.allowed_models_json or [])},
        )
    return provider


@router.get("/spaces/{space_id}/model-settings", response_model=SpaceModelSettingsOut)
def get_space_model_settings(
    space_id: int,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceModelSettingsOut:
    """模型设置视图：两 agent 维度行级设置 + 允许目录 + 有效平台默认。"""
    actor, _account = identity
    _require_space_manager(session, space_id, actor.id)
    return SpaceModelSettingsOut(
        space_id=space_id,
        settings=SpaceModelSettingsKindsOut(
            assistant=_setting_out(_setting_row(session, space_id, "assistant")),
            steward=_setting_out(_setting_row(session, space_id, "steward")),
        ),
        catalog=_catalog_out(session),
        platform_default=_valid_platform_defaults_out(session),
    )


@router.put("/spaces/{space_id}/model-settings", response_model=SpaceAgentSettingOut)
def put_space_model_settings(
    space_id: int,
    body: AgentSpaceModelSettingsRequest,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> SpaceAgentSettingOut:
    """单维度幂等 upsert：enabled=true 成对必填；enabled=false 显式停用（可空）。"""
    actor, _account = identity
    _require_space_manager(session, space_id, actor.id)

    if body.enabled:
        if body.provider_id is None or not body.model:
            # 继承平台默认请用 DELETE；显式停用请用 enabled=false
            raise_api_error(
                422, VALIDATION_ERROR, "选择模型时必须同时指定 provider_id 与 model"
            )
        provider = _validate_selection(session, body.provider_id, body.model)
        new_provider_id: int | None = provider.id
        new_model: str | None = body.model
    else:
        # 显式停用：provider/model 可空；若提供则必须成对（允许带着选择停用）
        if (body.provider_id is None) != (body.model is None):
            raise_api_error(422, VALIDATION_ERROR, "provider_id 与 model 必须成对提供")
        if body.provider_id is not None and body.model is not None:
            _validate_selection(session, body.provider_id, body.model)
        new_provider_id = body.provider_id
        new_model = body.model

    row = _setting_row(session, space_id, body.agent_kind)
    if row is None:
        row = AgentSpaceProviderSetting(space_id=space_id, agent_kind=body.agent_kind)
        session.add(row)
    row.provider_id = new_provider_id
    row.model = new_model
    row.cloud_allowed = body.cloud_allowed
    row.local_required = body.local_required
    row.enabled = body.enabled
    session.commit()
    audit.write_audit(
        session,
        action="space_model_settings_updated",
        actor_id=actor.id,
        target_id=space_id,
        ip=_client_ip(request),
        detail={
            "agent_kind": row.agent_kind,
            "provider_id": row.provider_id,
            "model": row.model,
            "cloud_allowed": row.cloud_allowed,
            "local_required": row.local_required,
            "enabled": row.enabled,
        },
    )
    session.commit()
    return _setting_out(row)


@router.delete("/spaces/{space_id}/model-settings/{agent_kind}", status_code=204)
def delete_space_model_setting(
    space_id: int,
    agent_kind: str,
    request: Request,
    session: Session = Depends(get_db),
    identity: tuple[User, Account] = Depends(require_authenticated_user),
) -> Response:
    """删行 = 恢复平台默认继承（幂等；无行同样 204）。"""
    actor, _account = identity
    _require_space_manager(session, space_id, actor.id)
    if agent_kind not in AGENT_MODEL_KINDS:
        raise_api_error(422, VALIDATION_ERROR, "未知的 agent 维度", {"agent_kind": agent_kind})
    row = _setting_row(session, space_id, agent_kind)
    if row is not None:
        session.delete(row)
    session.commit()
    audit.write_audit(
        session,
        action="space_model_settings_reset",
        actor_id=actor.id,
        target_id=space_id,
        ip=_client_ip(request),
        detail={"agent_kind": agent_kind},
    )
    session.commit()
    return Response(status_code=204)
