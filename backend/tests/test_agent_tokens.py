"""Agent 内部 token（service/run 两级 HMAC）单元测试。"""

import pytest

from app import config
from app.services.agent_tokens import (
    RUN_TOKEN_TYPE,
    SERVICE_TOKEN_TYPE,
    AgentTokenError,
    decode_run_token,
    decode_service_token,
    issue_run_token,
    issue_service_token,
)


def test_service_token_roundtrip():
    raw = issue_service_token(ttl_seconds=60)
    claims = decode_service_token(raw)
    assert claims["typ"] == SERVICE_TOKEN_TYPE
    assert 0 < claims["exp"] - claims["iat"] <= 60


def test_run_token_roundtrip_and_ttl_cap():
    raw = issue_run_token(
        run_id=7,
        job_id=9,
        agent_kind="assistant",
        account_id=11,
        space_id=13,
        tool_allowlist=["familygraph.echo", "familygraph.probe_scope"],
        ttl_seconds=99999,  # 超上限必须被钳制到 ≤600s
    )
    claims = decode_run_token(raw)
    assert claims["typ"] == RUN_TOKEN_TYPE
    assert claims["run_id"] == 7
    assert claims["job_id"] == 9
    assert claims["agent_kind"] == "assistant"
    assert claims["account_id"] == 11
    assert claims["space_id"] == 13
    assert claims["tool_allowlist"] == ["familygraph.echo", "familygraph.probe_scope"]
    assert 0 < claims["exp"] - claims["iat"] <= config.AGENT_RUN_TOKEN_TTL_SECONDS_MAX


def test_expired_token_rejected():
    raw = issue_service_token(ttl_seconds=-1)
    with pytest.raises(AgentTokenError):
        decode_service_token(raw)


def test_tampered_token_rejected():
    raw = issue_run_token(
        run_id=1,
        job_id=1,
        agent_kind="assistant",
        account_id=1,
        space_id=1,
        tool_allowlist=[],
    )
    header, payload, signature = raw.split(".")
    forged_payload = payload[:-2] + ("AA" if not payload.endswith("AA") else "BB")
    with pytest.raises(AgentTokenError):
        decode_run_token(f"{header}.{forged_payload}.{signature}")
    with pytest.raises(AgentTokenError):
        decode_run_token(f"{header}.{payload}.{signature[:-2]}XX")


def test_token_type_confusion_rejected():
    """run token 不能当 service token 用（反之亦然）。"""
    service_raw = issue_service_token()
    with pytest.raises(AgentTokenError):
        decode_run_token(service_raw)
    run_raw = issue_run_token(
        run_id=1,
        job_id=1,
        agent_kind="steward",
        account_id=1,
        space_id=1,
        tool_allowlist=["familygraph.steward_ping"],
    )
    with pytest.raises(AgentTokenError):
        decode_service_token(run_raw)


def test_missing_shared_secret_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "AGENT_SERVICE_SECRET", "")
    with pytest.raises(AgentTokenError):
        issue_service_token()
    with pytest.raises(AgentTokenError):
        decode_service_token("whatever.token.here")
