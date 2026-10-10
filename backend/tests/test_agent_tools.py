"""Agent 工具协议测试：四类拒绝码 + 合法执行 + running 态门禁（RT-3）。"""

import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.services import agent_queue, agent_tools
from conftest import create_agent_fixture, create_agent_session


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


def test_removed_steward_tool_is_unknown(db_session):
    """Steward 不再伪装成通用 runtime tool；旧名称必须按未知工具拒绝。"""
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
    assert exc_info.value.detail["__api_error__"]["code"] == "AGENT_TOOL_UNKNOWN"


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


def test_unexpected_dispatch_failure_is_audited_and_typed(db_session, monkeypatch):
    """非协议异常必须留下 `agent_tool_failed` 审计，并返回专属错误码。

    这条测试防的是一次真实事故：`cancel_requested = 0` 在 PostgreSQL 上是类型错误
    （boolean = integer），抛出的是 `ProgrammingError` 而非 `ToolProtocolError`，
    于是它**绕过全部审计与记录**直接变成通用 500。后果是「所有工具调用恒失败」
    静默潜伏数天（生产 2324 次/72h），而模型只看到 `INTERNAL_ERROR`。

    这里用 monkeypatch 在分派里注入一个非协议异常，断言三件事：
    错误码可辨认、审计留痕、detail 不含异常 message（可能含数据）。
    """
    user, space = create_agent_fixture(db_session, name="t8")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    grant = _lease_and_start(db_session, run)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("sensitive detail that must not leak")

    monkeypatch.setattr(agent_tools, "_dispatch", _boom)

    with pytest.raises(HTTPException) as excinfo:
        agent_tools.execute(
            db_session,
            grant.run,
            session,
            {"agent_kind": "assistant"},
            name="familygraph.echo",
            version=1,
            input_payload={"text": "hi"},
        )

    assert excinfo.value.status_code == 500
    payload = excinfo.value.detail["__api_error__"]
    assert payload["code"] == "AGENT_TOOL_EXECUTION_FAILED"
    assert payload["detail"]["error_class"] == "RuntimeError"
    # 不泄露：message / SQL / 参数都不得出现在错误体里。
    assert "sensitive detail" not in str(payload)

    row = db_session.scalars(select(AuditLog).where(AuditLog.action == "agent_tool_failed")).one()
    # detail_json 是 Text 列（存 JSON 字符串），与 test_bindings 同口径解析。
    detail = json.loads(row.detail_json)
    assert detail["tool"] == "familygraph.echo"
    assert detail["error_class"] == "RuntimeError"
    assert detail["agent_kind"] == "assistant"
    # detail 只放异常类型名，不放 message。
    assert "sensitive detail" not in row.detail_json
