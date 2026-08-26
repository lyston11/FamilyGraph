"""Provider 密钥静态加密盒（HMAC 流加密，无第三方依赖）。

格式：base64url(nonce(16B) || ciphertext || tag(32B))
- 加密流：SHA-256 计数器 keystream（key_enc 派生自 SECRET_KEY 域分隔标签）
- 完整性：HMAC-SHA256(key_mac, nonce || ciphertext)；解密先验签后运算（fail-closed）
- 随机 nonce 保证同一明文每次密文不同；tag 校验失败一律拒绝（防篡改注入）

SECRET_KEY 更换即全部旧密文失效（与 JWT 会话同生命周期）；日志/事件/浏览器载荷
禁止出现本模块输出（logging-guidelines.md 脱敏红线）。
"""

import base64
import hashlib
import hmac
import secrets

from app import config

_NONCE_BYTES = 16
_TAG_BYTES = 32


def _derive(label: bytes) -> bytes:
    """由 SECRET_KEY 域分隔派生子密钥。"""
    return hashlib.sha256(
        b"familygraph-secretbox:" + label + b":" + config.SECRET_KEY.encode("utf-8")
    ).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """SHA-256 计数器模式 keystream：block_i = SHA256(key || nonce || counter_be64)。"""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest())
        counter += 1
    return bytes(out[:length])


def _xor(data: bytes, stream: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, stream, strict=False))


class SecretBoxError(ValueError):
    """密文格式非法或完整性校验失败（fail-closed，不区分具体原因）。"""


def encrypt_secret(plaintext: str) -> str:
    """加密明文密钥为可落库密文（base64url，无 padding）。"""
    enc_key = _derive(b"enc")
    mac_key = _derive(b"mac")
    nonce = secrets.token_bytes(_NONCE_BYTES)
    plaintext_bytes = plaintext.encode("utf-8")
    ciphertext = _xor(plaintext_bytes, _keystream(enc_key, nonce, len(plaintext_bytes)))
    tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + ciphertext + tag).decode("ascii").rstrip("=")


def decrypt_secret(token: str) -> str:
    """验签并解密；任何格式/完整性问题抛 SecretBoxError。"""
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        if len(raw) < _NONCE_BYTES + _TAG_BYTES:
            raise SecretBoxError("ciphertext too short")
        nonce = raw[:_NONCE_BYTES]
        ciphertext = raw[_NONCE_BYTES:-_TAG_BYTES]
        tag = raw[-_TAG_BYTES:]
    except (ValueError, UnicodeDecodeError):
        raise SecretBoxError("malformed ciphertext") from None
    mac_key = _derive(b"mac")
    expected = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise SecretBoxError("integrity check failed")
    enc_key = _derive(b"enc")
    plaintext = _xor(ciphertext, _keystream(enc_key, nonce, len(ciphertext)))
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretBoxError("plaintext not utf-8") from exc
