"""Provider 代理端点（P1 唯一 egress）回归。

覆盖：run token 认证与 scope 核验、Run 活跃门禁、服务端解密转发、
成功流式透传与用量审计、上游错误脱敏、Provider 不可用 fail-closed。
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.services import provider_proxy
from app.services.agent_tokens import issue_service_token


class _FakeUpstream:
    def __init__(self, chunks: list[bytes], status_code: int = 200) -> None:
        self._chunks = chunks
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self.closed = False

    async def aiter_raw(self):
        for chunk in self._chunks:
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


def _install_fake(monkeypatch, chunks: list[bytes], status_code: int = 200) -> None:
    _FakeAsyncClient.response = _FakeUpstream(chunks, status_code)
    _FakeAsyncClient.raise_on_send = None
    monkeypatch.setattr(provider_proxy.httpx, "AsyncClient", _FakeAsyncClient)


def _seed_provider(db_session, *, name: str):
    """user + space + enabled provider（openai_compatible，密文落库）。"""
    from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
    from app.utils import secretbox, timeutil

    user = __import__("conftest").create_user_with_pin(db_session, f"{name}-u", "123456")
    from app.models.space import FamilySpace

    space = FamilySpace(
        name=f"{name}-space", kind="household", owner_id=user.id, created_at=user.created_at
    )
    db_session.add(space)
    db_session.flush()
    provider = AgentProvider(
        name=f"{name}-p",
        kind="openai_compatible",
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

    request_body = b'{"model": "model-x", "messages": [{"role": "user", "content": "hi"}]}'
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
    from conftest import create_agent_session

    from app.services import agent_queue

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
    from conftest import create_agent_session

    from app.services import agent_queue

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
    from conftest import create_agent_session

    from app.models.space import FamilySpace
    from app.services import agent_queue

    space = FamilySpace(
        name="proxy-none-space", kind="household", owner_id=user.id, created_at=user.created_at
    )
    db_session.add(space)
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
    from conftest import create_agent_session

    from app.services import agent_queue

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
    from conftest import create_agent_session

    from app.services import agent_queue

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
        headers={"Authorization": f"Bearer {run_token}"},
    )
    assert response.status_code == 502
    assert "boom" not in response.text
