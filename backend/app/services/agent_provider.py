"""Provider 解析与 Policy 推导（RT-5；ProviderGateway 实调属后续 Block）。

本模块只做配置层解析：给定空间设置 + Provider 注册，推导 policy 结果：
- allowed                ：所选 Provider 可用且满足 cloud/local 约束，model 在 allowlist 内
- denied                 ：未配置 / 设置或 Provider 停用 / model 不在 allowlist
- denied_no_local        ：要求本地执行但解析不到可用本地 Provider（可解释拒绝）
- denied_cloud_forbidden ：空间未开放云端但所选为云 Provider

绝不返回密钥明文或密文；context 仅下发 secret_ref 供 sidecar 安全配置对账。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
from app.utils import secretbox

POLICY_ALLOWED = "allowed"
POLICY_DENIED = "denied"
POLICY_DENIED_NO_LOCAL = "denied_no_local"
POLICY_DENIED_CLOUD_FORBIDDEN = "denied_cloud_forbidden"


@dataclass(frozen=True)
class ProviderRuntime:
    """注入 sidecar 的运行期 Provider 配置（ProviderGateway 唯一解密出口）。

    凭据只经 internal listener 下发给已验签的 run token 持有者；此对象
    不得出现在浏览器 API、SSE、领域事件、审计或普通日志。base_url 必填
    （否则不可发起请求）；local Provider 允许无 api_key。
    """

    provider_id: int
    kind: str
    model: str
    base_url: str
    api_key: str | None


@dataclass(frozen=True)
class ProviderResolution:
    """context 端点下发给 sidecar 的解析结果（无任何密钥材料）。

    reason 为 denied 时的可解释原因（RT-5：本地要求不可用必须可解释拒绝，
    绝不静默换云）；additive 字段，内部合同向后兼容。
    """

    provider_id: int | None
    model: str | None
    kind: str | None
    policy_result: str
    secret_ref: str | None
    reason: str | None = None


def resolve_for_space(db: Session, space_id: int) -> ProviderResolution:
    """按空间解析 Provider 与策略；永不抛错——不可用一律以可解释 denied 表达。

    space 显式选择优先；无任何可用配置 → POLICY_DENIED（reason 说明缺口），
    绝不枚举其他 Provider 替补（无静默 fallback）。
    """
    setting = db.scalar(
        select(AgentSpaceProviderSetting).where(AgentSpaceProviderSetting.space_id == space_id)
    )
    if setting is None:
        return ProviderResolution(None, None, None, POLICY_DENIED, None, "no_space_setting")
    if not setting.enabled:
        return ProviderResolution(None, None, None, POLICY_DENIED, None, "setting_disabled")
    provider = db.get(AgentProvider, setting.provider_id)
    if provider is None:
        return ProviderResolution(
            setting.provider_id, None, None, POLICY_DENIED, None, "provider_missing"
        )
    if not provider.enabled:
        return ProviderResolution(
            setting.provider_id, None, None, POLICY_DENIED, None, "provider_disabled"
        )
    allowed_models = list(provider.allowed_models_json or [])
    if setting.model not in allowed_models:
        return ProviderResolution(
            provider.id, None, provider.kind, POLICY_DENIED, None, "model_not_allowed"
        )
    if provider.kind == "local":
        # 本地 Provider：满足 local_required；cloud_allowed 不约束本地模型
        return _allowed(provider, setting.model)
    # openai_compatible 云 Provider
    if setting.local_required:
        # 要求本地却选中云 Provider：视为本地不可用的可解释拒绝（绝不换选本地替补）
        return ProviderResolution(
            provider.id,
            None,
            provider.kind,
            POLICY_DENIED_NO_LOCAL,
            None,
            "selected_provider_not_local",
        )
    if not setting.cloud_allowed:
        return ProviderResolution(
            provider.id,
            None,
            provider.kind,
            POLICY_DENIED_CLOUD_FORBIDDEN,
            None,
            "cloud_not_allowed",
        )
    return _allowed(provider, setting.model)


def _allowed(provider: AgentProvider, model: str) -> ProviderResolution:
    return ProviderResolution(
        provider_id=provider.id,
        model=model,
        kind=provider.kind,
        policy_result=POLICY_ALLOWED,
        secret_ref=f"agent_providers/{provider.id}/secret",
    )


def find_local_provider(db: Session) -> AgentProvider | None:
    """是否存在已启用的本地 Provider（local_required 且空间未选云时的解释依据）。"""
    return db.scalar(
        select(AgentProvider).where(AgentProvider.kind == "local", AgentProvider.enabled.is_(True))
    )


def resolve_runtime(db: Session, space_id: int) -> ProviderRuntime | None:
    """把空间级 Provider 解析为可注入 sidecar 的运行期配置（含解密凭据）。

    权威链：DB Provider 注册 → 空间选择的 policy（allowed）→ secretbox 解密。
    policy 非 allowed、provider/model 缺失或 base_url 为空一律返回 None（fail-closed），
    调用方将其映射为可解释拒绝，绝不回退到 sidecar 环境变量。
    """
    resolution = resolve_for_space(db, space_id)
    if (
        resolution.policy_result != POLICY_ALLOWED
        or resolution.provider_id is None
        or resolution.model is None
    ):
        return None
    provider = db.get(AgentProvider, resolution.provider_id)
    if provider is None:
        return None
    base_url = (provider.base_url or "").strip()
    if not base_url:
        return None
    api_key: str | None = None
    if provider.secret_ciphertext:
        try:
            api_key = secretbox.decrypt_secret(provider.secret_ciphertext)
        except secretbox.SecretBoxError:
            # 密文轮换/损坏必须拒绝，绝不静默无凭据调用
            return None
    return ProviderRuntime(
        provider_id=provider.id,
        kind=provider.kind,
        model=resolution.model,
        base_url=base_url,
        api_key=api_key,
    )
