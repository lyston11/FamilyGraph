"""浏览器 Agent API 测试：会话 scope、Idempotency、并发限额、cancel、feature flag。"""

import pytest
from conftest import (
    auth_header,
    create_agent_fixture,
    create_space_member,
    login,
)
from sqlalchemy import func, select

from app import config
from app.models.agent import AgentRun, AgentRunEvent, AgentSession
from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
from app.models.audit_log import AuditLog
from app.models.space import FamilySpace
from app.services import agent_queue, agent_tokens
from app.utils import timeutil


def _allow_provider(db, space_id: int) -> None:
    """直建可用云 Provider 与空间选择（绕过管理 API；治理端点另测）。"""
    provider = db.scalar(select(AgentProvider).where(AgentProvider.name == "test-allow"))
    if provider is None:
        provider = AgentProvider(
            name="test-allow",
            kind="openai_compatible",
            base_url="https://api.example.com/v1",
            allowed_models_json=["model-x"],
            enabled=True,
            created_at=timeutil.utcnow(),
            updated_at=timeutil.utcnow(),
        )
        db.add(provider)
        db.flush()
    db.add(
        AgentSpaceProviderSetting(
            space_id=space_id,
            provider_id=provider.id,
            model="model-x",
            cloud_allowed=True,
            local_required=False,
            enabled=True,
        )
    )
    db.commit()


def _member_session(client, db, name: str):
    """造 user+space+membership+可用 Provider 并经 API 创建会话。"""
    user, space = create_agent_fixture(db, name=name)
    create_space_member(db, space.id, user.id)
    _allow_provider(db, space.id)
    token_pair = login(client, name, "123456").json()
    headers = auth_header(token_pair)
    response = client.post("/api/agent/sessions", json={"space_id": space.id}, headers=headers)
    assert response.status_code == 201
    return user, space, headers, response.json()


def _post_message(client, headers, session_id: int, content: str, key: str):
    return client.post(
        f"/api/agent/sessions/{session_id}/messages",
        json={"content": content},
        headers={**headers, "Idempotency-Key": key},
    )


# ---- 会话 ----


