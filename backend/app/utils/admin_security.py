"""管理员 JWT 独立签发域（09-04 SF-F4）。

管理员令牌与家庭令牌使用完全独立的 secret/issuer/audience：
- 复制 admin token 到家庭 listener，或反向复制，签名校验一律失败；
- claims 固定含 sub / principal_type=system_admin / iss / aud /
  token_version / jti / iat / exp / typ；
- access 短效（ADMIN_ACCESS_TOKEN_TTL_SECONDS，默认 15 分钟）；
- refresh 轮换受绝对有效期约束（轮换不续期，见 services/admin_auth.py）。

脱敏红线（logging-guidelines.md）：本模块产出的 token 禁止进入日志。
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app import config
from app.utils.security import JWT_ALGORITHM, REFRESH_TOKEN_TYPE, TokenDecodeError

ADMIN_ACCESS_TOKEN_TYPE = "access"
ADMIN_REFRESH_TOKEN_TYPE = REFRESH_TOKEN_TYPE
ADMIN_PRINCIPAL_TYPE = "system_admin"

# 管理员版本 claim 名与家庭 ``ver`` 刻意不同：两类 token 结构上不可互认。
VERSION_CLAIM = "token_version"


def _encode(payload: dict[str, Any], ttl_seconds: int) -> str:
    """签发 admin JWT；必须用 aware UTC 计算 epoch（参照 security._encode）。"""
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "iss": config.ADMIN_JWT_ISSUER,
        "aud": config.ADMIN_JWT_AUDIENCE,
        "principal_type": ADMIN_PRINCIPAL_TYPE,
        **payload,
    }
    return jwt.encode(claims, config.ADMIN_JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_admin_access_token(admin_id: int, password_version: int, jti: str | None = None) -> str:
    """管理员 access JWT（15 分钟短效）。"""
    return _encode(
        {
            "sub": str(admin_id),
            VERSION_CLAIM: password_version,
            "typ": ADMIN_ACCESS_TOKEN_TYPE,
            "jti": jti or secrets.token_hex(16),
        },
        config.ADMIN_ACCESS_TOKEN_TTL_SECONDS,
    )


def create_admin_refresh_token(
    admin_id: int, password_version: int, jti: str, ttl_seconds: int
) -> str:
    """管理员 refresh JWT；exp 即该会话链的绝对有效期（轮换不延长）。"""
    return _encode(
        {
            "sub": str(admin_id),
            VERSION_CLAIM: password_version,
            "typ": ADMIN_REFRESH_TOKEN_TYPE,
            "jti": jti,
        },
        ttl_seconds,
    )


def decode_admin_token(raw_token: str, expected_type: str) -> dict[str, Any]:
    """校验 admin JWT：签名 + iss + aud + typ + principal_type + 必备 claims。

    家庭 token（家庭 SECRET_KEY 签名、无 iss/aud）在此一律解码失败。
    失败统一抛 TokenDecodeError，不区分原因泄露给调用方。
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            raw_token,
            config.ADMIN_JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            issuer=config.ADMIN_JWT_ISSUER,
            audience=config.ADMIN_JWT_AUDIENCE,
            options={"require": ["exp", "iat", "sub", VERSION_CLAIM, "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenDecodeError(str(exc.__class__.__name__)) from None
    if payload.get("typ") != expected_type:
        raise TokenDecodeError("token type mismatch")
    if payload.get("principal_type") != ADMIN_PRINCIPAL_TYPE:
        raise TokenDecodeError("principal type mismatch")
    return payload
