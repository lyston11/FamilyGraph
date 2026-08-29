"""密钥静态加密与导出信封（fail-closed；日志/事件/浏览器载荷禁止出现本模块输出）。

两部分：
1. Provider 密钥静态加密（encrypt_secret/decrypt_secret）：纯 stdlib HMAC
   流加密，格式 base64url(nonce(16B) || ciphertext || tag(32B))，用于
   AgentProvider / Controlled Web provider secret 落库。
2. 数据权利导出 envelope（encrypt_envelope/decrypt_envelope）：成熟 AEAD
   （AES-256-GCM + HKDF-SHA256 KEK 派生，依赖 cryptography），每文件随机
   DEK，DEK 再用 SECRET_KEY 派生的 KEK 包裹；带 key_id 支持密钥轮换识别。

SECRET_KEY 更换即全部派生密钥失效（与 JWT 会话同生命周期）；验签/验 tag
失败与未知 key_id 一律拒绝（防篡改注入）。
"""

import base64
import hashlib
import hmac
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

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


# ---- 数据权利导出 envelope（server KEK + per-file DEK；AES-GCM AEAD）----

_EXPORT_ENVELOPE_ALG = "familygraph-export-envelope-v2"
_DEK_BYTES = 32
_GCM_NONCE_BYTES = 12
_EXPORT_KEK_INFO = b"familygraph-export-envelope/v2"


def _export_kek(secret_key: str) -> tuple[bytes, str]:
    """由 SECRET_KEY 派生导出密钥加密主密钥（AES-256-GCM）与其稳定 key_id。

    key_id 用于轮换识别：SECRET_KEY 轮换后派生 key_id 变化，旧 envelope 的
    key_id 无法匹配即 fail-closed（绝不尝试用新密钥解旧密或返回明文）。
    """
    material = secret_key.encode("utf-8")
    kek = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_EXPORT_KEK_INFO).derive(
        material
    )
    key_id = hashlib.sha256(b"familygraph-export-key-id:" + material).hexdigest()[:16]
    return kek, key_id


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def encrypt_envelope(plaintext: str) -> dict[str, str]:
    """导出文件 envelope：per-file DEK 用 AES-256-GCM 加密明文，DEK 再用
    SECRET_KEY 派生的 KEK（带 key_id）以 AES-256-GCM 包裹。磁盘无明文。"""
    kek, key_id = _export_kek(config.SECRET_KEY)
    dek = secrets.token_bytes(_DEK_BYTES)
    aad = f"{_EXPORT_ENVELOPE_ALG}:{key_id}".encode()
    nonce = secrets.token_bytes(_GCM_NONCE_BYTES)
    ciphertext = AESGCM(dek).encrypt(nonce, plaintext.encode("utf-8"), aad)
    kek_nonce = secrets.token_bytes(_GCM_NONCE_BYTES)
    wrapped_key = kek_nonce + AESGCM(kek).encrypt(kek_nonce, dek, aad)
    return {
        "v": "2",
        "alg": _EXPORT_ENVELOPE_ALG,
        "key_id": key_id,
        "wrapped_key": _b64(wrapped_key),
        "nonce": _b64(nonce),
        "ciphertext": _b64(ciphertext),
    }


def decrypt_envelope(envelope: dict[str, str]) -> str:
    """验签并解密 envelope；alg/key_id 不匹配、nonce 非法或 GCM tag 失败一律
    抛 SecretBoxError（fail-closed，不区分具体原因以抑制 oracle）。"""
    if not isinstance(envelope, dict) or envelope.get("alg") != _EXPORT_ENVELOPE_ALG:
        raise SecretBoxError("unknown envelope alg")
    key_id = envelope.get("key_id")
    if not isinstance(key_id, str):
        raise SecretBoxError("malformed envelope")
    kek, expected_id = _export_kek(config.SECRET_KEY)
    if key_id != expected_id:
        # SECRET_KEY 轮换后旧 envelope 无法解密：fail-closed，绝不返回旧密文明文
        raise SecretBoxError("unknown key id")
    aad = f"{_EXPORT_ENVELOPE_ALG}:{key_id}".encode()
    try:
        wrapped = _unb64(envelope["wrapped_key"])
        nonce = _unb64(envelope["nonce"])
        ciphertext = _unb64(envelope["ciphertext"])
        if len(wrapped) < _GCM_NONCE_BYTES or len(nonce) != _GCM_NONCE_BYTES:
            raise SecretBoxError("malformed envelope")
        kek_nonce = wrapped[:_GCM_NONCE_BYTES]
        dek = AESGCM(kek).decrypt(kek_nonce, wrapped[_GCM_NONCE_BYTES:], aad)
        plaintext = AESGCM(dek).decrypt(nonce, ciphertext, aad)
    except (KeyError, ValueError, TypeError, InvalidTag):
        raise SecretBoxError("malformed envelope") from None
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretBoxError("plaintext not utf-8") from exc
