"""Provider 解析与 Policy 推导（RT-5；ProviderGateway 使用本模块的结果）。

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

from app import config
from app.models.agent import AgentRun
from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
from app.utils import secretbox

POLICY_ALLOWED = "allowed"
POLICY_DENIED = "denied"
POLICY_DENIED_NO_LOCAL = "denied_no_local"
POLICY_DENIED_CLOUD_FORBIDDEN = "denied_cloud_forbidden"

# Canonical cloud profile copied from the developer's local Pi
# ``~/.pi/agent/models.json``. Keep this metadata non-secret; credentials are
# supplied separately through the admin API and encrypted at rest.
STANDARD_PROVIDER_NAME = "liu-dada"
STANDARD_MODEL = "gpt-5.6-sol"
STANDARD_API = "openai-responses"
STANDARD_BASE_URL = "https://api.liu-dada.com/v1"
STANDARD_CONTEXT_WINDOW = 272_000
STANDARD_MAX_TOKENS = 60_000
STANDARD_REASONING = True
STANDARD_INPUT_MODALITIES = ("text", "image")
STANDARD_THINKING_LEVELS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ProviderRuntime:
    """注入 sidecar 的运行期 Provider 配置（ProviderGateway 唯一解密出口）。

    凭据只经 internal listener 下发给已验签的 run token 持有者；此对象
    不得出现在浏览器 API、SSE、领域事件、审计或普通日志。base_url 必填
    （否则不可发起请求）；local Provider 允许无 api_key。
    """

    provider_id: int
    provider_name: str
    kind: str
    model: str
    api: str
    compat: dict[str, object]
    context_window: int
    max_tokens: int
    reasoning: bool
    input_modalities: list[str]
    thinking_levels: list[str]
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
    api: str | None
    compat: dict[str, object]
    context_window: int | None
    max_tokens: int | None
    reasoning: bool | None
    input_modalities: list[str]
    thinking_levels: list[str]
    policy_result: str
    secret_ref: str | None
    reason: str | None = None
    provider_name: str | None = None


def provider_profile_error(provider: AgentProvider, model: str | None = None) -> str | None:
    """Return a stable reason when a cloud row is not the approved Pi profile.

    Local providers remain supported as the optional sensitive-data fallback.
    The standard-profile switch is checked at registration and resolution so
    manually-mutated rows fail closed too.
    """
    # Cloud inference is intentionally pinned to the same profile used by the
    # local Pi installation.  There is no runtime environment escape hatch:
    # relaxing this check would let a deployment silently route user data to an
    # unreviewed endpoint/model.  Tests that need synthetic rows may
    # monkeypatch the module constant explicitly; production config remains
    # fail-closed.
    if provider.kind == "local" or not config.AGENT_PROVIDER_STANDARD_PROFILE_ONLY:
        return None
    if provider.name != STANDARD_PROVIDER_NAME:
        return "provider_name_not_allowed"
    if provider.api != STANDARD_API:
        return "provider_api_not_allowed"
    if (provider.base_url or "").rstrip("/") != STANDARD_BASE_URL:
        return "provider_base_url_not_allowed"
    if model is not None and model != STANDARD_MODEL:
        return "provider_model_not_allowed"
    if list(provider.allowed_models_json or []) != [STANDARD_MODEL]:
        return "provider_model_allowlist_not_allowed"
    if provider.context_window != STANDARD_CONTEXT_WINDOW:
        return "provider_context_window_not_allowed"
    if provider.max_tokens != STANDARD_MAX_TOKENS:
        return "provider_max_tokens_not_allowed"
    if bool(provider.reasoning) != STANDARD_REASONING:
        return "provider_reasoning_not_allowed"
    if tuple(provider.input_modalities_json or []) != STANDARD_INPUT_MODALITIES:
        return "provider_input_modalities_not_allowed"
    if tuple(provider.thinking_levels_json or []) != STANDARD_THINKING_LEVELS:
        return "provider_thinking_levels_not_allowed"
    if dict(provider.compat_json or {}):
        return "provider_compat_not_allowed"
    return None


def resolve_for_space(db: Session, space_id: int) -> ProviderResolution:
    """按空间解析 Provider 与策略；永不抛错——不可用一律以可解释 denied 表达。

    space 显式选择优先；无任何可用配置 → POLICY_DENIED（reason 说明缺口），
    绝不枚举其他 Provider 替补（无静默 fallback）。
    """
    setting = db.scalar(
        select(AgentSpaceProviderSetting).where(AgentSpaceProviderSetting.space_id == space_id)
    )
    if setting is None:
        return ProviderResolution(
            None,
            None,
            None,
            None,
            {},
            None,
            None,
            None,
            [],
            [],
            POLICY_DENIED,
            None,
            "no_space_setting",
        )
    if not setting.enabled:
        return ProviderResolution(
            None,
            None,
            None,
            None,
            {},
            None,
            None,
            None,
            [],
            [],
            POLICY_DENIED,
            None,
            "setting_disabled",
        )
    provider = db.get(AgentProvider, setting.provider_id)
    if provider is None:
        return ProviderResolution(
            setting.provider_id,
            None,
            None,
            None,
            {},
            None,
            None,
            None,
            [],
            [],
            POLICY_DENIED,
            None,
            "provider_missing",
        )
    if not provider.enabled:
        return ProviderResolution(
            setting.provider_id,
            None,
            None,
            None,
            {},
            None,
            None,
            None,
            [],
            [],
            POLICY_DENIED,
            None,
            "provider_disabled",
        )
    profile_error = provider_profile_error(provider, setting.model)
    if profile_error is not None:
        return ProviderResolution(
            provider.id,
            None,
            provider.kind,
            provider.api,
            dict(provider.compat_json or {}),
            provider.context_window,
            provider.max_tokens,
            provider.reasoning,
            list(provider.input_modalities_json or []),
            list(provider.thinking_levels_json or []),
            POLICY_DENIED,
            None,
            profile_error,
            provider.name,
        )
    allowed_models = list(provider.allowed_models_json or [])
    if setting.model not in allowed_models:
        return ProviderResolution(
            provider.id,
            None,
            provider.kind,
            provider.api,
            dict(provider.compat_json or {}),
            provider.context_window,
            provider.max_tokens,
            provider.reasoning,
            list(provider.input_modalities_json or []),
            list(provider.thinking_levels_json or []),
            POLICY_DENIED,
            None,
            "model_not_allowed",
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
            provider.api,
            dict(provider.compat_json or {}),
            provider.context_window,
            provider.max_tokens,
            provider.reasoning,
            list(provider.input_modalities_json or []),
            list(provider.thinking_levels_json or []),
            POLICY_DENIED_NO_LOCAL,
            None,
            "selected_provider_not_local",
        )
    if not setting.cloud_allowed:
        return ProviderResolution(
            provider.id,
            None,
            provider.kind,
            provider.api,
            dict(provider.compat_json or {}),
            provider.context_window,
            provider.max_tokens,
            provider.reasoning,
            list(provider.input_modalities_json or []),
            list(provider.thinking_levels_json or []),
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
        api=provider.api,
        compat=dict(provider.compat_json or {}),
        context_window=provider.context_window,
        max_tokens=provider.max_tokens,
        reasoning=bool(provider.reasoning),
        input_modalities=list(provider.input_modalities_json or []),
        thinking_levels=list(provider.thinking_levels_json or []),
        policy_result=POLICY_ALLOWED,
        secret_ref=f"agent_providers/{provider.id}/secret",
        provider_name=provider.name,
    )


def snapshot_for_space(db: Session, space_id: int) -> dict[str, object]:
    """Capture non-secret provider metadata for an AgentRun.

    A denied profile is captured too, so a run cannot become executable merely
    because an operator changes the space setting after enqueue.
    """
    resolution = resolve_for_space(db, space_id)
    provider_revision: str | None = None
    if resolution.provider_id is not None:
        provider = db.get(AgentProvider, resolution.provider_id)
        if provider is not None:
            # updated_at is the server-side configuration revision.  It lets a
            # leased run fail closed if an operator rotates the endpoint,
            # adapter, compatibility flags or credentials mid-run.
            provider_revision = provider.updated_at.isoformat()
    return {
        "provider_id": resolution.provider_id,
        "model": resolution.model,
        "kind": resolution.kind,
        "api": resolution.api,
        "compat": dict(resolution.compat),
        "context_window": resolution.context_window,
        "max_tokens": resolution.max_tokens,
        "reasoning": resolution.reasoning,
        "input_modalities": list(resolution.input_modalities),
        "thinking_levels": list(resolution.thinking_levels),
        "policy_result": resolution.policy_result,
        "provider_revision": provider_revision,
        "provider_name": resolution.provider_name,
    }


def resolve_for_run(db: Session, run: AgentRun, space_id: int) -> ProviderResolution:
    """Resolve current authorization while pinning model metadata to run snapshot."""
    current = resolve_for_space(db, space_id)
    snapshot = run.runtime_snapshot_json
    if not snapshot:
        return current
    # A denied decision is immutable for the lifetime of a queued Run.  If an
    # operator later enables a provider or changes cloud/local policy, this Run
    # must not be "revived" into an executable one.  Legacy snapshots without
    # a concrete provider id are the only exception handled below.
    snapshot_provider_id = snapshot.get("provider_id")
    snapshot_policy = snapshot.get("policy_result")
    # Modern snapshots always include policy_result, including the
    # no-provider/disabled cases where provider_id is null.  Any such denied
    # decision is immutable for the queued run and must not be revived by a
    # later space configuration change.  Only genuinely legacy snapshots that
    # predate this field may adopt a newly allowed provider.
    if "policy_result" in snapshot and snapshot_policy != POLICY_ALLOWED:
        denied_policy = (
            snapshot_policy
            if snapshot_policy
            in {
                POLICY_DENIED,
                POLICY_DENIED_NO_LOCAL,
                POLICY_DENIED_CLOUD_FORBIDDEN,
            }
            else POLICY_DENIED
        )
        return ProviderResolution(
            int(snapshot_provider_id)
            if isinstance(snapshot_provider_id, int) and not isinstance(snapshot_provider_id, bool)
            else None,
            None,
            str(snapshot.get("kind")) if snapshot.get("kind") is not None else None,
            str(snapshot.get("api")) if snapshot.get("api") is not None else None,
            dict(snapshot.get("compat") or {}),
            int(snapshot["context_window"])
            if isinstance(snapshot.get("context_window"), int)
            and not isinstance(snapshot.get("context_window"), bool)
            else None,
            int(snapshot["max_tokens"])
            if isinstance(snapshot.get("max_tokens"), int)
            and not isinstance(snapshot.get("max_tokens"), bool)
            else None,
            bool(snapshot["reasoning"]) if isinstance(snapshot.get("reasoning"), bool) else None,
            list(snapshot.get("input_modalities") or []),
            list(snapshot.get("thinking_levels") or []),
            denied_policy,
            None,
            "runtime_snapshot_policy_denied",
            str(snapshot.get("provider_name"))
            if snapshot.get("provider_name") is not None
            else None,
        )
    if (
        "policy_result" not in snapshot
        and snapshot.get("provider_id") is None
        and current.policy_result == POLICY_ALLOWED
    ):
        # Legacy queued rows created before a provider was configured did not
        # have enough metadata to pin.  Adopt the now-allowed profile; all
        # modern rows capture a concrete provider id and remain immutable.
        return current
    if current.policy_result != POLICY_ALLOWED:
        return current
    # Allowed snapshots are an immutable protocol contract too.  Malformed
    # JSON must never be coerced into a runnable profile.
    if (
        not (isinstance(snapshot_provider_id, int) and not isinstance(snapshot_provider_id, bool))
        or not isinstance(snapshot.get("model"), str)
        or not snapshot.get("model")
        or not isinstance(snapshot.get("api"), str)
        or snapshot.get("api") not in ("openai-completions", "openai-responses")
        or not isinstance(snapshot.get("kind"), str)
        or snapshot.get("kind") not in ("openai_compatible", "local")
        or not isinstance(snapshot.get("context_window"), int)
        or isinstance(snapshot.get("context_window"), bool)
        or not isinstance(snapshot.get("max_tokens"), int)
        or isinstance(snapshot.get("max_tokens"), bool)
        or not isinstance(snapshot.get("reasoning"), bool)
        or not isinstance(snapshot.get("compat", {}), dict)
        or not isinstance(snapshot.get("input_modalities", []), list)
        or not all(isinstance(item, str) for item in snapshot.get("input_modalities", []))
        or not isinstance(snapshot.get("thinking_levels", []), list)
        or not all(isinstance(item, str) for item in snapshot.get("thinking_levels", []))
        or not isinstance(snapshot.get("provider_revision"), str)
        or not snapshot.get("provider_revision")
    ):
        return ProviderResolution(
            current.provider_id,
            None,
            current.kind,
            current.api,
            dict(current.compat),
            current.context_window,
            current.max_tokens,
            current.reasoning,
            list(current.input_modalities),
            list(current.thinking_levels),
            POLICY_DENIED,
            None,
            "runtime_snapshot_invalid",
            current.provider_name,
        )
    provider = (
        db.get(AgentProvider, current.provider_id) if current.provider_id is not None else None
    )
    snapshot_revision = snapshot.get("provider_revision")
    if (
        provider is not None
        and isinstance(snapshot_revision, str)
        and provider.updated_at.isoformat() != snapshot_revision
    ):
        return ProviderResolution(
            current.provider_id,
            None,
            current.kind,
            current.api,
            dict(current.compat),
            current.context_window,
            current.max_tokens,
            current.reasoning,
            list(current.input_modalities),
            list(current.thinking_levels),
            POLICY_DENIED,
            None,
            "runtime_snapshot_mismatch",
            current.provider_name,
        )
    if (
        current.provider_id != snapshot.get("provider_id")
        or current.model != snapshot.get("model")
        or current.api != snapshot.get("api")
        or current.kind != snapshot.get("kind")
        or current.provider_name != snapshot.get("provider_name")
        or dict(current.compat) != dict(snapshot.get("compat") or {})
        or current.context_window != snapshot.get("context_window")
        or current.max_tokens != snapshot.get("max_tokens")
        or current.reasoning != snapshot.get("reasoning")
        or list(current.input_modalities) != list(snapshot.get("input_modalities") or [])
        or list(current.thinking_levels) != list(snapshot.get("thinking_levels") or [])
    ):
        return ProviderResolution(
            current.provider_id,
            None,
            current.kind,
            current.api,
            dict(current.compat),
            current.context_window,
            current.max_tokens,
            current.reasoning,
            list(current.input_modalities),
            list(current.thinking_levels),
            POLICY_DENIED,
            None,
            "runtime_snapshot_mismatch",
            current.provider_name,
        )
    return ProviderResolution(
        current.provider_id,
        current.model,
        current.kind,
        str(snapshot.get("api") or current.api),
        dict(snapshot.get("compat") or current.compat),
        int(snapshot.get("context_window") or current.context_window or 272_000),
        int(snapshot.get("max_tokens") or current.max_tokens or 60_000),
        bool(snapshot.get("reasoning", current.reasoning)),
        list(snapshot.get("input_modalities") or current.input_modalities),
        list(snapshot.get("thinking_levels") or current.thinking_levels),
        current.policy_result,
        current.secret_ref,
        current.reason,
        current.provider_name,
    )


def find_local_provider(db: Session) -> AgentProvider | None:
    """是否存在已启用的本地 Provider（local_required 且空间未选云时的解释依据）。"""
    return db.scalar(
        select(AgentProvider).where(AgentProvider.kind == "local", AgentProvider.enabled.is_(True))
    )


def resolve_runtime(
    db: Session, space_id: int, *, run: AgentRun | None = None
) -> ProviderRuntime | None:
    """把空间级 Provider 解析为可注入 sidecar 的运行期配置（含解密凭据）。

    权威链：DB Provider 注册 → 空间选择的 policy（allowed）→ secretbox 解密。
    policy 非 allowed、provider/model 缺失或 base_url 为空一律返回 None（fail-closed），
    调用方将其映射为可解释拒绝，绝不回退到 sidecar 环境变量。
    """
    resolution = (
        resolve_for_run(db, run, space_id) if run is not None else resolve_for_space(db, space_id)
    )
    if (
        resolution.policy_result != POLICY_ALLOWED
        or resolution.provider_id is None
        or resolution.model is None
    ):
        return None
    provider = db.get(AgentProvider, resolution.provider_id)
    if provider is None:
        return None
    if provider_profile_error(provider, resolution.model) is not None:
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
    elif provider.kind != "local":
        # Cloud requests without a credential are never sent anonymously.
        return None
    return ProviderRuntime(
        provider_id=provider.id,
        provider_name=provider.name,
        kind=provider.kind,
        model=resolution.model,
        # A missing adapter is not a license to silently downgrade the
        # Responses profile.  Current rows always carry api; legacy/malformed
        # rows fail closed at profile resolution before reaching this fallback.
        api=resolution.api or STANDARD_API,
        compat=dict(resolution.compat),
        context_window=resolution.context_window or 272_000,
        max_tokens=resolution.max_tokens or 60_000,
        reasoning=bool(resolution.reasoning),
        input_modalities=list(resolution.input_modalities),
        thinking_levels=list(resolution.thinking_levels),
        base_url=base_url,
        api_key=api_key,
    )
