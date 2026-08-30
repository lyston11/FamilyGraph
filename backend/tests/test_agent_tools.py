"""Agent 工具协议测试：四类拒绝码 + 合法执行 + running 态门禁（RT-3）。"""

import pytest
from conftest import create_agent_fixture, create_agent_session
from fastapi import HTTPException
from sqlalchemy import select

from app.models.agent import AgentJob
from app.models.audit_log import AuditLog
from app.services import agent_queue, agent_tools


def _enqueue(db, session, *, allowlist=None):
    return agent_queue.enqueue_run(
        db,
        agent_session=session,
        kind="assistant",
        policy_version="p",
        tool_allowlist=allowlist or ["familygraph.echo"],
    )


def _lease_and_start(db, run):
    grant = agent_queue.lease_next(db, kind="assistant", leased_by="sc")
    assert grant is not None
    agent_events_start(db, grant.run)
    return grant


def agent_events_start(db, run):
    from app.services import agent_events as events_service

    seq = events_service.next_seq(db, run.id)
    events_service.append_events(
        db, run, [events_service.EventEntry(seq=seq, type="run.started", public_payload={})]
    )


def test_unknown_tool_denied_with_audit(db_session):
    user, space = create_agent_fixture(db_session, name="t1")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    grant = _lease_and_start(db_session, run)
    with pytest.raises(HTTPException) as exc_info:
        agent_tools.execute(
            db_session,
            grant.run,
            session,
            {"agent_kind": "assistant"},
            name="familygraph.nuclear_launch",
            version=1,
            input_payload={},
        )
    detail = exc_info.value.detail["__api_error__"]
    assert detail["code"] == "AGENT_TOOL_UNKNOWN"
    audit_row = db_session.scalar(select(AuditLog).where(AuditLog.action == "agent_tool_denied"))
    assert audit_row is not None


def test_wrong_version_denied(db_session):
    user, space = create_agent_fixture(db_session, name="t2")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    grant = _lease_and_start(db_session, run)
    with pytest.raises(HTTPException) as exc_info:
        agent_tools.execute(
            db_session,
            grant.run,
            session,
            {"agent_kind": "assistant"},
            name="familygraph.echo",
            version=99,
            input_payload={"text": "hi"},
        )
    assert exc_info.value.detail["__api_error__"]["code"] == "AGENT_TOOL_VERSION_UNSUPPORTED"


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"text": "hi", "extra": True}, "额外字段"),
        ({}, "缺必填"),
        ({"text": 123}, "类型错误"),
        ({"text": "x" * 1001}, "超长"),
    ],
)
def test_schema_invalid_denied(db_session, payload, reason):
    user, space = create_agent_fixture(db_session, name=f"t3{reason}")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    grant = _lease_and_start(db_session, run)
    with pytest.raises(HTTPException) as exc_info:
        agent_tools.execute(
            db_session,
            grant.run,
            session,
            {"agent_kind": "assistant"},
            name="familygraph.echo",
            version=1,
            input_payload=payload,
        )
    assert exc_info.value.detail["__api_error__"]["code"] == "AGENT_TOOL_SCHEMA_INVALID"


def test_allowlist_scope_denied(db_session):
    """token allowlist 未包含的工具拒绝执行。"""
    user, space = create_agent_fixture(db_session, name="t4")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session, allowlist=["familygraph.echo"])
    grant = _lease_and_start(db_session, run)
    with pytest.raises(HTTPException) as exc_info:
        agent_tools.execute(
            db_session,
            grant.run,
            session,
            {"agent_kind": "assistant"},
            name="familygraph.probe_scope",
            version=1,
            input_payload={},
        )
    assert exc_info.value.detail["__api_error__"]["code"] == "AGENT_TOOL_SCOPE_DENIED"


