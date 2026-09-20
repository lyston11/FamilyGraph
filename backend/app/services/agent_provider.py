"""Provider 解析与 Policy 推导（RT-5；ProviderGateway 使用本模块的结果）。

本模块只做配置层解析：给定空间设置 + Provider 注册，推导 policy 结果：
- allowed                ：所选 Provider 可用且满足 cloud/local 约束，model 在 allowlist 内
- denied                 ：未配置 / 设置或 Provider 停用 / model 不在 allowlist
- denied_no_local        ：要求本地执行但解析不到可用本地 Provider（可解释拒绝）
- denied_cloud_forbidden ：空间未开放云端但所选为云 Provider

解析按 agent 维度（assistant|steward）独立进行：空间显式设置优先，无空间行时
回退平台默认单行表（agent_platform_defaults，仅决定通道与档位；云同意仍归空间），
均无 → POLICY_DENIED（no_space_setting）。绝不枚举其他 Provider 替补
（无静默 fallback）。

绝不返回密钥明文或密文；context 仅下发 secret_ref 供 sidecar 安全配置对账。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import VALIDATION_ERROR, raise_api_error
from app.models.agent import AgentRun
from app.models.agent_provider import (
    AgentPlatformDefault,
    AgentProvider,
    AgentSpaceProviderSetting,
)
from app.utils import secretbox, timeutil

POLICY_ALLOWED = "allowed"
POLICY_DENIED = "denied"
POLICY_DENIED_NO_LOCAL = "denied_no_local"
POLICY_DENIED_CLOUD_FORBIDDEN = "denied_cloud_forbidden"

# Provider 设置的 Agent 维度（09-06 治理迁移）：assistant/steward 各自独立的
# 空间选择与平台默认。与 policy_consumer 同维；RuntimeAgentKind 是 generic
# runtime 的 assistant-only 常量（09-01 决策），不用于本配置维度。
AGENT_KIND_ASSISTANT = "assistant"
AGENT_KIND_STEWARD = "steward"
AGENT_KINDS: tuple[str, ...] = (AGENT_KIND_ASSISTANT, AGENT_KIND_STEWARD)

# Provider 协议白名单：只有这两个 OpenAI 兼容适配器存在（sidecar 与网关同源）。
# 这里刻意不再包含任何具体供应商名/模型名/端点：平台不绑定单一上游，
# 官方与第三方只要提供 /responses 或 /chat/completions 即可注册使用。
SUPPORTED_APIS: tuple[str, ...] = ("openai-completions", "openai-responses")


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
    # additive（09-06）：本次解析是否来自平台默认回退（无空间显式行时为 True），
    # 供 PROVIDER_UNRESOLVED detail 区分「通道未配置」与「空间未选/未同意云」。
    platform_default_configured: bool = False


def provider_profile_error(provider: AgentProvider, model: str | None = None) -> str | None:
    """结构性校验：Provider 行是否具备可安全出站的最小完整信息。

    这里**刻意不再比对任何具体供应商名、模型名或端点**。早期版本把云 Provider
    硬钉死在一个受控 profile 上（name/model/base_url/协议/元数据逐字段相等），
    带来两个实际问题：上游停服或变更协议即无法切换；无法接入自有或私网内的
    兼容端点。

    那层白名单原本想控制的是「用户数据可以发到哪里」。该职责现在由空间级
    ``cloud_allowed``（空间所有者显式同意云执行，见 ``resolve_for_space``）
    承担 —— 这是更合适的归属：数据出不出本机是使用者的决定，不是「是不是
    某个特定供应商」的属性。

    保留的校验仍然是安全控制，不可放宽：
    - 云 Provider 必须有可解析的 http/https 绝对 URL（否则不知道该连哪里，
      相对路径或空值只会变成难以诊断的运行期失败）；
    - 协议必须是被支持的 OpenAI 适配器之一；
    - allowlist 非空，且空间选中的 model 必须在其中（防止越权模型名一路
      走到出站）。

    ``kind=local`` 不做这些要求：本机端点的协议地址由运行环境决定。
    """
    if provider.kind == "local":
        # 本机端点：满足 local_required；cloud_allowed 不约束本地模型。
        # 注意："local" 描述的是端点可达性，**不是**「数据不出网」的承诺 ——
        # 本机网关完全可能把请求转发给外部厂商。数据出网由 cloud_allowed 控制。
        return None
    if provider.kind != "openai_compatible":
        return "provider_kind_not_allowed"
    if provider.api not in SUPPORTED_APIS:
        return "provider_api_not_allowed"
    base_url = (provider.base_url or "").strip()
    if not base_url:
        return "provider_base_url_required"
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "provider_base_url_invalid"
    allowed_models = list(provider.allowed_models_json or [])
    if not allowed_models:
        return "provider_model_allowlist_empty"
    if model is not None and model not in allowed_models:
        return "model_not_allowed"
    return None


def resolve_for_space(
    db: Session, space_id: int, agent_kind: str = AGENT_KIND_ASSISTANT
) -> ProviderResolution:
    """按空间 + agent 维度解析 Provider 与策略；不可用一律以可解释 denied 表达。

    agent_kind 仅接受 assistant|steward（未知值 fail-closed 422）。解析顺序 =
    空间显式设置 → 平台默认（owner 未显式选择时的通道回退；云同意仍归空间，
    云默认 Provider 在 owner 同意云前 denied_cloud_forbidden）→ POLICY_DENIED
    （no_space_setting）。绝不枚举其他 Provider 替补（无静默 fallback）。
    """
    if agent_kind not in AGENT_KINDS:
        raise_api_error(422, VALIDATION_ERROR, "未知的 agent 维度", {"agent_kind": agent_kind})
    setting = db.scalar(
        select(AgentSpaceProviderSetting).where(
            AgentSpaceProviderSetting.space_id == space_id,
            AgentSpaceProviderSetting.agent_kind == agent_kind,
        )
    )
    if setting is None:
        default = _valid_platform_default(db, agent_kind)
        if default is None:
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
        # 平台默认回退：构造虚拟 setting 走既有判定链。cloud_allowed=False /
        # local_required=False（平台默认只决定通道与档位，不替 owner 打开云同意）。
        provider_id, model = default
        setting = AgentSpaceProviderSetting(
            space_id=space_id,
            agent_kind=agent_kind,
            provider_id=provider_id,
            model=model,
            cloud_allowed=False,
            local_required=False,
            enabled=True,
        )
        return _resolve_setting(db, setting, platform_default_configured=True)
    return _resolve_setting(db, setting, platform_default_configured=False)


def _resolve_setting(
    db: Session, setting: AgentSpaceProviderSetting, *, platform_default_configured: bool
) -> ProviderResolution:
    """对已存在的（显式或虚拟）setting 走既有判定链；policy 结果永不落库。"""
    if not setting.enabled:
        # owner 显式停用：优先于平台默认，绝不回退（reason=setting_disabled）
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
            None,
            platform_default_configured,
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
            None,
            platform_default_configured,
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
            None,
            platform_default_configured,
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
            platform_default_configured,
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
            None,
            platform_default_configured,
        )
    if provider.kind == "local":
        # 本地 Provider：满足 local_required；cloud_allowed 不约束本地模型
        return _allowed(provider, setting.model, platform_default_configured)
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
            None,
            platform_default_configured,
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
            None,
            platform_default_configured,
        )
    return _allowed(provider, setting.model, platform_default_configured)


def _valid_platform_default(db: Session, agent_kind: str) -> tuple[int, str] | None:
    """读取该 agent 维度的平台默认；provider 存在且 enabled 才视为有效默认。

    Provider 删除（SET NULL）或停用留孤 model → 视为该维度无默认，解析回退到
    no_space_setting，绝不静默改选其他 Provider。
    """
    row = db.get(AgentPlatformDefault, 1)
    if row is None:
        return None
    if agent_kind == AGENT_KIND_STEWARD:
        provider_id, model = row.steward_provider_id, row.steward_model
    else:
        provider_id, model = row.assistant_provider_id, row.assistant_model
    if provider_id is None or not model:
        return None
    provider = db.get(AgentProvider, provider_id)
    if provider is None or not provider.enabled:
        return None
    return provider_id, model


def valid_platform_default(db: Session, agent_kind: str) -> tuple[int, str] | None:
    """该 agent 维度当前有效的平台默认（provider 存在且 enabled）；解析链同口径。

    供 owner 侧设置视图使用：视图只展示解析真正会采用的默认，避免把已被
    停用/删除 Provider 的陈旧默认呈现为可一键启用的选项。
    """
    if agent_kind not in AGENT_KINDS:
        raise_api_error(422, VALIDATION_ERROR, "未知的 agent 维度", {"agent_kind": agent_kind})
    return _valid_platform_default(db, agent_kind)


def get_platform_defaults(db: Session) -> AgentPlatformDefault:
    """平台默认单行（懒建照 WebPlatformConfig 先例）；提交由调用方事务决定。"""
    row = db.get(AgentPlatformDefault, 1)
    if row is None:
        row = AgentPlatformDefault(id=1, updated_at=timeutil.utcnow())
        db.add(row)
        db.flush()
    return row


def set_platform_defaults(
    db: Session,
    *,
    assistant: tuple[int, str] | None,
    steward: tuple[int, str] | None,
    updated_by_admin_id: int | None,
) -> AgentPlatformDefault:
    """全量覆盖平台默认（PUT 语义）：tuple=设置该维度，None=清除该维度。

    成对性（provider_id+model 同设/同清）由 schema 层校验（design §1.2）；
    本层校验 model 必须在该 Provider allowed_models 内（违反一律 422）。
    云同意语义不在本层（空间 cloud_allowed 默认 False 由解析链执行）。
    """
    row = get_platform_defaults(db)
    for kind, pair in (
        (AGENT_KIND_ASSISTANT, assistant),
        (AGENT_KIND_STEWARD, steward),
    ):
        if pair is None:
            # 清除该维度默认：解析链回退 no_space_setting（继承即不存在）
            if kind == AGENT_KIND_STEWARD:
                row.steward_provider_id = None
                row.steward_model = None
            else:
                row.assistant_provider_id = None
                row.assistant_model = None
            continue
        provider_id, model = pair
        provider = db.get(AgentProvider, provider_id)
        if provider is None:
            raise_api_error(422, VALIDATION_ERROR, "Provider 不存在", {"provider_id": provider_id})
        if model not in list(provider.allowed_models_json or []):
            raise_api_error(
                422,
                VALIDATION_ERROR,
                "model 不在该 Provider 的 allowed_models 内",
                {"allowed_models": list(provider.allowed_models_json or [])},
            )
        if kind == AGENT_KIND_STEWARD:
            row.steward_provider_id = provider_id
            row.steward_model = model
        else:
            row.assistant_provider_id = provider_id
            row.assistant_model = model
    row.updated_by_admin_id = updated_by_admin_id
    row.updated_at = timeutil.utcnow()
    return row


def _allowed(
    provider: AgentProvider, model: str, platform_default_configured: bool = False
) -> ProviderResolution:
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
        platform_default_configured=platform_default_configured,
    )


def snapshot_for_space(
    db: Session, space_id: int, agent_kind: str = AGENT_KIND_ASSISTANT
) -> dict[str, object]:
    """Capture non-secret provider metadata for an AgentRun.

    A denied profile is captured too, so a run cannot become executable merely
    because an operator changes the space setting after enqueue. agent_kind 只
    影响解析维度；快照字段结构保持不变（steward child run 快照扩展属子任务 B）。
    """
    resolution = resolve_for_space(db, space_id, agent_kind)
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


def resolve_for_run(
    db: Session, run: AgentRun, space_id: int, agent_kind: str = AGENT_KIND_ASSISTANT
) -> ProviderResolution:
    """Resolve current authorization while pinning model metadata to run snapshot."""
    current = resolve_for_space(db, space_id, agent_kind)
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
    db: Session,
    space_id: int,
    *,
    run: AgentRun | None = None,
    agent_kind: str = AGENT_KIND_ASSISTANT,
) -> ProviderRuntime | None:
    """把空间级 Provider 解析为可注入 sidecar 的运行期配置（含解密凭据）。

    权威链：DB Provider 注册 → 空间选择的 policy（allowed）→ secretbox 解密。
    policy 非 allowed、provider/model 缺失或 base_url 为空一律返回 None（fail-closed），
    调用方将其映射为可解释拒绝，绝不回退到 sidecar 环境变量。
    """
    resolution = (
        resolve_for_run(db, run, space_id, agent_kind)
        if run is not None
        else resolve_for_space(db, space_id, agent_kind)
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
    if resolution.api not in SUPPORTED_APIS:
        # 不猜协议：缺/非法 adapter 的行在结构校验（provider_profile_error）
        # 与快照校验层面都已 fail-closed，走到这里说明数据异常。
        # 早期版本会静默退回某个固定协议，那不是安全的默认值。
        return None
    return ProviderRuntime(
        provider_id=provider.id,
        provider_name=provider.name,
        kind=provider.kind,
        model=resolution.model,
        api=resolution.api,
        compat=dict(resolution.compat),
        context_window=resolution.context_window or 272_000,
        max_tokens=resolution.max_tokens or 60_000,
        reasoning=bool(resolution.reasoning),
        input_modalities=list(resolution.input_modalities),
        thinking_levels=list(resolution.thinking_levels),
        base_url=base_url,
        api_key=api_key,
    )