def test_create_session_requires_active_membership(client, db_session):
    _user, space = create_agent_fixture(db_session, name="owner1")
    outsider, _outsider_space = create_agent_fixture(db_session, name="outsider")
    headers = auth_header(login(client, "outsider", "123456").json())
    response = client.post("/api/agent/sessions", json={"space_id": space.id}, headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SPACE_FORBIDDEN_ACTOR"

    missing = client.post("/api/agent/sessions", json={"space_id": 99999}, headers=headers)
    assert missing.status_code == 404


def test_session_kind_fixed_assistant_and_no_update_endpoint(client, db_session):
    _user, _space, headers, created = _member_session(client, db_session, "fixedkind")
    assert created["agent_kind"] == "assistant"
    # steward 会话不接受浏览器创建；scope 更新类端点不存在（404/405）
    for method in ("put", "patch"):
        response = getattr(client, method)(
            f"/api/agent/sessions/{created['id']}", json={"space_id": 2}, headers=headers
        )
        assert response.status_code in (404, 405)


def test_session_scope_immutable_at_db_level(db_session):
    user, space = create_agent_fixture(db_session, name="immutable")
    row = AgentSession(
        account_id=user.account.id,
        space_id=space.id,
        agent_kind="assistant",
        created_at=user.created_at,
    )
    db_session.add(row)
    db_session.commit()
    with pytest.raises(Exception, match="scope is immutable"):
        row.space_id = space.id + 1
        db_session.commit()


def test_list_sessions_filter_by_space(client, db_session):
    user, space, headers, first = _member_session(client, db_session, "listfilter")
    other_space = FamilySpace(
        name="second-space", kind="household", owner_id=user.id, created_at=user.created_at
    )
    db_session.add(other_space)
    db_session.commit()
    create_space_member(db_session, other_space.id, user.id)
    second = client.post("/api/agent/sessions", json={"space_id": other_space.id}, headers=headers)
    assert second.status_code == 201

    all_sessions = client.get("/api/agent/sessions", headers=headers)
    assert {s["id"] for s in all_sessions.json()} == {first["id"], second.json()["id"]}
    filtered = client.get(f"/api/agent/sessions?space_id={other_space.id}", headers=headers)
    assert [s["id"] for s in filtered.json()] == [second.json()["id"]]


# ---- 消息与幂等 ----


def test_message_create_enqueues_run_with_event(client, db_session):
    _user, _space, headers, session_row = _member_session(client, db_session, "msgcreate")
    response = _post_message(client, headers, session_row["id"], "你好", "key-1")
    assert response.status_code == 200
    body = response.json()
    assert body["replayed"] is False
    assert body["message"]["role"] == "user"
    assert body["message"]["content_json"] == {"text": "你好"}
    run_ref = body["run"]
    assert run_ref is not None and run_ref["status"] == "queued"
    event_seq0 = db_session.scalar(select(AgentRunEvent).where(AgentRunEvent.seq == 0))
    assert event_seq0 is not None and event_seq0.type == "message.user_added"


def test_history_projection_excludes_internal_fields(client, db_session):
    _user, _space, headers, session_row = _member_session(client, db_session, "history")
    _post_message(client, headers, session_row["id"], "第一条", "key-h")
    listing = client.get(f"/api/agent/sessions/{session_row['id']}/messages", headers=headers)
    assert listing.status_code == 200
    messages = listing.json()
    assert len(messages) == 1
    assert "idempotency_key" not in messages[0]
    assert messages[0]["content_json"] == {"text": "第一条"}


def test_idempotency_same_payload_replays_original_run(client, db_session):
    _user, _space, headers, session_row = _member_session(client, db_session, "idem")
    first = _post_message(client, headers, session_row["id"], "同内容", "key-same")
    assert first.status_code == 200
    second = _post_message(client, headers, session_row["id"], "同内容", "key-same")
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert second.json()["message"]["id"] == first.json()["message"]["id"]
    assert second.json()["run"]["id"] == first.json()["run"]["id"]
    runs = db_session.scalar(select(func.count(AgentRun.id)))
    assert runs == 1


def test_idempotency_different_payload_conflict(client, db_session):
    _user, _space, headers, session_row = _member_session(client, db_session, "idemconf")
    first = _post_message(client, headers, session_row["id"], "内容A", "key-c")
    assert first.status_code == 200
    conflict = _post_message(client, headers, session_row["id"], "内容B", "key-c")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"


def test_idempotency_key_required_and_length_capped(client, db_session):
    _user, _space, headers, session_row = _member_session(client, db_session, "keyreq")
    missing = client.post(
        f"/api/agent/sessions/{session_row['id']}/messages", json={"content": "x"}, headers=headers
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    too_long = _post_message(client, headers, session_row["id"], "x", "k" * 121)
    assert too_long.status_code == 400


def test_content_validation_nonempty_and_length_cap(client, db_session):
    _user, _space, headers, session_row = _member_session(client, db_session, "contentval")
    empty = _post_message(client, headers, session_row["id"], "", "key-e")
    assert empty.status_code == 422
    too_long = _post_message(
        client, headers, session_row["id"], "字" * (config.AGENT_MESSAGE_MAX_LENGTH + 1), "key-t"
    )
    assert too_long.status_code == 422


def test_concurrency_session_busy_maps_to_run_limit(client, db_session):
    _user, _space, headers, session_row = _member_session(client, db_session, "sbusy")
    first = _post_message(client, headers, session_row["id"], "第一条", "key-1")
    assert first.status_code == 200
    second = _post_message(client, headers, session_row["id"], "第二条", "key-2")
    assert second.status_code == 409
    body = second.json()
    assert body["error"]["code"] == "AGENT_RUN_LIMIT"
    assert body["error"]["detail"]["reason"] == "AGENT_RUN_SESSION_BUSY"


def test_account_assistant_run_limit_across_sessions(client, db_session):
    user, space, headers, first_session = _member_session(client, db_session, "acctlim")

    # 先占满第一个会话的 active run（账户额度 = AGENT_ACCOUNT_ASSISTANT_RUN_LIMIT）
    seeded = _post_message(client, headers, first_session["id"], "第零条", "key-0")
    assert seeded.status_code == 200

    def _extra_session(name: str) -> int:
        extra = FamilySpace(
            name=name, kind="household", owner_id=user.id, created_at=user.created_at
        )
        db_session.add(extra)
        db_session.commit()
        create_space_member(db_session, extra.id, user.id)
        _allow_provider(db_session, extra.id)
        created = client.post("/api/agent/sessions", json={"space_id": extra.id}, headers=headers)
        assert created.status_code == 201
        return created.json()["id"]

    second_session = _extra_session("acctlim-space-2")
    third_session = _extra_session("acctlim-space-3")
    ok = _post_message(client, headers, second_session, "第二条", "key-2")
    assert ok.status_code == 200
    blocked = _post_message(client, headers, third_session, "第三条", "key-3")
    assert blocked.status_code == 409
    body = blocked.json()
    assert body["error"]["code"] == "AGENT_RUN_LIMIT"
    assert body["error"]["detail"]["reason"] == "AGENT_RUN_ACCOUNT_LIMIT"


# ---- Run 可见性与 cancel ----


def test_get_run_visibility_other_user_404(client, db_session):
    _user, _space, headers, session_row = _member_session(client, db_session, "runvis")
    create_agent_fixture(db_session, name="runner-other")  # 陌生账号（非本人）
    created = _post_message(client, headers, session_row["id"], "看看", "key-v")
    run_id = created.json()["run"]["id"]
    other_headers = auth_header(login(client, "runner-other", "123456").json())
    response = client.get(f"/api/agent/runs/{run_id}", headers=other_headers)
    assert response.status_code == 404
    mine = client.get(f"/api/agent/runs/{run_id}", headers=headers)
    assert mine.status_code == 200
    assert mine.json()["status"] == "queued"


def test_cancel_queued_immediate_writes_terminal_event_and_audit(client, db_session):
    _user, _space, headers, session_row = _member_session(client, db_session, "cancelq")
    created = _post_message(client, headers, session_row["id"], "取消我", "key-q")
    run_id = created.json()["run"]["id"]
    response = client.post(f"/api/agent/runs/{run_id}/cancel", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    terminal = db_session.scalar(
        select(AgentRunEvent).where(
            AgentRunEvent.run_id == run_id, AgentRunEvent.type == "run.cancelled"
        )
    )
    assert terminal is not None
    audit_row = db_session.scalar(select(AuditLog).where(AuditLog.action == "agent_run_cancelled"))
    assert audit_row is not None and audit_row.actor_id is not None


def test_cancel_running_then_settle_overrides_to_cancelled(client, db_session):
    _user, _space, headers, session_row = _member_session(client, db_session, "cancelrun")
    created = _post_message(client, headers, session_row["id"], "跑到一半", "key-r")
    run_id = created.json()["run"]["id"]

    lease = client.post(
        "/internal/agent/jobs/lease",
        json={"kind": "assistant", "leased_by": "sidecar-x"},
        headers={"Authorization": f"Bearer {agent_tokens.issue_service_token()}"},
    )
    assert lease.status_code == 200

    cancelled = client.post(f"/api/agent/runs/{run_id}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "leased"
    assert cancelled.json()["cancel_requested"] is True

    run = db_session.get(AgentRun, run_id)
    settled = agent_queue.settle_run(db_session, run, status="succeeded")
    db_session.expire(settled)
    assert settled.status == "cancelled"
    override_audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "agent_run_settle_overridden")
    )
    assert override_audit is not None
    assert override_audit.detail_json is not None

    again = client.post(f"/api/agent/runs/{run_id}/cancel", headers=headers)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "AGENT_RUN_TERMINAL"


# ---- feature flag ----


def test_feature_flag_disabled_returns_503(client, db_session, monkeypatch):
    _user, space, headers, session_row = _member_session(client, db_session, "flagoff503")
    created = _post_message(client, headers, session_row["id"], "x", "key-f")
    run_id = created.json()["run"]["id"]
    monkeypatch.setattr(config, "AGENT_RUNTIME_ENABLED", False)
    cases = [
        lambda: client.post("/api/agent/sessions", json={"space_id": space.id}, headers=headers),
        lambda: client.get("/api/agent/sessions", headers=headers),
        lambda: _post_message(client, headers, session_row["id"], "x2", "key-f2"),
        lambda: client.get(f"/api/agent/sessions/{session_row['id']}/messages", headers=headers),
        lambda: client.get(f"/api/agent/runs/{run_id}", headers=headers),
        lambda: client.post(f"/api/agent/runs/{run_id}/cancel", headers=headers),
        lambda: client.get(f"/api/agent/runs/{run_id}/events", headers=headers),
    ]
    for case in cases:
        response = case()
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "AGENT_RUNTIME_DISABLED"
