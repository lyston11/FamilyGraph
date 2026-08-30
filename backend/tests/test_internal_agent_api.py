"""Internal Agent HTTP 协议测试：两级认证、fail-closed 审计、feature flag。"""

from conftest import (
    auth_header,
    create_agent_fixture,
    create_agent_message,
    create_agent_session,
    login,
)
from sqlalchemy import select

from app import config
from app.models.audit_log import AuditLog
from app.services import agent_queue
from app.services.agent_tokens import decode_run_token, issue_run_token, issue_service_token


def _seed(db, *, name: str):
    user, space = create_agent_fixture(db, name=name)
    agent_session = create_agent_session(db, account_id=user.account.id, space_id=space.id)
    message = create_agent_message(db, agent_session)
    run = agent_queue.enqueue_run(
        db,
        agent_session=agent_session,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=["familygraph.echo"],
        message=message,
    )
    return user, space, agent_session, run


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_user_jwt_forbidden_on_internal_routes(client, internal_client, db_session):
    """用户 JWT 打 internal 一律 403 并写安全审计（RT-3）。"""
    _seed(db_session, name="jwt")
    # 登录走公开 app；internal listener 上不存在任何公开路由
    token_pair = login(client, "jwt", "123456").json()
    response = internal_client.get(
        "/internal/agent/runs/1/context", headers=auth_header(token_pair)
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AGENT_INTERNAL_FORBIDDEN"
    audit_row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "agent_internal_authz_denied")
    )
    assert audit_row is not None

    lease_response = internal_client.post(
        "/internal/agent/jobs/lease",
        json={"kind": "assistant", "leased_by": "sc"},
        headers=auth_header(token_pair),
    )
    assert lease_response.status_code == 403


def test_missing_or_garbage_token_unauthorized(internal_client, db_session):
    _seed(db_session, name="noauth")
    assert internal_client.get("/internal/agent/runs/1/context").status_code == 401
    garbage = internal_client.post(
        "/internal/agent/jobs/lease",
        json={"kind": "assistant", "leased_by": "sc"},
        headers=_auth("not-a-valid-token"),
    )
    assert garbage.status_code == 401
    assert garbage.json()["error"]["code"] == "AGENT_TOKEN_INVALID"


def test_service_token_cannot_access_run_endpoints(internal_client, db_session):
    """token 类型混淆 fail-closed：service token 只可 lease。"""
    _, _, _, run = _seed(db_session, name="confuse")
    service_token = issue_service_token()
    response = internal_client.get(
        f"/internal/agent/runs/{run.id}/context", headers=_auth(service_token)
    )
    assert response.status_code == 401


def test_lease_flow_happy_path(internal_client, db_session):
    """lease → 200 合同字段齐全；再次 lease → 204 无可租。"""
    _, _, _, run = _seed(db_session, name="leaseflow")
    first = internal_client.post(
        "/internal/agent/jobs/lease",
        json={"kind": "assistant", "leased_by": "sidecar-a"},
        headers=_auth(issue_service_token()),
    )
    assert first.status_code == 200
    body = first.json()
    assert body["run_id"] == run.id
    assert body["agent_kind"] == "assistant"
    assert body["attempt"] == 1
    assert body["tool_allowlist"] == ["familygraph.echo"]
    assert body["policy_version"] == "p1"
    claims = decode_run_token(body["run_token"])
    assert claims["run_id"] == run.id and claims["job_id"] == body["job_id"]

    second = internal_client.post(
        "/internal/agent/jobs/lease",
        json={"kind": "assistant", "leased_by": "sidecar-b"},
        headers=_auth(issue_service_token()),
    )
    assert second.status_code == 204


def test_lease_without_kind_returns_any_queued(internal_client, db_session):
    """兼容旧 sidecar：省略 kind 时默认只租 Assistant 队列。"""
    _, _, _, run = _seed(db_session, name="noskind")
    response = internal_client.post(
        "/internal/agent/jobs/lease",
        json={"leased_by": "sc"},
        headers=_auth(issue_service_token()),
    )
    assert response.status_code == 200
    assert response.json()["run_id"] == run.id


def test_http_lease_cannot_consume_steward_queue(internal_client, db_session):
    """Steward leasing is reserved for the in-process canonical worker."""
    user, space = create_agent_fixture(db_session, name="steward-http")
    session = create_agent_session(
        db_session, account_id=user.account.id, space_id=space.id, kind="steward"
    )
    agent_queue.enqueue_run(
        db_session,
        agent_session=session,
        kind="steward",
        policy_version="p1",
        tool_allowlist=[],
    )
    for payload in ({"kind": "steward", "leased_by": "sc"}, {"kind": None, "leased_by": "sc"}):
        response = internal_client.post(
            "/internal/agent/jobs/lease",
            json=payload,
            headers=_auth(issue_service_token()),
        )
        assert response.status_code in (403, 422)


