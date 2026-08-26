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

SERVICE_TOKEN_TYPE = "agent_service"
RUN_TOKEN_TYPE = "agent_run"
_ALGORITHM = "HS256"

# run token 必含 claims（scope 五元组 + allowlist）
_RUN_REQUIRED_CLAIMS = (
    "run_id",
    "job_id",
    "agent_kind",
    "account_id",
    "space_id",
    "tool_allowlist",
)


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
    agent_kind: str,
    account_id: int,
    space_id: int,
    tool_allowlist: list[str],
    ttl_seconds: int | None = None,
) -> str:
    """run token：绑定执行实体与 scope；exp 上限 600s（design.md 合同）。"""
    ttl = min(
        ttl_seconds if ttl_seconds is not None else config.AGENT_RUN_TOKEN_TTL_SECONDS,
        config.AGENT_RUN_TOKEN_TTL_SECONDS_MAX,
    )
    return _encode(
        {
            "typ": RUN_TOKEN_TYPE,
            "run_id": run_id,
            "job_id": job_id,
            "agent_kind": agent_kind,
            "account_id": account_id,
            "space_id": space_id,
            "tool_allowlist": list(tool_allowlist),
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
    """校验 run token 并返回 claims；缺 scope claims 一律失败。"""
    payload = _decode(raw_token)
    if payload.get("typ") != RUN_TOKEN_TYPE:
        raise AgentTokenError("token type mismatch")
    missing = [key for key in _RUN_REQUIRED_CLAIMS if key not in payload]
    if missing:
        raise AgentTokenError("missing claims")
    allowlist = payload["tool_allowlist"]
    if not isinstance(allowlist, list) or any(not isinstance(item, str) for item in allowlist):
        raise AgentTokenError("invalid tool_allowlist")
    for key in ("run_id", "job_id", "account_id", "space_id"):
        if not isinstance(payload[key], int):
            raise AgentTokenError("invalid scope claim type")
    if payload["agent_kind"] not in ("assistant", "steward"):
        raise AgentTokenError("invalid agent_kind")
    return payload
