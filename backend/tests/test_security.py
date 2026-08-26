"""utils/security.py 单测（implement.md 清单 #2：单测先行项）。"""

import pytest

from app.utils import security


def test_generate_pin_is_six_digits() -> None:
    for _ in range(20):
        pin = security.generate_pin()
        assert len(pin) == 6
        assert pin.isdigit()


def test_generate_pin_not_constant() -> None:
    pins = {security.generate_pin() for _ in range(10)}
    assert len(pins) > 1  # secrets 随机性冒烟


def test_hash_pin_roundtrip_and_salt_uniqueness() -> None:
    h1 = security.hash_pin("123456")
    h2 = security.hash_pin("123456")
    assert h1 != h2  # bcrypt 盐随机
    assert security.verify_pin("123456", h1)
    assert not security.verify_pin("654321", h1)
    assert not security.verify_pin("123456", "not-a-hash")


def test_no_plaintext_or_reversible_pin_in_hash() -> None:
    digest = security.hash_pin("903472")
    assert "903472" not in digest


def test_access_token_roundtrip_claims() -> None:
    token = security.create_access_token(user_id=7, token_version=3, is_platform_operator=True)
    payload = security.decode_token(token, security.ACCESS_TOKEN_TYPE)
    assert payload["sub"] == "7"
    assert payload["ver"] == 3
    assert payload["adm"] is True
    assert payload["typ"] == "access"


def test_refresh_token_roundtrip() -> None:
    token = security.create_refresh_token(user_id=9, token_version=0, jti="abc")
    payload = security.decode_token(token, security.REFRESH_TOKEN_TYPE)
    assert payload["typ"] == "refresh"
    assert payload["jti"] == "abc"


def test_expired_token_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config

    monkeypatch.setattr(config, "ACCESS_TOKEN_TTL_SECONDS", -10)
    token = security.create_access_token(user_id=1, token_version=0, is_platform_operator=False)
    with pytest.raises(security.TokenDecodeError):
        security.decode_token(token, security.ACCESS_TOKEN_TYPE)


def test_tampered_signature_rejected() -> None:
    token = security.create_access_token(user_id=1, token_version=0, is_platform_operator=False)
    header, body, sig = token.split(".")
    tampered = f"{header}.{body}.AAAA{sig[4:]}"
    with pytest.raises(security.TokenDecodeError):
        security.decode_token(tampered, security.ACCESS_TOKEN_TYPE)


def test_type_confusion_rejected() -> None:
    """access token 不得当 refresh 用，反之亦然。"""
    access = security.create_access_token(user_id=1, token_version=0, is_platform_operator=False)
    refresh = security.create_refresh_token(user_id=1, token_version=0, jti="x")
    with pytest.raises(security.TokenDecodeError):
        security.decode_token(access, security.REFRESH_TOKEN_TYPE)
    with pytest.raises(security.TokenDecodeError):
        security.decode_token(refresh, security.ACCESS_TOKEN_TYPE)


def test_garbage_token_rejected() -> None:
    with pytest.raises(security.TokenDecodeError):
        security.decode_token("garbage.token.here", security.ACCESS_TOKEN_TYPE)


def test_hash_token_is_sha256_hex() -> None:
    import hashlib

    digest = security.hash_token("raw-token")
    assert digest == hashlib.sha256(b"raw-token").hexdigest()
    assert len(digest) == 64
