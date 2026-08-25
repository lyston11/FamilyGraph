"""凭据安全原语：PIN 生成（secrets）/ bcrypt 哈希 / JWT 签发校验（AD-2）。

脱敏红线（logging-guidelines.md）：本模块产出的 PIN/JWT/token 哈希禁止进入日志。
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app import config

# 时序均衡用假哈希：用户名不存在时也执行一次等开销 bcrypt 校验，避免响应时间差
# 泄露账号存在性。cost 必须与真实哈希一致（config.BCRYPT_ROUNDS），否则统一文案
# 可被时序差绕过。
_DUMMY_PIN_HASH = bcrypt.hashpw(b"000000", bcrypt.gensalt(rounds=config.BCRYPT_ROUNDS))

PIN_LENGTH = 6
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


def generate_pin() -> str:
    """密码学安全随机 6 位数字 PIN（锁定决策 A3/A4）。"""
    return "".join(secrets.choice("0123456789") for _ in range(PIN_LENGTH))


def hash_pin(pin: str) -> str:
    """bcrypt 哈希；数据库永不存明文/可逆 PIN。"""
    salt = bcrypt.gensalt(rounds=config.BCRYPT_ROUNDS)
    digest = bcrypt.hashpw(pin.encode("utf-8"), salt)
    return digest.decode("utf-8")


def verify_pin(pin: str, pin_hash: str) -> bool:
    try:
        return bcrypt.checkpw(pin.encode("utf-8"), pin_hash.encode("utf-8"))
    except ValueError:
        return False


def verify_dummy_pin(pin: str) -> None:
    """对不存在的用户名执行等价开销的空校验（防时序枚举）。"""
    bcrypt.checkpw(pin.encode("utf-8"), _DUMMY_PIN_HASH)


def hash_token(token: str) -> str:
    """refresh token 指纹：sha256 hex，落库用（原始 token 不存）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _encode(payload: dict[str, Any], ttl_seconds: int) -> str:
    # 必须用 aware UTC 计算 epoch：naive .timestamp() 按本地时区解释，
    # 在 UTC+ 时区会导致新签发 token 立即过期
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        **payload,
    }
    return jwt.encode(claims, config.SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_access_token(user_id: int, token_version: int, is_admin: bool) -> str:
    """access JWT（2h）：payload 含 {sub, ver, adm, typ}（design.md 兼容性）。

    sub 按 RFC 7519/PyJWT 2.10 要求为字符串。"""
    return _encode(
        {
            "sub": str(user_id),
            "ver": token_version,
            "adm": is_admin,
            "typ": ACCESS_TOKEN_TYPE,
            "jti": secrets.token_hex(16),
        },
        config.ACCESS_TOKEN_TTL_SECONDS,
    )


def create_refresh_token(user_id: int, token_version: int, jti: str) -> str:
    """refresh JWT（30d）：jti 与 refresh_sessions 行对应。"""
    return _encode(
        {"sub": str(user_id), "ver": token_version, "typ": REFRESH_TOKEN_TYPE, "jti": jti},
        config.REFRESH_TOKEN_TTL_SECONDS,
    )


class TokenDecodeError(Exception):
    """签名/过期/类型不符等一切 JWT 校验失败的统一异常。"""


def decode_token(raw_token: str, expected_type: str) -> dict[str, Any]:
    """解码并校验 JWT；失败统一抛 TokenDecodeError（不区分原因给调用方）。"""
    try:
        payload: dict[str, Any] = jwt.decode(
            raw_token, config.SECRET_KEY, algorithms=[JWT_ALGORITHM]
        )
    except jwt.PyJWTError as exc:
        raise TokenDecodeError(str(exc.__class__.__name__)) from None
    if payload.get("typ") != expected_type:
        raise TokenDecodeError("token type mismatch")
    required = ("sub", "ver")
    if any(key not in payload for key in required):
        raise TokenDecodeError("missing claims")
    return payload
