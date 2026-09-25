"""内部协议两级 HMAC token（design.md / notes.md「两级认证」）。

- service token：sidecar 静态凭据（env AGENT_SERVICE_SECRET 签发），仅可调 lease。
- run token：lease 响应签发，claims 绑定 run_id、job_id、agent_kind、account/space
  scope、tool_allowlist 与 exp（≤600s）+ jti；context/events/tools/settle 只收它。

校验一律 fail-closed：签名/过期/类型/缺 claims 统一抛 AgentTokenError，
由调用方写安全审计后拒绝。token 原文禁止进日志。
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app import config
from app.models.agent import RUNTIME_AGENT_KINDS

SERVICE_TOKEN_TYPE = "agent_service"
RUN_TOKEN_TYPE = "agent_run"
_ALGORITHM = "HS256"

# run token 必含 claims，按 kind 分开（**per-kind 表，不用一个宽元组**）：
# 两者共享 run_id / attempt / agent_kind / space_id / tool_allowlist；
# - assistant 绑定 (job_id=AgentJob.id, account_id)，因为会话式运行有账号；
# - steward 绑定 (job_id=StewardJob.id, steward_batch_id?) 且**无** account_id
#   （空间级执行）；terminology 另有 viewer_account_id。
#
# 用一个宽元组会让「steward token 缺 account_id」与「assistant token 缺
# steward_batch_id」都被误判为合法，因此必须逐 kind 校验。
_RUN_REQUIRED_CLAIMS_BY_KIND: dict[str, tuple[str, ...]] = {
    "assistant": (
        "run_id",
        "job_id",
        "attempt",
        "agent_kind",
        "account_id",
        "space_id",
        "tool_allowlist",
    ),
    "steward": (
        "run_id",
        "job_id",
        "attempt",
        "agent_kind",
        "space_id",
        "tool_allowlist",
    ),
}

# 两者都必须携带且必须为正整数的 scope claims（account_id 仅 assistant 有）
_SCOPE_INT_CLAIMS_BY_KIND: dict[str, tuple[str, ...]] = {
    "assistant": ("run_id", "job_id", "attempt", "account_id", "space_id"),
    "steward": ("run_id", "job_id", "attempt", "space_id"),
}

# steward 可选的额外 claims（可缺失或为 null，不可为其他类型）
_STEWARD_OPTIONAL_INT_CLAIMS = ("steward_batch_id", "viewer_account_id")


class AgentTokenError(Exception):
    """任何 token 校验失败的统一异常（不向调用方区分原因，防枚举）。"""


def _signing_key() -> bytes:
    secret = config.AGENT_SERVICE_SECRET
    if not secret.strip():
        # 未配置共享密钥：fail-closed（签不出也验不过）
        raise AgentTokenError("AGENT_SERVICE_SECRET not configured")
    return secret.encode("utf-8")


def _encode(payload: dict[str, Any], ttl_seconds: int) -> str:
    now = datetime.now(UTC)
    claims = {
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "jti": secrets.token_hex(16),
        **payload,
    }
    return jwt.encode(claims, _signing_key(), algorithm=_ALGORITHM)


def issue_service_token(ttl_seconds: int | None = None) -> str:
    """sidecar lease 凭据；TTL 短（默认 config.AGENT_SERVICE_TOKEN_TTL_SECONDS）。"""
    ttl = ttl_seconds or config.AGENT_SERVICE_TOKEN_TTL_SECONDS
    return _encode({"typ": SERVICE_TOKEN_TYPE}, ttl)


def issue_run_token(
    *,
    run_id: int,
    job_id: int,
    attempt: int,
    agent_kind: str,
    account_id: int | None = None,
    space_id: int,
    tool_allowlist: list[str],
    steward_batch_id: int | None = None,
    viewer_account_id: int | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """run token：绑定执行实体与 scope；exp 上限 600s（design.md 合同）。

    per-kind 必含 claims（见 ``_RUN_REQUIRED_CLAIMS_BY_KIND``）：assistant 必须
    给 account_id 且不得给 steward 专属字段；steward 必须**不**给 account_id。
    少给或多给都 fail-closed——宁可签不出来，也不签一个语义含混的 token。
    """
    if type(attempt) is not int or attempt < 1:
        raise AgentTokenError("invalid attempt")
    if agent_kind not in RUNTIME_AGENT_KINDS:
        raise AgentTokenError("invalid agent_kind")

    if agent_kind == "assistant":
        if account_id is None or type(account_id) is not int or account_id < 1:
            raise AgentTokenError("assistant run token requires account_id")
        if steward_batch_id is not None or viewer_account_id is not None:
            raise AgentTokenError("assistant run token must not carry steward claims")
        payload: dict[str, Any] = {"account_id": account_id}
    else:
        if account_id is not None:
            # 空间级执行没有单一账号；带 account_id 会让下游误以为可以按账号授权。
            raise AgentTokenError("steward run token must not carry account_id")
        payload = {}
        for key, value in (
            ("steward_batch_id", steward_batch_id),
            ("viewer_account_id", viewer_account_id),
        ):
            if value is not None:
                if type(value) is not int or value < 1:
                    raise AgentTokenError(f"invalid {key}")
                payload[key] = value

    ttl = min(
        ttl_seconds if ttl_seconds is not None else config.AGENT_RUN_TOKEN_TTL_SECONDS,
        config.AGENT_RUN_TOKEN_TTL_SECONDS_MAX,
    )
    return _encode(
        {
            "typ": RUN_TOKEN_TYPE,
            "run_id": run_id,
            "job_id": job_id,
            "attempt": int(attempt),
            "agent_kind": agent_kind,
            "space_id": space_id,
            "tool_allowlist": list(tool_allowlist),
            **payload,
        },
        ttl,
    )


def _decode(raw_token: str) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = jwt.decode(raw_token, _signing_key(), algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AgentTokenError(exc.__class__.__name__) from None
    return payload


def decode_service_token(raw_token: str) -> dict[str, Any]:
    """校验 service token；类型不符视为失败（run token 不能调 lease）。"""
    payload = _decode(raw_token)
    if payload.get("typ") != SERVICE_TOKEN_TYPE:
        raise AgentTokenError("token type mismatch")
    return payload


def decode_run_token(raw_token: str) -> dict[str, Any]:
    """校验 run token 并返回 claims；按 kind 逐项校验 scope claims。"""
    payload = _decode(raw_token)
    if payload.get("typ") != RUN_TOKEN_TYPE:
        raise AgentTokenError("token type mismatch")
    agent_kind = payload.get("agent_kind")
    if agent_kind not in RUNTIME_AGENT_KINDS:
        raise AgentTokenError("invalid agent_kind")
    # 先按 kind 取必含集，再校验——顺序不能反，否则会用一个宽集合放过语义错配。
    required = _RUN_REQUIRED_CLAIMS_BY_KIND[agent_kind]
    if any(key not in payload for key in required):
        raise AgentTokenError("missing claims")
    allowlist = payload["tool_allowlist"]
    if not isinstance(allowlist, list) or any(not isinstance(item, str) for item in allowlist):
        raise AgentTokenError("invalid tool_allowlist")
    for key in _SCOPE_INT_CLAIMS_BY_KIND[agent_kind]:
        if type(payload[key]) is not int or payload[key] < 1:
            raise AgentTokenError("invalid scope claim type")
    if agent_kind == "steward":
        # 可选 claims 缺失或为正整数，其他类型一律拒绝（不接受 "3"/True）。
        for key in _STEWARD_OPTIONAL_INT_CLAIMS:
            value = payload.get(key)
            if value is not None and (type(value) is not int or value < 1):
                raise AgentTokenError(f"invalid {key}")
        if "account_id" in payload:
            raise AgentTokenError("steward run token must not carry account_id")
    return payload