def test_min_kind_scope_denied_for_assistant(db_session):
    """min_kind=steward 的工具对 assistant Run 拒绝。"""
    user, space = create_agent_fixture(db_session, name="t5")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session, allowlist=["familygraph.steward_ping"])
    grant = _lease_and_start(db_session, run)
    with pytest.raises(HTTPException) as exc_info:
        agent_tools.execute(
            db_session,
            grant.run,
            session,
            {"agent_kind": "assistant"},
            name="familygraph.steward_ping",
            version=1,
            input_payload={},
        )
    assert exc_info.value.detail["__api_error__"]["code"] == "AGENT_TOOL_SCOPE_DENIED"


def test_tool_requires_running_state(db_session):
    """leased 未开始时工具不可执行（409 fail-closed）。"""
    user, space = create_agent_fixture(db_session, name="t6")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None
    with pytest.raises(HTTPException) as exc_info:
        agent_tools.execute(
            db_session,
            grant.run,
            session,
            {"agent_kind": "assistant"},
            name="familygraph.echo",
            version=1,
            input_payload={"text": "hi"},
        )
    assert exc_info.value.detail["__api_error__"]["code"] == "AGENT_RUN_NOT_RUNNING"


def test_tool_rejected_after_server_cancellation(db_session):
    """cancel_requested is a server gate even before the sidecar heartbeat arrives."""
    user, space = create_agent_fixture(db_session, name="t-cancel-gate")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    grant = _lease_and_start(db_session, run)
    db_session.commit()
    run.cancel_requested = True
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        agent_tools.execute(
            db_session,
            grant.run,
            session,
            {"agent_kind": "assistant"},
            name="familygraph.echo",
            version=1,
            input_payload={"text": "must not execute"},
        )
    detail = exc_info.value.detail["__api_error__"]
    assert detail["code"] == "AGENT_RUN_NOT_RUNNING"
    assert detail["detail"]["reason"] == "cancel_requested"


def test_echo_and_probe_scope_success(db_session):
    """合法调用成功：echo 回显；probe_scope 返回 scope 摘要证明授权链路。"""
    user, space = create_agent_fixture(db_session, name="t7")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session, allowlist=["familygraph.echo", "familygraph.probe_scope"])
    grant = _lease_and_start(db_session, run)

    out = agent_tools.execute(
        db_session,
        grant.run,
        session,
        {"agent_kind": "assistant"},
        name="familygraph.echo",
        version=1,
        input_payload={"text": "你好"},
    )
    assert out == {"text": "你好"}

    probe = agent_tools.execute(
        db_session,
        grant.run,
        session,
        {"agent_kind": "assistant"},
        name="familygraph.probe_scope",
        version=1,
        input_payload={},
    )
    assert probe["run_id"] == grant.run.id
    assert probe["account_id"] == user.account.id
    assert probe["space_id"] == space.id
    assert probe["agent_kind"] == "assistant"
    assert probe["policy_version"] == "p"
    db_session.commit()  # 服务层不提交成功审计，API 层负责；此处等价提交后验证
    executed = db_session.scalars(select(AuditLog).where(AuditLog.action == "agent_tool_executed"))
    assert len(list(executed)) >= 2


def test_steward_ping_allows_steward_kind(db_session):
    user, space = create_agent_fixture(db_session, name="t8")
    steward_session = create_agent_session(
        db_session, account_id=user.account.id, space_id=space.id, kind="steward"
    )
    agent_queue.enqueue_run(
        db_session,
        agent_session=steward_session,
        kind="steward",
        policy_version="p",
        tool_allowlist=["familygraph.steward_ping"],
    )
    grant = agent_queue.lease_next(db_session, kind="steward", leased_by="sc")
    assert grant is not None
    agent_events_start(db_session, grant.run)
    job = db_session.get(AgentJob, grant.job.id)
    assert job is not None and job.kind == "steward"
    out = agent_tools.execute(
        db_session,
        grant.run,
        steward_session,
        {"agent_kind": "steward"},
        name="familygraph.steward_ping",
        version=1,
        input_payload={},
    )
    assert out == {"ok": True, "space_id": space.id}