def test_heartbeat_scope_mismatch_fail_closed(internal_client, db_session):
    """用别的 run 的 token 打 heartbeat：scope 不匹配拒绝 + 审计。"""
    _, _, _, run_a = _seed(db_session, name="hba")
    _, _, _, run_b = _seed(db_session, name="hbb")
    grant_a_response = internal_client.post(
        "/internal/agent/jobs/lease",
        json={"kind": "assistant", "leased_by": "sc"},
        headers=_auth(issue_service_token()),
    )
    assert grant_a_response.status_code == 200
    token_a = grant_a_response.json()["run_token"]

    job_b_id = run_b.job_id
    assert job_b_id is not None
    mismatch = internal_client.post(
        f"/internal/agent/jobs/{job_b_id}/heartbeat",
        headers=_auth(token_a),
    )
    assert mismatch.status_code == 403
    assert mismatch.json()["error"]["code"] == "AGENT_TOKEN_SCOPE_MISMATCH"

    ok = internal_client.post(
        f"/internal/agent/jobs/{run_a.job_id}/heartbeat", headers=_auth(token_a)
    )
    assert ok.status_code == 200
    assert ok.json()["ok"] is True


def test_context_returns_projection_without_secrets(internal_client, db_session):
    """context 返回 scope/消息投影/provider 解析；密钥明文与密文均不出现。"""
    from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
    from app.models.context import ContextBuild
    from app.utils import secretbox, timeutil

    # Configure the provider before enqueueing: the run snapshot pins the
    # decision at creation and a later configuration change must not revive a
    # denied/no-provider run.
    user, space = create_agent_fixture(db_session, name="ctx")
    provider = AgentProvider(
        name="main",
        kind="openai_compatible",
        base_url="https://api.example.com/v1",
        secret_ciphertext=secretbox.encrypt_secret("sk-super-secret-value"),
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
    agent_session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    message = create_agent_message(db_session, agent_session)
    run = agent_queue.enqueue_run(
        db_session,
        agent_session=agent_session,
        kind="assistant",
        policy_version="p1",
        tool_allowlist=["familygraph.echo"],
        message=message,
    )

    grant_response = internal_client.post(
        "/internal/agent/jobs/lease",
        json={"kind": "assistant", "leased_by": "sc"},
        headers=_auth(issue_service_token()),
    )
    assert grant_response.status_code == 200
    token = grant_response.json()["run_token"]

    response = internal_client.get(f"/internal/agent/runs/{run.id}/context", headers=_auth(token))
    assert response.status_code == 200
    body = response.text
    payload = response.json()
    assert payload["account_id"] == user.account.id
    assert payload["space_id"] == space.id
    assert [m["role"] for m in payload["messages"]] == ["user"]
    provider_view = payload["provider"]
    assert provider_view["policy_result"] == "allowed"
    assert provider_view["model"] == "model-x"
    assert provider_view["secret_ref"].startswith("agent_providers/")
    # P1 唯一 egress：context 只下发代理路径；真实 base_url/api_key 不出服务端
    assert provider_view["base_url"] == f"/internal/agent/runs/{run.id}/provider"
    assert provider_view["api_key"] is None
    assert "api.example.com" not in body
    assert "sk-super-secret-value" not in body
    assert "secret_ciphertext" not in payload["provider"]  # 密文不下发
    assert provider.secret_ciphertext not in body
    assert payload["context_build_id"] is not None
    assert (
        db_session.scalar(
            select(ContextBuild).where(ContextBuild.id == payload["context_build_id"])
        )
        is not None
    )


def test_context_returns_complete_transcript_without_recent_limit(internal_client, db_session):
    """Pi rehydration receives every durable message, not an arbitrary recent-N slice."""
    from app.models.agent import AgentMessage
    from app.utils import timeutil

    user, space, session, run = _seed(db_session, name="ctx-complete")
    for index in range(60):
        db_session.add(
            AgentMessage(
                session_id=session.id,
                role="assistant" if index % 2 else "user",
                content_json={"text": f"message-{index}"},
                idempotency_key=f"extra-{index}",
                created_at=timeutil.utcnow(),
            )
        )
    db_session.commit()
    grant = internal_client.post(
        "/internal/agent/jobs/lease",
        json={"kind": "assistant", "leased_by": "sc"},
        headers=_auth(issue_service_token()),
    )
    assert grant.status_code == 200
    token = grant.json()["run_token"]
    response = internal_client.get(f"/internal/agent/runs/{run.id}/context", headers=_auth(token))
    assert response.status_code == 200
    messages = response.json()["messages"]
    assert len(messages) == 61
    assert messages[-1]["content_json"]["text"] == "message-59"


def test_events_append_and_settle_via_api(internal_client, db_session):
    """events:append 幂等 + settle 终态 + 终态事件自动追加（RT-4）。"""
    _, _, _, run = _seed(db_session, name="evapi")
    lease_response = internal_client.post(
        "/internal/agent/jobs/lease",
        json={"kind": "assistant", "leased_by": "sc"},
        headers=_auth(issue_service_token()),
    )
    token = lease_response.json()["run_token"]

    append = internal_client.post(
        f"/internal/agent/runs/{run.id}/events/append",
        json={"events": [{"seq": 1, "type": "run.started", "public_payload": {}}]},
        headers=_auth(token),
    )
    assert append.status_code == 200
    assert [a["seq"] for a in append.json()["accepted"]] == [1]

    retry = internal_client.post(
        f"/internal/agent/runs/{run.id}/events/append",
        json={"events": [{"seq": 1, "type": "run.started", "public_payload": {}}]},
        headers=_auth(token),
    )
    assert retry.status_code == 200
    assert retry.json() == {"accepted": [], "duplicates": [1]}

    unknown = internal_client.post(
        f"/internal/agent/runs/{run.id}/events/append",
        json={"events": [{"seq": 2, "type": "card.show", "public_payload": {}}]},
        headers=_auth(token),
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "AGENT_EVENT_INVALID"

    settle = internal_client.post(
        f"/internal/agent/runs/{run.id}/settle",
        json={"status": "succeeded"},
        headers=_auth(token),
    )
    assert settle.status_code == 200
    assert settle.json()["status"] == "succeeded"

    again = internal_client.post(
        f"/internal/agent/runs/{run.id}/settle",
        json={"status": "failed", "error_code": "X"},
        headers=_auth(token),
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "AGENT_RUN_TERMINAL"


def test_feature_flag_disabled_returns_503(internal_client, db_session, monkeypatch):
    """RT-6：总开关关闭时 internal 端点一律 503 AGENT_DISABLED（默认关闭）。"""
    monkeypatch.setattr(config, "AGENT_RUNTIME_ENABLED", False)
    response = internal_client.post(
        "/internal/agent/jobs/lease",
        json={"kind": "assistant", "leased_by": "sc"},
        headers=_auth(issue_service_token()),
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AGENT_DISABLED"


def test_run_token_from_other_run_denied_on_tools(internal_client, db_session):
    """工具执行要求 token 与 DB 双向一致；篡改 scope 的 token fail-closed。"""
    _, _, _, run = _seed(db_session, name="toolsc")
    lease_response = internal_client.post(
        "/internal/agent/jobs/lease",
        json={"kind": "assistant", "leased_by": "sc"},
        headers=_auth(issue_service_token()),
    )
    token = lease_response.json()["run_token"]
    internal_client.post(
        f"/internal/agent/runs/{run.id}/events/append",
        json={"events": [{"seq": 1, "type": "run.started", "public_payload": {}}]},
        headers=_auth(token),
    )

    # allowlist 篡改：手工签发一个声称拥有 steward_ping 的 token → 与 DB 不一致拒绝
    legit_claims = decode_run_token(token)
    forged = issue_run_token(
        run_id=run.id,
        job_id=run.job_id or 0,
        agent_kind="assistant",
        account_id=legit_claims["account_id"],
        space_id=legit_claims["space_id"],
        tool_allowlist=["familygraph.steward_ping"],
    )
    denied = internal_client.post(
        f"/internal/agent/runs/{run.id}/tools/familygraph.echo/execute",
        json={"version": 1, "input": {"text": "hi"}},
        headers=_auth(forged),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "AGENT_TOKEN_SCOPE_MISMATCH"

    ok = internal_client.post(
        f"/internal/agent/runs/{run.id}/tools/familygraph.echo/execute",
        json={"version": 1, "input": {"text": "hi"}},
        headers=_auth(token),
    )
    assert ok.status_code == 200
    assert ok.json()["output"] == {"text": "hi"}


def test_tool_execute_records_tool_call_id_in_audit(internal_client, db_session):
    """tool_call_id 透传进执行审计；未知额外字段（如 tool_version）仍被拒绝。"""
    from app.models.audit_log import AuditLog as AuditLogModel

    _, _, _, run = _seed(db_session, name="tcid")
    lease_response = internal_client.post(
        "/internal/agent/jobs/lease",
        json={"leased_by": "sc"},
        headers=_auth(issue_service_token()),
    )
    token = lease_response.json()["run_token"]
    started = internal_client.post(
        f"/internal/agent/runs/{run.id}/events/append",
        json={"events": [{"seq": 1, "type": "run.started", "public_payload": {}}]},
        headers=_auth(token),
    )
    assert started.status_code == 200

    ok = internal_client.post(
        f"/internal/agent/runs/{run.id}/tools/familygraph.echo/execute",
        json={"version": 1, "input": {"text": "hi"}, "tool_call_id": "tc_42"},
        headers=_auth(token),
    )
    assert ok.status_code == 200
    audit_row = db_session.scalar(
        select(AuditLogModel)
        .where(AuditLogModel.action == "agent_tool_executed")
        .order_by(AuditLogModel.id.desc())
    )
    assert audit_row is not None
    assert audit_row.detail["tool_call_id"] == "tc_42"

    # strict schema：已废弃的 tool_version 额外字段 fail-closed（422）
    extra = internal_client.post(
        f"/internal/agent/runs/{run.id}/tools/familygraph.echo/execute",
        json={"version": 1, "input": {"text": "hi"}, "tool_version": 1},
        headers=_auth(token),
    )
    assert extra.status_code == 422


def test_tool_call_dedup_and_actor_semantics(internal_client, db_session):
    """R4：同 (run_id, tool_call_id) 幂等重放；审计 actor 为 users.id 而非 accounts.id。"""
    from app.models.agent import AgentToolCall
    from app.models.audit_log import AuditLog as AuditLogModel

    user, space, agent_session, run = _seed(db_session, name="dedup")
    lease_response = internal_client.post(
        "/internal/agent/jobs/lease",
        json={"leased_by": "sc"},
        headers=_auth(issue_service_token()),
    )
    token = lease_response.json()["run_token"]
    started = internal_client.post(
        f"/internal/agent/runs/{run.id}/events/append",
        json={"events": [{"seq": 1, "type": "run.started", "public_payload": {}}]},
        headers=_auth(token),
    )
    assert started.status_code == 200

    body = {"version": 1, "input": {"text": "ping"}, "tool_call_id": "tc_dedup_1"}
    first = internal_client.post(
        f"/internal/agent/runs/{run.id}/tools/familygraph.echo/execute",
        json=body,
        headers=_auth(token),
    )
    assert first.status_code == 200
    assert first.json()["output"] == {"text": "ping"}

    # 重放：同一输出、不重复执行、不重复审计、不新增台账行
    replay = internal_client.post(
        f"/internal/agent/runs/{run.id}/tools/familygraph.echo/execute",
        json=body,
        headers=_auth(token),
    )
    assert replay.status_code == 200
    assert replay.json()["output"] == {"text": "ping"}
    calls = db_session.scalars(select(AgentToolCall).where(AgentToolCall.run_id == run.id)).all()
    assert len(calls) == 1
    executed_audits = db_session.scalars(
        select(AuditLogModel).where(
            AuditLogModel.action == "agent_tool_executed", AuditLogModel.target_id == run.id
        )
    ).all()
    assert len(executed_audits) == 1

    # 审计 actor 归属语义：actor_id == users.id；account/session 另存 detail
    assert executed_audits[0].actor_id == user.id
    assert executed_audits[0].detail["account_id"] == user.account.id
    assert executed_audits[0].detail["session_id"] == agent_session.id

    # 同 id 不同版本 → 409 AGENT_TOOL_CALL_CONFLICT（fail-closed；去重在版本解析前）
    conflict = internal_client.post(
        f"/internal/agent/runs/{run.id}/tools/familygraph.echo/execute",
        json={"version": 2, "input": {"text": "ping"}, "tool_call_id": "tc_dedup_1"},
        headers=_auth(token),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "AGENT_TOOL_CALL_CONFLICT"


def test_internal_routes_absent_from_public_listener(client, db_session):
    """P1 网络隔离回归：公开 app 不挂载 internal 协议，任意方法一律 404。"""
    _seed(db_session, name="pub")
    token = issue_service_token()
    headers = {"Authorization": f"Bearer {token}"}
    for method in ("get", "post", "put", "patch", "delete"):
        response = client.request(
            method,
            "/internal/agent/jobs/lease",
            json={"kind": "assistant", "leased_by": "sc"},
            headers=headers,
        )
        assert response.status_code == 404, (method, response.status_code)
        assert "error" in response.json()
