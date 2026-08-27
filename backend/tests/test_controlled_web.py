"""V2.6 Controlled Web: default-off, dual opt-in, SSRF, one-use tokens, quota, citations.

These tests exercise the FastAPI-only gateway boundary directly so policy,
DNS/URL checks, approval tokens, quotas and citations keep identical semantics
for both browser endpoints and Agent-side tool dispatch.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import create_agent_fixture, create_space_member, create_user_with_pin
from sqlalchemy import select

from app import config
from app.errors import (
    CONTROLLED_WEB_DISABLED,
    WEB_APPROVAL_EXPIRED,
    WEB_APPROVAL_INVALID,
    WEB_APPROVAL_USED,
    WEB_BUDGET_EXCEEDED,
    WEB_DOMAIN_NOT_ALLOWED,
    WEB_QUERY_BLOCKED,
    WEB_RATE_LIMITED,
    WEB_SPACE_DISABLED,
    WEB_SSRF_BLOCKED,
    WEB_URL_INVALID,
)
from app.models.controlled_web import (
    WebApprovedURL,
    WebPlatformConfig,
    WebSpaceConfig,
)
from app.services import controlled_web
from app.services.controlled_web import WebGatewayError, _validate_public_url


# ---- 造数辅助 ---------------------------------------------------------------


def _enable_platform(
    db_session,
    *,
    allowed_domains: list[str] | None = None,
    search_endpoint: str = "https://search.example.com/api",
    provider_secret: str = "secret-token",
    max_requests_per_minute: int = 30,
    monthly_budget_cents: int = 0,
) -> WebPlatformConfig:
    from app.utils import secretbox, timeutil

    row = WebPlatformConfig(
        id=1,
        enabled=True,
        search_provider="configured",
        search_endpoint=search_endpoint,
        provider_secret_ciphertext=secretbox.encrypt_secret(provider_secret),
        allowed_domains_json=allowed_domains or ["example.com", "search.example.com"],
        denied_domains_json=[],
        max_results=10,
        max_fetch_bytes=1_000_000,
        max_requests_per_minute=max_requests_per_minute,
        monthly_budget_cents=monthly_budget_cents,
        updated_at=timeutil.utcnow(),
        updated_by_account_id=None,
    )
    db_session.add(row)
    return row


def _enable_space(
    db_session,
    *,
    space_id: int,
    use_cases: list[str] | None = None,
    max_requests_per_minute: int = 10,
) -> WebSpaceConfig:
    from app.utils import timeutil

    row = WebSpaceConfig(
        space_id=space_id,
        enabled=True,
        allowed_use_cases_json=use_cases or ["research", "fact_check", "citation"],
        max_results=10,
        max_fetch_bytes=1_000_000,
        max_requests_per_minute=max_requests_per_minute,
        monthly_budget_cents=0,
        updated_at=timeutil.utcnow(),
        updated_by_account_id=None,
    )
    db_session.add(row)
    return row


def _fake_search_results(*results: dict[str, str]) -> list[dict[str, str]]:
    """Default to a single public example.com result if none supplied."""
    if not results:
        results = ({"title": "Example", "url": "https://www.example.com/page", "snippet": "ok"},)
    return [dict(r) for r in results]


def _patch_provider_search(monkeypatch, results: list[dict[str, str]] | None = None):
    payload = {"results": _fake_search_results(*(results or ()))}

    def _fake(endpoint, secret_ciphertext, query, limit):
        assert endpoint  # endpoint required
        assert secret_ciphertext  # secret must be decrypted inside gateway
        return payload["results"]

    monkeypatch.setattr(controlled_web, "_provider_search", _fake)


def _patch_fetch_bytes(monkeypatch, body: bytes, content_type: str = "text/html; charset=utf-8"):
    def _fake(url, max_bytes):
        assert len(body) <= max_bytes
        return body, content_type

    monkeypatch.setattr(controlled_web, "_fetch_bytes", _fake)


def _patch_dns(monkeypatch, *, host_map: dict[str, str] | None = None):
    """Bypass real DNS for policy/quota/token tests so they don't depend on a network.

    Returns a normalised (hostname, scheme) tuple the same shape as the real
    validator, after a lightweight scheme/port/credential check. SSRF-specific
    tests call the real ``_validate_public_url`` directly and must not patch it.
    """
    safe_hosts = host_map or {
        "search.example.com": "search.example.com",
        "www.example.com": "www.example.com",
        "www.allowed.example.com": "www.allowed.example.com",
        "example.com": "example.com",
    }

    def _fake(url: str):
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise controlled_web.WebGatewayError(
                422, WEB_URL_INVALID, "仅允许 http/https 网址"
            )
        if parsed.username or parsed.password or not parsed.hostname:
            raise controlled_web.WebGatewayError(
                422, WEB_URL_INVALID, "网址不得包含凭据或缺少域名"
        )
        if parsed.port is not None and parsed.port not in {80, 443}:
            raise controlled_web.WebGatewayError(
                422, WEB_URL_INVALID, "网址端口不在允许范围"
            )
        host = parsed.hostname.rstrip(".").lower()
        if host not in safe_hosts:
            # Let the real validator's domain allowlist logic decide (returns
            # the hostname so _ensure_allowed_domain can reject unknown hosts).
            return host, parsed.scheme.lower()
        return safe_hosts[host], parsed.scheme.lower()

    monkeypatch.setattr(controlled_web, "_validate_public_url", _fake)


# ---- AC-W1 默认关闭与双层开关 --------------------------------------------------


def test_tools_not_enabled_when_platform_flag_off(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", False)
    owner, space = create_agent_fixture(db_session, name="web-owner")
    create_space_member(db_session, space.id, owner.id, role="owner")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id)
    db_session.commit()

    assert controlled_web.agent_tools_enabled(
        db_session, account_id=owner.account.id, space_id=space.id
    ) is False


def test_tools_not_enabled_when_platform_config_missing(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-owner2")
    create_space_member(db_session, space.id, owner.id, role="owner")
    # space config exists but platform row absent -> fail closed
    _enable_space(db_session, space_id=space.id)
    db_session.commit()

    assert controlled_web.agent_tools_enabled(
        db_session, account_id=owner.account.id, space_id=space.id
    ) is False


def test_tools_not_enabled_when_space_disabled(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-owner3")
    create_space_member(db_session, space.id, owner.id, role="owner")
    _enable_platform(db_session)
    row = _enable_space(db_session, space_id=space.id)
    row.enabled = False  # space opt-out even with platform on
    db_session.commit()

    assert controlled_web.agent_tools_enabled(
        db_session, account_id=owner.account.id, space_id=space.id
    ) is False


def test_search_denied_when_global_flag_off(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", False)
    owner, space = create_agent_fixture(db_session, name="web-owner4")
    create_space_member(db_session, space.id, owner.id, role="owner")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id)
    db_session.commit()

    with pytest.raises(WebGatewayError) as exc:
        controlled_web.search_web(
            db_session,
            account_id=owner.account.id,
            space_id=space.id,
            run_id=None,
            query="safe query",
            use_case="research",
            limit=5,
        )
    assert exc.value.code == CONTROLLED_WEB_DISABLED


def test_search_denied_when_use_case_not_authorized(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-owner5")
    create_space_member(db_session, space.id, owner.id, role="owner")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id, use_cases=["research"])  # no fact_check
    db_session.commit()

    with pytest.raises(WebGatewayError) as exc:
        controlled_web.search_web(
            db_session,
            account_id=owner.account.id,
            space_id=space.id,
            run_id=None,
            query="q",
            use_case="fact_check",
            limit=5,
        )
    assert exc.value.code == WEB_SPACE_DISABLED


def test_search_denied_for_non_member(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-owner6")
    create_space_member(db_session, space.id, owner.id, role="owner")
    # a second user who is NOT a member of the space
    outsider = create_user_with_pin(db_session, "web-outsider", "123456")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id)
    db_session.commit()

    with pytest.raises(WebGatewayError) as exc:
        controlled_web.search_web(
            db_session,
            account_id=outsider.account.id,
            space_id=space.id,
            run_id=None,
            query="q",
            use_case="research",
            limit=5,
        )
    assert exc.value.code == WEB_SPACE_DISABLED


def test_search_denied_for_guest_member(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-owner7")
    create_space_member(db_session, space.id, owner.id, role="owner")
    guest = create_user_with_pin(db_session, "web-guest", "123456")
    create_space_member(db_session, space.id, guest.id, role="guest", status="active")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id)
    db_session.commit()

    with pytest.raises(WebGatewayError) as exc:
        controlled_web.search_web(
            db_session,
            account_id=guest.account.id,
            space_id=space.id,
            run_id=None,
            query="q",
            use_case="research",
            limit=5,
        )
    assert exc.value.code == WEB_SPACE_DISABLED


# ---- AC-W2 SSRF / egress ----------------------------------------------------


def test_validate_public_url_rejects_non_http_schemes():
    with pytest.raises(WebGatewayError) as exc:
        _validate_public_url("file:///etc/passwd")
    assert exc.value.code == WEB_URL_INVALID


def test_validate_public_url_rejects_data_uri():
    with pytest.raises(WebGatewayError) as exc:
        _validate_public_url("data:text/plain,hello")
    assert exc.value.code == WEB_URL_INVALID


def test_validate_public_url_rejects_loopback_host(monkeypatch):
    # 127.0.0.1 resolves to a loopback IP -> SSRF_BLOCKED
    with pytest.raises(WebGatewayError) as exc:
        _validate_public_url("https://127.0.0.1/")
    assert exc.value.code == WEB_SSRF_BLOCKED


def test_validate_public_url_rejects_private_rfc1918():
    with pytest.raises(WebGatewayError) as exc:
        _validate_public_url("https://10.0.0.1/")
    assert exc.value.code == WEB_SSRF_BLOCKED


def test_validate_public_url_rejects_metadata_host():
    # AWS/Azure IMDS addresses are link-local / reserved
    with pytest.raises(WebGatewayError) as exc:
        _validate_public_url("https://169.254.169.254/latest/meta-data/")
    assert exc.value.code == WEB_SSRF_BLOCKED


def test_validate_public_url_rejects_non_standard_port():
    with pytest.raises(WebGatewayError) as exc:
        _validate_public_url("https://example.com:8080/")
    assert exc.value.code == WEB_URL_INVALID


def test_validate_public_url_rejects_credentials_in_url():
    with pytest.raises(WebGatewayError) as exc:
        _validate_public_url("https://user:pass@example.com/")
    assert exc.value.code == WEB_URL_INVALID


def test_search_filters_results_outside_allowlist(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-owner8")
    create_space_member(db_session, space.id, owner.id, role="owner")
    _enable_platform(
        db_session,
        allowed_domains=["allowed.example.com", "search.example.com"],
    )
    _enable_space(db_session, space_id=space.id)
    db_session.commit()
    _patch_dns(
        monkeypatch,
        host_map={"search.example.com": "search.example.com"},
    )

    # provider returns one allowed + one disallowed host; only allowed survives
    _patch_provider_search(
        monkeypatch,
        results=(
            {"title": "Allowed", "url": "https://www.allowed.example.com/a", "snippet": "ok"},
            {"title": "Blocked", "url": "https://www.evil.example.com/b", "snippet": "bad"},
        ),
    )

    result = controlled_web.search_web(
        db_session,
        account_id=owner.account.id,
        space_id=space.id,
        run_id=None,
        query="q",
        use_case="research",
        limit=5,
    )
    assert len(result["results"]) == 1
    assert result["results"][0]["domain"] == "www.allowed.example.com"
    db_session.commit()  # flush pending token rows (autoflush is off)
    tokens = db_session.scalars(select(WebApprovedURL)).all()
    assert len(tokens) == 1
    assert tokens[0].domain == "www.allowed.example.com"


def test_search_rejects_provider_endpoint_not_in_allowlist(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-owner9")
    create_space_member(db_session, space.id, owner.id, role="owner")
    # platform endpoint host NOT in allowlist
    _enable_platform(
        db_session,
        allowed_domains=["www.example.com"],  # only fetch targets allowed
        search_endpoint="https://provider.notallowed.example.com/api",
    )
    _enable_space(db_session, space_id=space.id)
    db_session.commit()
    _patch_dns(
        monkeypatch,
        host_map={"provider.notallowed.example.com": "provider.notallowed.example.com"},
    )

    with pytest.raises(WebGatewayError) as exc:
        controlled_web.search_web(
            db_session,
            account_id=owner.account.id,
            space_id=space.id,
            run_id=None,
            query="q",
            use_case="research",
            limit=5,
        )
    assert exc.value.code == WEB_DOMAIN_NOT_ALLOWED


# ---- approval token: one-use + expiry ---------------------------------------


def test_search_then_fetch_happy_path(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-owner10")
    create_space_member(db_session, space.id, owner.id, role="owner")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id)
    db_session.commit()
    _patch_dns(monkeypatch)

    _patch_provider_search(monkeypatch)
    _patch_fetch_bytes(monkeypatch, b"<html><body><p>Family records</p></body></html>")

    search = controlled_web.search_web(
        db_session,
        account_id=owner.account.id,
        space_id=space.id,
        run_id=None,
        query="genealogy",
        use_case="research",
        limit=5,
    )
    token = search["results"][0]["approved_token"]
    db_session.commit()

    fetched = controlled_web.fetch_approved_page(
        db_session,
        account_id=owner.account.id,
        space_id=space.id,
        run_id=None,
        approved_token=token,
    )
    db_session.commit()

    assert "Family records" in fetched["content"]
    assert fetched["untrusted"] is True
    assert fetched["prompt_instructions"]  # injection-boundary instruction present
    citation = fetched["citation"]
    assert citation["trust"] == "external"
    assert citation["url"].startswith("https://")


def test_fetch_rejects_invalid_token(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-owner11")
    create_space_member(db_session, space.id, owner.id, role="owner")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id)
    db_session.commit()

    with pytest.raises(WebGatewayError) as exc:
        controlled_web.fetch_approved_page(
            db_session,
            account_id=owner.account.id,
            space_id=space.id,
            run_id=None,
            approved_token="nonexistent-token-aaaaaaaaaaaaaaaaaaaaaa",
        )
    assert exc.value.code == WEB_APPROVAL_INVALID


def test_fetch_token_is_single_use(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-owner12")
    create_space_member(db_session, space.id, owner.id, role="owner")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id)
    db_session.commit()
    _patch_dns(monkeypatch)

    _patch_provider_search(monkeypatch)
    _patch_fetch_bytes(monkeypatch, b"<p>once</p>")

    search = controlled_web.search_web(
        db_session,
        account_id=owner.account.id,
        space_id=space.id,
        run_id=None,
        query="q",
        use_case="research",
        limit=5,
    )
    token = search["results"][0]["approved_token"]
    db_session.commit()

    controlled_web.fetch_approved_page(
        db_session,
        account_id=owner.account.id,
        space_id=space.id,
        run_id=None,
        approved_token=token,
    )
    db_session.commit()

    # second use with same token must be rejected
    with pytest.raises(WebGatewayError) as exc:
        controlled_web.fetch_approved_page(
            db_session,
            account_id=owner.account.id,
            space_id=space.id,
            run_id=None,
            approved_token=token,
        )
    assert exc.value.code == WEB_APPROVAL_USED


def test_fetch_token_cannot_be_used_by_other_account(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-owner13")
    create_space_member(db_session, space.id, owner.id, role="owner")
    other = create_user_with_pin(db_session, "web-other13", "123456")
    create_space_member(db_session, space.id, other.id, role="member")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id)
    db_session.commit()
    _patch_dns(monkeypatch)

    _patch_provider_search(monkeypatch)
    _patch_fetch_bytes(monkeypatch, b"<p>x</p>")

    search = controlled_web.search_web(
        db_session,
        account_id=owner.account.id,
        space_id=space.id,
        run_id=None,
        query="q",
        use_case="research",
        limit=5,
    )
    token = search["results"][0]["approved_token"]
    db_session.commit()

    # different account cannot redeem owner's token
    with pytest.raises(WebGatewayError) as exc:
        controlled_web.fetch_approved_page(
            db_session,
            account_id=other.account.id,
            space_id=space.id,
            run_id=None,
            approved_token=token,
        )
    assert exc.value.code == WEB_APPROVAL_INVALID


def test_fetch_token_expired_is_rejected(db_session, monkeypatch):
    from datetime import timedelta

    from app.utils import timeutil

    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-owner14")
    create_space_member(db_session, space.id, owner.id, role="owner")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id)
    db_session.commit()
    _patch_dns(monkeypatch)

    _patch_provider_search(monkeypatch)
    search = controlled_web.search_web(
        db_session,
        account_id=owner.account.id,
        space_id=space.id,
        run_id=None,
        query="q",
        use_case="research",
        limit=5,
    )
    token = search["results"][0]["approved_token"]
    db_session.commit()  # flush pending token rows (autoflush is off)
    # force expiry
    rows = db_session.scalars(select(WebApprovedURL)).all()
    for row in rows:
        row.expires_at = timeutil.utcnow() - timedelta(seconds=1)
    db_session.commit()

    with pytest.raises(WebGatewayError) as exc:
        controlled_web.fetch_approved_page(
            db_session,
            account_id=owner.account.id,
            space_id=space.id,
            run_id=None,
            approved_token=token,
        )
    assert exc.value.code == WEB_APPROVAL_EXPIRED


# ---- AC-W5 quota / budget ---------------------------------------------------


def test_rate_limit_blocks_after_max_requests(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-owner15")
    create_space_member(db_session, space.id, owner.id, role="owner")
    _enable_platform(db_session, max_requests_per_minute=2)
    _enable_space(db_session, space_id=space.id, max_requests_per_minute=2)
    db_session.commit()
    _patch_dns(monkeypatch)
    _patch_provider_search(monkeypatch)

    for i in range(2):
        controlled_web.search_web(
            db_session,
            account_id=owner.account.id,
            space_id=space.id,
            run_id=None,
            query=f"q{i}",
            use_case="research",
            limit=5,
        )
        db_session.commit()

    with pytest.raises(WebGatewayError) as exc:
        controlled_web.search_web(
            db_session,
            account_id=owner.account.id,
            space_id=space.id,
            run_id=None,
            query="q-overflow",
            use_case="research",
            limit=5,
        )
    assert exc.value.code == WEB_RATE_LIMITED


def test_monthly_budget_exhaustion_blocks_search(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-owner16")
    create_space_member(db_session, space.id, owner.id, role="owner")
    # each succeeded request costs 1 cent; budget = 1
    _enable_platform(db_session, max_requests_per_minute=100, monthly_budget_cents=1)
    _enable_space(db_session, space_id=space.id, max_requests_per_minute=100)
    db_session.commit()
    _patch_dns(monkeypatch)
    _patch_provider_search(monkeypatch)

    controlled_web.search_web(
        db_session,
        account_id=owner.account.id,
        space_id=space.id,
        run_id=None,
        query="q1",
        use_case="research",
        limit=5,
    )
    db_session.commit()

    with pytest.raises(WebGatewayError) as exc:
        controlled_web.search_web(
            db_session,
            account_id=owner.account.id,
            space_id=space.id,
            run_id=None,
            query="q2",
            use_case="research",
            limit=5,
        )
    assert exc.value.code == WEB_BUDGET_EXCEEDED


# ---- usage audit never stores raw query -------------------------------------


def test_usage_record_stores_hash_not_raw_query(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-owner17")
    create_space_member(db_session, space.id, owner.id, role="owner")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id)
    db_session.commit()
    _patch_dns(monkeypatch)
    _patch_provider_search(monkeypatch)

    controlled_web.search_web(
        db_session,
        account_id=owner.account.id,
        space_id=space.id,
        run_id=None,
        query="secret family name query",
        use_case="research",
        limit=5,
    )
    db_session.commit()

    from app.models.controlled_web import WebRequestUsage

    rows = db_session.scalars(select(WebRequestUsage)).all()
    assert len(rows) == 1
    assert rows[0].query_hash is not None
    assert rows[0].query_hash != "secret family name query"
    # detail_json must not leak the raw query
    assert "secret family name query" not in str(rows[0].detail_json)


# ---- agent tool disclosure mirrors policy ----------------------------------


def test_agent_tools_enabled_when_both_opt_ins(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-owner18")
    create_space_member(db_session, space.id, owner.id, role="owner")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id)
    db_session.commit()

    assert controlled_web.agent_tools_enabled(
        db_session, account_id=owner.account.id, space_id=space.id
    ) is True


def test_default_allowlist_excludes_web_tools_when_disabled(db_session, monkeypatch):
    """Assistant default allowlist never advertises Web tools unless both opt-ins hold."""
    from app.services import agent_tools

    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", False)
    owner, space = create_agent_fixture(db_session, name="web-owner19")
    create_space_member(db_session, space.id, owner.id, role="owner")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id)
    db_session.commit()

    allowlist = agent_tools.default_allowlist(
        "assistant", db_session, account_id=owner.account.id, space_id=space.id
    )
    assert agent_tools.TOOL_SEARCH_WEB not in allowlist
    assert agent_tools.TOOL_FETCH_APPROVED_PAGE not in allowlist


def test_default_allowlist_includes_web_tools_when_enabled(db_session, monkeypatch):
    from app.services import agent_tools

    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-owner20")
    create_space_member(db_session, space.id, owner.id, role="owner")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id)
    db_session.commit()

    allowlist = agent_tools.default_allowlist(
        "assistant", db_session, account_id=owner.account.id, space_id=space.id
    )
    assert agent_tools.TOOL_SEARCH_WEB in allowlist
    assert agent_tools.TOOL_FETCH_APPROVED_PAGE in allowlist
    # V2.3 kinship tool must still be present (regression guard)
    assert agent_tools.TOOL_RECORD_TERM_USAGE in allowlist


def test_default_allowlist_backwards_compatible_without_db():
    """Callers without a db scope still get the static assistant toolset."""
    from app.services import agent_tools

    allowlist = agent_tools.default_allowlist("assistant")
    assert agent_tools.TOOL_RECORD_TERM_USAGE in allowlist
    assert agent_tools.TOOL_SEARCH_WEB not in allowlist


# ---- AC-W3 query PII / secret minimization ----------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "contact 13800138000 please",
        "tel:+8613912345678",
        "email me at someone@example.com",
        "user password: AKIAIOSFODNN7EXAMPLE",
        "bearer dGhpcyBpcyBhIGxvbmcgc2VjcmV0",
        "token=abcdef0123456789abcdef0123456789",
        "value is [masked] from record",
        "field *** redacted",
        "11010119900307891X",
    ],
)
def test_search_blocks_pii_and_secret_queries(query, db_session, monkeypatch):
    """PII / secret / masked values must never reach the provider (AC-W3)."""
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-pii")
    create_space_member(db_session, space.id, owner.id, role="owner")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id)
    db_session.commit()
    _patch_dns(monkeypatch)
    _patch_provider_search(monkeypatch)  # must never be called for blocked queries

    with pytest.raises(WebGatewayError) as exc:
        controlled_web.search_web(
            db_session,
            account_id=owner.account.id,
            space_id=space.id,
            run_id=None,
            query=query,
            use_case="research",
            limit=5,
        )
    assert exc.value.code == WEB_QUERY_BLOCKED
    # a blocked query must not record a succeeded usage nor consume quota
    from app.models.controlled_web import WebRequestUsage

    assert db_session.scalars(select(WebRequestUsage)).all() == []


def test_search_blocks_clustered_address_query(db_session, monkeypatch):
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-addr")
    create_space_member(db_session, space.id, owner.id, role="owner")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id)
    db_session.commit()
    _patch_dns(monkeypatch)
    _patch_provider_search(monkeypatch)

    with pytest.raises(WebGatewayError) as exc:
        controlled_web.search_web(
            db_session,
            account_id=owner.account.id,
            space_id=space.id,
            run_id=None,
            query="查找 北京市海淀区中关村路1号 附近的人",
            use_case="research",
            limit=5,
        )
    assert exc.value.code == WEB_QUERY_BLOCKED


def test_search_accepts_safe_query(db_session, monkeypatch):
    """A benign genealogy query is forwarded to the provider unchanged."""
    monkeypatch.setattr(config, "CONTROLLED_WEB_ENABLED", True)
    owner, space = create_agent_fixture(db_session, name="web-safe")
    create_space_member(db_session, space.id, owner.id, role="owner")
    _enable_platform(db_session)
    _enable_space(db_session, space_id=space.id)
    db_session.commit()
    _patch_dns(monkeypatch)
    received: dict[str, object] = {}

    def _capture(endpoint, secret, query, limit):
        received["query"] = query
        return _fake_search_results()

    monkeypatch.setattr(controlled_web, "_provider_search", _capture)

    result = controlled_web.search_web(
        db_session,
        account_id=owner.account.id,
        space_id=space.id,
        run_id=None,
        query="  Qing dynasty genealogy customs  ",
        use_case="research",
        limit=5,
    )
    assert result["results"]
    assert received["query"] == "Qing dynasty genealogy customs"  # stripped only
