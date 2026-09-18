"""Provider 代理端点（P1 唯一 egress）回归。

覆盖：run token 认证与 scope 核验、Run 活跃门禁、服务端解密转发、
成功流式透传与用量审计、上游错误脱敏、Provider 不可用 fail-closed。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from sqlalchemy import select

from app.models.agent import AgentRun
from app.models.audit_log import AuditLog
from app.services import agent_queue, provider_proxy
from app.services.agent_tokens import issue_service_token


class _FakeUpstream:
    def __init__(
        self,
        chunks: list[bytes],
        status_code: int = 200,
        *,
        stream_error: Exception | None = None,
        stream_error_after: int = 0,
        on_chunk: Any = None,
    ) -> None:
        self._chunks = chunks
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self.closed = False
        # 注入 headers 之后的流中断（E-AC3：流中断必须留安全终态）。
        self._stream_error = stream_error
        self._stream_error_after = stream_error_after
        # 逐块回调：用于在流中改变服务端权威状态（取消/失租）。
        self._on_chunk = on_chunk

    async def aiter_raw(self):
        for index, chunk in enumerate(self._chunks):
            if self._stream_error is not None and index == self._stream_error_after:
                raise self._stream_error
            if self._on_chunk is not None:
                await self._on_chunk(index)
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class _FakeAsyncClient:
    """替换 provider_proxy.httpx.AsyncClient：捕获转发请求并返回预设响应。"""

    last: dict[str, Any] | None = None
    response: _FakeUpstream
    raise_on_send: Exception | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _FakeAsyncClient.last_request = None

    def build_request(self, method: str, url: str, content: Any = None, headers: Any = None):
        return {"method": method, "url": url, "content": content, "headers": dict(headers or {})}

    async def send(self, request: Any, *, stream: bool = False):
        if _FakeAsyncClient.raise_on_send is not None:
            raise _FakeAsyncClient.raise_on_send
        _FakeAsyncClient.last = request
        return _FakeAsyncClient.response

    async def aclose(self) -> None:
        self.closed = True


def _install_fake(
    monkeypatch,
    chunks: list[bytes],
    status_code: int = 200,
    **upstream_kwargs: Any,
) -> None:
    _FakeAsyncClient.response = _FakeUpstream(chunks, status_code, **upstream_kwargs)
    _FakeAsyncClient.raise_on_send = None
    _FakeAsyncClient.last = None
    monkeypatch.setattr(provider_proxy.httpx, "AsyncClient", _FakeAsyncClient)


def _egress_rows(db_session, run_id: int) -> list[AuditLog]:
    """按 run 取 egress 审计行（target_id 即 run_id），保持写入顺序。"""
    return list(
        db_session.scalars(
            select(AuditLog)
            .where(AuditLog.action == "agent_provider_egress", AuditLog.target_id == run_id)
            .order_by(AuditLog.id)
        ).all()
    )


def _seed_provider(db_session, *, name: str, api: str = "openai-completions"):
    """user + space + enabled provider（openai_compatible，密文落库）。"""
    from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
    from app.utils import secretbox, timeutil

    user = __import__("conftest").create_user_with_pin(db_session, f"{name}-u", "123456")
    from app.models.space import FamilySpace, SpaceMember

    space = FamilySpace(
        name=f"{name}-space", kind="household", owner_id=user.id, created_at=user.created_at
    )
    db_session.add(space)
    db_session.flush()
    # 运行时授权按 active membership 判定，空间必须有本空间管理员成员行。
    db_session.add(
        SpaceMember(
            space_id=space.id,
            user_id=user.id,
            added_by=user.id,
            role="space_admin",
            status="active",
            created_at=user.created_at,
            updated_at=user.created_at,
        )
    )
    db_session.flush()
    provider = AgentProvider(
        name=f"{name}-p",
        kind="openai_compatible",
        api=api,
        base_url="https://api.example.com/v1",
        secret_ciphertext=secretbox.encrypt_secret("sk-real-secret-value"),
        allowed_models_json=["model-x"],
        enabled=True,
        created_at=timeutil.utcnow(),
        updated_at=timeutil.utcnow(),
    )
    db_session.add(provider)
    db_session.flush()
    db_session.add(
        AgentSpaceProviderSetting(
            space_id=space.id,
            provider_id=provider.id,
            model="model-x",
            cloud_allowed=True,
            local_required=False,
            enabled=True,
        )
    )
    db_session.commit()
    return user, space, provider


def _lease_run_token(client, kind: str = "assistant") -> tuple[int, str]:
    response = client.post(
        "/internal/agent/jobs/lease",
        json={"kind": kind, "leased_by": "proxy-test"},
        headers={"Authorization": f"Bearer {issue_service_token()}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body["run_id"], body["run_token"]


def test_proxy_streams_upstream_and_audits_usage(internal_client, db_session, monkeypatch):
    """成功路径：服务端解密转发，响应透传，字节数落 agent_provider_egress 审计。"""
    _install_fake(monkeypatch, [b'{"id": ', b'"cmpl-1"}'])
    user, space, provider = _seed_provider(db_session, name="proxy-ok")
    from conftest import create_agent_session

    session_row = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    from app.services import agent_queue

    run = agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    db_session.commit()
    run_id, token = _lease_run_token(internal_client)
    assert run_id == run.id

    request_body = (
        b'{"model": "model-x", "messages": [{"role": "user", "content": "hi"}], ' b'"stream": true}'
    )
    response = internal_client.post(
        f"/internal/agent/runs/{run_id}/provider/chat/completions",
        content=request_body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.content == b'{"id": "cmpl-1"}'

    # 转发目标：真实 base_url + 真实凭据（不出现在 sidecar 视野）
    forwarded = _FakeAsyncClient.last
    assert forwarded is not None
    assert forwarded["url"] == "https://api.example.com/v1/chat/completions"
    assert forwarded["headers"]["Authorization"] == "Bearer sk-real-secret-value"
    assert forwarded["content"] == request_body

    audit_row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "agent_provider_egress")
    )
    assert audit_row is not None
    assert audit_row.detail["bytes_read"] == len(b'{"id": "cmpl-1"}')
    assert audit_row.detail["status"] == "succeeded"
    assert "sk-real-secret-value" not in (audit_row.detail_json or "")  # type: ignore[operator]


def test_proxy_requires_run_token(internal_client, db_session, monkeypatch):
    """无 token / 用户 JWT / token 与 run 不匹配一律 fail-closed。"""
    _install_fake(monkeypatch, [b"{}"])
    user, space, _provider = _seed_provider(db_session, name="proxy-auth")
    from app.services import agent_queue
    from conftest import create_agent_session

    session_row = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    db_session.commit()

    # 无 token → 401
    assert (
        internal_client.post(f"/internal/agent/runs/{run.id}/provider/chat/completions").status_code
        == 401
    )
    # 用户 JWT → 403（internal 协议拒绝浏览器身份；登录走公开 client，
    # 该负向由 test_internal_agent_api::test_user_jwt_forbidden_on_internal_routes 覆盖）
    garbage = internal_client.post(
        f"/internal/agent/runs/{run.id}/provider/chat/completions",
        headers={"Authorization": "Bearer not-a-token"},
    )
    assert garbage.status_code == 401
    # run_id 不匹配 → 403
    run_id, token = _lease_run_token(internal_client)
    other = internal_client.post(
        f"/internal/agent/runs/{run_id + 99999}/provider/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert other.status_code == 403


def test_proxy_rejects_non_active_run(internal_client, db_session, monkeypatch):
    """Run 未 lease（queued）→ 409；模型调用仅活跃期允许。"""
    _install_fake(monkeypatch, [b"{}"])
    user, space, _provider = _seed_provider(db_session, name="proxy-state")
    from app.services import agent_queue
    from conftest import create_agent_session

    session_row = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    db_session.commit()
    token = issue_service_token()
    # 直接构造同 scope run token：lease 才会签发，这里用 service token 打 run 端点 401，
    # 改走 lease（作业 kind=assistant 无 queued run 可租）——为控状态，先 lease 再手动复位
    lease = internal_client.post(
        "/internal/agent/jobs/lease",
        json={"kind": "assistant", "leased_by": "t"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert lease.status_code == 200
    run_token = lease.json()["run_token"]
    db_session.expire_all()  # lease 由 endpoint 会话提交，先失效本会话缓存
    run2 = db_session.get(type(run), lease.json()["run_id"])
    run2.status = "queued"
    db_session.commit()

    response = internal_client.post(
        f"/internal/agent/runs/{run2.id}/provider/chat/completions",
        headers={"Authorization": f"Bearer {run_token}"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AGENT_RUN_NOT_RUNNING"


def test_proxy_fail_closed_when_provider_unresolved(internal_client, db_session, monkeypatch):
    """无 Provider 配置/解密失败 → 503 可解释拒绝，绝不回退 env。"""
    _install_fake(monkeypatch, [b"{}"])
    user = __import__("conftest").create_user_with_pin(db_session, "proxy-none-u", "123456")
    from app.models.space import FamilySpace
    from app.services import agent_queue
    from conftest import create_agent_session

    space = FamilySpace(
        name="proxy-none-space", kind="household", owner_id=user.id, created_at=user.created_at
    )
    db_session.add(space)
    db_session.flush()
    from app.models.space import SpaceMember

    db_session.add(
        SpaceMember(
            space_id=space.id,
            user_id=user.id,
            added_by=user.id,
            role="space_admin",
            status="active",
            created_at=user.created_at,
            updated_at=user.created_at,
        )
    )
    db_session.flush()
    session_row = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    db_session.commit()
    run_id, run_token = _lease_run_token(internal_client)

    response = internal_client.post(
        f"/internal/agent/runs/{run_id}/provider/chat/completions",
        content=b'{"model":"model-x","messages":[],"stream":true}',
        headers={"Authorization": f"Bearer {run_token}"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AGENT_PROVIDER_PROXY_UNAVAILABLE"


def test_proxy_redacts_upstream_error_body(internal_client, db_session, monkeypatch):
    """上游 4xx/5xx → 502 通用错误体；上游 body（含 secret 形文本）不透传。"""
    _install_fake(
        monkeypatch,
        [b'{"error": {"message": "invalid key sk-real-secret-value"}}'],
        status_code=500,
    )
    user, space, _provider = _seed_provider(db_session, name="proxy-err")
    from app.services import agent_queue
    from conftest import create_agent_session

    session_row = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    db_session.commit()
    run_id, run_token = _lease_run_token(internal_client)

    response = internal_client.post(
        f"/internal/agent/runs/{run_id}/provider/chat/completions",
        content=b'{"model":"model-x","messages":[],"stream":true}',
        headers={"Authorization": f"Bearer {run_token}"},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "AGENT_PROVIDER_PROXY_UNAVAILABLE"
    assert b"sk-real-secret-value" not in response.content
    audit_row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "agent_provider_egress")
    )
    assert audit_row is not None and audit_row.detail["status"] == "failed"


def test_proxy_maps_network_failure_to_502(internal_client, db_session, monkeypatch):
    """连接失败 → 502 AGENT_PROVIDER_PROXY_UNAVAILABLE（不泄漏异常细节）。"""
    _FakeAsyncClient.raise_on_send = httpx.ConnectError("boom 172.17.0.9")
    _FakeAsyncClient.response = _FakeUpstream([b"{}"])
    monkeypatch.setattr(provider_proxy.httpx, "AsyncClient", _FakeAsyncClient)
    user, space, _provider = _seed_provider(db_session, name="proxy-net")
    from app.services import agent_queue
    from conftest import create_agent_session

    session_row = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    db_session.commit()
    run_id, run_token = _lease_run_token(internal_client)

    response = internal_client.post(
        f"/internal/agent/runs/{run_id}/provider/chat/completions",
        content=b'{"model":"model-x","messages":[],"stream":true}',
        headers={"Authorization": f"Bearer {run_token}"},
    )
    assert response.status_code == 502
    assert "boom" not in response.text


def test_proxy_rejects_empty_body_before_upstream(internal_client, db_session, monkeypatch):
    """空 body fail-closed，不能创建上游 client 或发出请求。"""
    from app.services import agent_queue
    from conftest import create_agent_session

    user, space, _provider = _seed_provider(db_session, name="proxy-empty-real")
    session_row = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    db_session.commit()
    run_id, run_token = _lease_run_token(internal_client)
    called = False

    class _ShouldNotConstruct:
        def __init__(self, *args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("upstream client must not be constructed")

    monkeypatch.setattr(provider_proxy.httpx, "AsyncClient", _ShouldNotConstruct)
    response = internal_client.post(
        f"/internal/agent/runs/{run_id}/provider/chat/completions",
        headers={"Authorization": f"Bearer {run_token}"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AGENT_PROVIDER_REQUEST_INVALID"
    assert called is False


def test_proxy_rejects_non_object_json_before_upstream(internal_client, db_session, monkeypatch):
    """JSON 数组等非 OpenAI 请求对象不得触发上游请求。"""
    user, space, _provider = _seed_provider(db_session, name="proxy-array")
    from conftest import create_agent_session

    session_row = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    db_session.commit()
    run_id, run_token = _lease_run_token(internal_client)
    called = False

    class _ShouldNotConstruct:
        def __init__(self, *args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("upstream client must not be constructed")

    monkeypatch.setattr(provider_proxy.httpx, "AsyncClient", _ShouldNotConstruct)
    response = internal_client.post(
        f"/internal/agent/runs/{run_id}/provider/chat/completions",
        content=b"[]",
        headers={"Authorization": f"Bearer {run_token}"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AGENT_PROVIDER_REQUEST_INVALID"
    assert called is False


def test_proxy_rejects_cancelled_run_before_upstream(internal_client, db_session, monkeypatch):
    """取消竞态由 Gateway 二次门禁收口，不能再建立上游 client。"""
    user, space, _provider = _seed_provider(db_session, name="proxy-cancel")
    from conftest import create_agent_session

    session_row = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    from app.services import agent_queue

    agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    db_session.commit()
    run_id, run_token = _lease_run_token(internal_client)
    db_session.expire_all()
    run = db_session.get(AgentRun, run_id)
    assert run is not None
    run.cancel_requested = True
    db_session.commit()

    called = False

    class _ShouldNotConstruct:
        def __init__(self, *args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("cancelled run must not construct upstream client")

    monkeypatch.setattr(provider_proxy.httpx, "AsyncClient", _ShouldNotConstruct)
    response = internal_client.post(
        f"/internal/agent/runs/{run_id}/provider/chat/completions",
        content=b'{"model":"model-x","messages":[],"stream":true}',
        headers={"Authorization": f"Bearer {run_token}"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AGENT_RUN_NOT_RUNNING"
    assert called is False


def test_proxy_binds_model_and_stream_contract_before_upstream(
    internal_client, db_session, monkeypatch
):
    """有效 run token 也不能改模型或绕过流式/输出上限约束。"""
    user, space, _provider = _seed_provider(db_session, name="proxy-payload-contract")
    from conftest import create_agent_session

    session_row = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    db_session.commit()
    run_id, run_token = _lease_run_token(internal_client)
    called = False

    class _ShouldNotConstruct:
        def __init__(self, *args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("invalid provider payload must not construct upstream client")

    monkeypatch.setattr(provider_proxy.httpx, "AsyncClient", _ShouldNotConstruct)
    for payload in (
        b'{"model":"other-model","messages":[],"stream":true}',
        b'{"model":"model-x","messages":[],"stream":false}',
        b'{"model":"model-x","messages":[],"stream":true,"max_tokens":60001}',
    ):
        response = internal_client.post(
            f"/internal/agent/runs/{run_id}/provider/chat/completions",
            content=payload,
            headers={"Authorization": f"Bearer {run_token}"},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "AGENT_PROVIDER_REQUEST_INVALID"
    assert called is False


def test_proxy_rejects_route_protocol_mismatch_before_upstream(
    internal_client, db_session, monkeypatch
):
    """A completions snapshot cannot be invoked through the Responses route."""
    _install_fake(monkeypatch, [b"{}"])
    user, space, _provider = _seed_provider(db_session, name="proxy-route-mismatch")
    from conftest import create_agent_session

    session_row = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    db_session.commit()
    run_id, run_token = _lease_run_token(internal_client)
    response = internal_client.post(
        f"/internal/agent/runs/{run_id}/provider/responses",
        content=b'{"model":"model-x","messages":[],"stream":true}',
        headers={"Authorization": f"Bearer {run_token}"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AGENT_PROVIDER_REQUEST_INVALID"

    # The inverse mismatch is rejected as well for a Responses profile.
    user2, space2, _provider2 = _seed_provider(
        db_session, name="proxy-route-mismatch-responses", api="openai-responses"
    )
    session2 = create_agent_session(db_session, account_id=user2.account.id, space_id=space2.id)
    agent_queue.enqueue_run(
        db_session,
        agent_session=session2,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    db_session.commit()
    run_id2, run_token2 = _lease_run_token(internal_client)
    response2 = internal_client.post(
        f"/internal/agent/runs/{run_id2}/provider/chat/completions",
        content=b'{"model":"model-x","messages":[],"stream":true}',
        headers={"Authorization": f"Bearer {run_token2}"},
    )
    assert response2.status_code == 422
    assert response2.json()["error"]["code"] == "AGENT_PROVIDER_REQUEST_INVALID"


# ---- E：错误分类、恰好一次审计与重试治理 ----


def _provider_call(internal_client, run_id: int, token: str, *, api: str = "chat/completions"):
    return internal_client.post(
        f"/internal/agent/runs/{run_id}/provider/{api}",
        content=b'{"model":"model-x","messages":[],"stream":true}',
        headers={"Authorization": f"Bearer {token}"},
    )


def _leased_run(internal_client, db_session, name: str):
    """建 provider + session + run 并 lease，返回 (run_id, token)。"""
    user, space, _provider = _seed_provider(db_session, name=name)
    from conftest import create_agent_session

    session_row = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    agent_queue.enqueue_run(
        db_session,
        agent_session=session_row,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=[],
    )
    db_session.commit()
    return _lease_run_token(internal_client)


import pytest  # noqa: E402


@pytest.mark.parametrize("upstream_status", [400, 401, 403, 404, 422])
def test_proxy_marks_permanent_upstream_rejection_non_retryable(
    internal_client, db_session, monkeypatch, upstream_status
):
    """E-AC1：上游永久拒绝不得伪装为可重试 5xx，且留安全终态。"""
    _install_fake(
        monkeypatch,
        [b'{"error": {"message": "invalid key sk-real-secret-value"}}'],
        status_code=upstream_status,
    )
    run_id, token = _leased_run(internal_client, db_session, f"proxy-perm-{upstream_status}")

    response = _provider_call(internal_client, run_id, token)
    # 非 2xx 且不是 502：sidecar 的请求层与 SDK 都不会把 4xx 当作可重试。
    assert response.status_code == upstream_status, response.text
    assert response.json()["error"]["code"] == "AGENT_PROVIDER_UPSTREAM_REJECTED"
    # 显式重试提示：请求层即便面对 5xx 形状也会立即停止。
    assert response.headers.get("x-should-retry") == "false"
    # 上游错误体（含 secret 形文本）绝不透传。
    assert b"sk-real-secret-value" not in response.content

    rows = _egress_rows(db_session, run_id)
    assert len(rows) == 1, "每次出站尝试恰好一条终态审计"
    detail = rows[0].detail
    assert detail["status"] == "failed"
    assert detail["upstream_status"] == upstream_status
    assert detail["error_class"] == "upstream_rejected"
    assert detail["retryable"] is False
    assert detail["sent"] is True
    assert "sk-real-secret-value" not in rows[0].detail_json


@pytest.mark.parametrize("upstream_status", [408, 409, 429, 500, 503])
def test_proxy_keeps_transient_upstream_errors_retryable(
    internal_client, db_session, monkeypatch, upstream_status
):
    """E-AC1：暂时性上游错误维持既有可重试形状（502 通用体，不附带停止提示）。"""
    _install_fake(monkeypatch, [b'{"error": "busy"}'], status_code=upstream_status)
    run_id, token = _leased_run(internal_client, db_session, f"proxy-trans-{upstream_status}")

    response = _provider_call(internal_client, run_id, token)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "AGENT_PROVIDER_PROXY_UNAVAILABLE"
    assert response.headers.get("x-should-retry") is None

    rows = _egress_rows(db_session, run_id)
    assert len(rows) == 1
    assert rows[0].detail["error_class"] == "upstream_transient"
    assert rows[0].detail["retryable"] is True
    assert rows[0].detail["upstream_status"] == upstream_status


def test_proxy_audits_connection_failure_as_not_sent(internal_client, db_session, monkeypatch):
    """E-AC3：连接未建立也要留安全终态，并如实标注未发送与可重试。"""
    _FakeAsyncClient.raise_on_send = httpx.ConnectError("boom 172.17.0.9")
    _FakeAsyncClient.response = _FakeUpstream([b"{}"])
    monkeypatch.setattr(provider_proxy.httpx, "AsyncClient", _FakeAsyncClient)
    run_id, token = _leased_run(internal_client, db_session, "proxy-connect-audit")

    response = _provider_call(internal_client, run_id, token)
    assert response.status_code == 502
    assert "boom" not in response.text

    rows = _egress_rows(db_session, run_id)
    assert len(rows) == 1
    detail = rows[0].detail
    assert detail["status"] == "failed"
    assert detail["upstream_status"] is None
    assert detail["error_class"] == "transport_failure"
    assert detail["retryable"] is True
    assert detail["sent"] is False


def _drive_passthrough(
    db_session, *, run_id: int, provider_id: int, upstream: Any, client: Any
) -> tuple[list[bytes], BaseException | None]:
    """直接驱动流式透传生成器（不经 ASGI）：观察逐块结果与恰好一次的审计。

    端点层在流中异常时会断开响应（真实 uvicorn 行为），TestClient 拿不到部分
    正文；这里在 service 边界验证同一份代码，两种边界各测各的。
    """
    run = db_session.get(AgentRun, run_id)
    assert run is not None
    gen = provider_proxy.passthrough_with_audit(
        db_session,
        run=run,
        provider_id=provider_id,
        client=client,
        upstream=upstream,
        on_finish=db_session.commit,
    )
    chunks: list[bytes] = []
    error: BaseException | None = None

    async def drive() -> None:
        nonlocal error
        try:
            async for chunk in gen:
                chunks.append(chunk)
        except BaseException as exc:  # noqa: BLE001 - 测试需保留原异常
            error = exc
        finally:
            await gen.aclose()

    asyncio.run(drive())
    return chunks, error


def test_proxy_audits_stream_interruption_once(internal_client, db_session, monkeypatch):
    """E-AC3：headers 之后流中断只记一次，且标为已发送（上游可能已处理）。"""
    _install_fake(
        monkeypatch,
        [b'{"id": "cmpl-1"}', b'{"more": true}'],
        stream_error=httpx.ReadError("connection lost mid-stream"),
        stream_error_after=1,
    )
    run_id, _token = _leased_run(internal_client, db_session, "proxy-stream-break")
    db_session.expire_all()

    chunks, error = _drive_passthrough(
        db_session,
        run_id=run_id,
        provider_id=1,
        upstream=_FakeAsyncClient.response,
        client=_FakeAsyncClient(),
    )
    # 中断前的块已透传给 sidecar（真实 uvicorn 下响应被截断）。
    assert chunks == [b'{"id": "cmpl-1"}']
    assert isinstance(error, httpx.ReadError)

    rows = _egress_rows(db_session, run_id)
    assert len(rows) == 1, "流中断不得双记"
    detail = rows[0].detail
    assert detail["status"] == "failed"
    assert detail["error_class"] == "stream_interrupted"
    assert detail["sent"] is True
    assert detail["retryable"] is False


def test_proxy_audits_cancellation_during_stream_once(internal_client, db_session, monkeypatch):
    """E-AC3：流中取消/失租记 run_cancelled，恰好一次，不冒充上游错误。"""
    from app.db import SessionLocal

    async def cancel_between_chunks(index: int) -> None:
        if index != 0:
            return
        with SessionLocal() as other:
            fresh = other.get(AgentRun, run_id_holder[0])
            assert fresh is not None
            fresh.cancel_requested = True
            other.commit()

    run_id_holder = [0]
    _install_fake(monkeypatch, [b"a" * 8, b"b" * 8], on_chunk=cancel_between_chunks)
    run_id, _token = _leased_run(internal_client, db_session, "proxy-cancel-stream")
    run_id_holder[0] = run_id
    db_session.expire_all()

    chunks, error = _drive_passthrough(
        db_session,
        run_id=run_id,
        provider_id=1,
        upstream=_FakeAsyncClient.response,
        client=_FakeAsyncClient(),
    )
    # 取消在首块转发前的边界复核处生效：零块透传，且不双记。
    assert chunks == []
    assert isinstance(error, provider_proxy.ProviderProxyError)

    rows = _egress_rows(db_session, run_id)
    assert len(rows) == 1
    detail = rows[0].detail
    assert detail["status"] == "failed"
    assert detail["error_class"] == "run_cancelled"
    assert detail["retryable"] is False
    assert detail["sent"] is True
