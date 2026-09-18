"""Agent Run 公开事件流测试：幂等追加、seq 连续性、未知类型拒绝、leased→running 提升。"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.agent import AgentJob, AgentMessage, AgentRun, AgentRunEvent
from app.services import agent_events, agent_queue
from app.services.agent_events import EventEntry
from app.services.agent_tokens import issue_service_token
from conftest import create_agent_fixture, create_agent_message, create_agent_session


def _enqueue(db, session):
    message = create_agent_message(db, session)
    return agent_queue.enqueue_run(
        db,
        agent_session=session,
        kind="assistant",
        policy_version="p",
        tool_allowlist=["familygraph.echo"],
        message=message,
    )


def _error(exc: Exception) -> dict:
    detail = getattr(exc, "detail", None)
    assert isinstance(detail, dict) and "__api_error__" in detail
    return detail["__api_error__"]  # type: ignore[no-any-return]


def test_append_assigns_ids_and_persists_in_order(db_session):
    user, space = create_agent_fixture(db_session, name="ev1")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    accepted, duplicates = agent_events.append_events(
        db_session,
        run,
        [
            EventEntry(seq=1, type="turn.started", public_payload={"turn": 1}),
            EventEntry(seq=2, type="turn.completed", public_payload={"turn": 1}),
        ],
    )
    db_session.commit()
    assert [row.seq for row in accepted] == [1, 2]
    assert duplicates == []
    rows = list(
        db_session.scalars(
            select(AgentRunEvent).where(AgentRunEvent.run_id == run.id).order_by(AgentRunEvent.seq)
        )
    )
    assert [r.seq for r in rows] == [0, 1, 2]  # seq0 为入队首个事件


def test_duplicate_retry_is_idempotent(db_session):
    user, space = create_agent_fixture(db_session, name="ev2")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    entry = EventEntry(seq=1, type="turn.started", public_payload={"turn": 1})
    first, _ = agent_events.append_events(db_session, run, [entry])
    accepted, duplicates = agent_events.append_events(db_session, run, [entry])
    assert accepted == [] and duplicates == [1]
    # 完全一致的重试不产生第二行
    rows = list(db_session.scalars(select(AgentRunEvent.id).where(AgentRunEvent.run_id == run.id)))
    assert len(rows) == 2


def test_same_seq_different_content_conflicts(db_session):
    user, space = create_agent_fixture(db_session, name="ev3")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    agent_events.append_events(
        db_session, run, [EventEntry(seq=1, type="turn.started", public_payload={"turn": 1})]
    )
    db_session.commit()
    with pytest.raises(HTTPException) as exc_info:
        agent_events.append_events(
            db_session,
            run,
            [EventEntry(seq=1, type="turn.started", public_payload={"turn": 2})],
        )
    err = _error(exc_info.value)
    assert err["code"] == "AGENT_EVENT_SEQ_CONFLICT"


def test_gap_or_regression_rejected(db_session):
    user, space = create_agent_fixture(db_session, name="ev4")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    with pytest.raises(HTTPException) as exc_info:
        agent_events.append_events(
            db_session, run, [EventEntry(seq=5, type="turn.started", public_payload={})]
        )
    assert _error(exc_info.value)["code"] == "AGENT_EVENT_SEQ_CONFLICT"
    # 回退同样拒绝
    agent_events.append_events(
        db_session, run, [EventEntry(seq=1, type="turn.started", public_payload={})]
    )
    with pytest.raises(HTTPException):
        agent_events.append_events(
            db_session, run, [EventEntry(seq=1, type="turn.completed", public_payload={})]
        )


def test_unknown_type_rejected_without_persist(db_session):
    user, space = create_agent_fixture(db_session, name="ev5")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    with pytest.raises(HTTPException) as exc_info:
        agent_events.append_events(
            db_session,
            run,
            [EventEntry(seq=1, type="card.render.v2", public_payload={})],
        )
    assert _error(exc_info.value)["code"] == "AGENT_EVENT_INVALID"
    max_seq = db_session.scalar(
        select(AgentRunEvent.seq)
        .where(AgentRunEvent.run_id == run.id)
        .order_by(AgentRunEvent.seq.desc())
        .limit(1)
    )
    assert max_seq == 0  # 只有入队 seq0，非法类型未落公开流


def test_run_started_promotes_leased_to_running(db_session):
    user, space = create_agent_fixture(db_session, name="ev6")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None and grant.run.status == "leased"
    agent_events.append_events(
        db_session, grant.run, [EventEntry(seq=1, type="run.started", public_payload={})]
    )
    assert grant.run.status == "running"
    job = db_session.get(AgentJob, grant.job.id)
    assert job is not None and job.status == "running"

    # queued 状态直接发 run.started 属协议违规（FSM fail-closed）
    other = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    queued_run = _enqueue(db_session, other)
    with pytest.raises(HTTPException) as exc_info:
        agent_events.append_events(
            db_session, queued_run, [EventEntry(seq=1, type="run.started", public_payload={})]
        )
    assert _error(exc_info.value)["code"] == "AGENT_RUN_NOT_RUNNING"


def test_cancel_requested_rejection_is_machine_readable(internal_client, db_session):
    """取消裁决必须带 detail.reason=cancel_requested（真实 internal 端点）。

    sidecar 靠这个结构化原因把「服务端已裁决取消」与普通 409 协议冲突分开；
    只看状态码或 message 文本会让在途 append 的 409 被当成冲突，进而自造
    failed(SIDECAR_ERROR) 覆盖取消终态（F 受控验收 A6-1）。
    """
    user, space = create_agent_fixture(db_session, name="ev-cancel")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    _enqueue(db_session, session)
    db_session.commit()

    lease = internal_client.post(
        "/internal/agent/jobs/lease",
        json={"kind": "assistant", "leased_by": "sc"},
        headers={"Authorization": f"Bearer {issue_service_token()}"},
    )
    assert lease.status_code == 200, lease.text
    run_id = lease.json()["run_id"]
    run_token = lease.json()["run_token"]
    db_session.expire_all()
    run = db_session.get(AgentRun, run_id)
    assert run is not None
    run.cancel_requested = True
    db_session.commit()

    response = internal_client.post(
        f"/internal/agent/runs/{run_id}/events/append",
        json={"events": [{"seq": 1, "type": "run.started", "public_payload": {}}]},
        headers={"Authorization": f"Bearer {run_token}"},
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "AGENT_RUN_NOT_RUNNING"
    assert error["detail"] == {"reason": "cancel_requested"}


# ---- 09-18 P0-2: 临时正文显示事件（assistant.text_delta / text_reset）----


def test_provisional_delta_is_persisted_but_never_materialises_a_message(db_session):
    """临时正文是显示投影：落事件但不产生 AgentMessage（否则会进历史/RAG）。"""
    user, space = create_agent_fixture(db_session, name="pv1")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    accepted, duplicates = agent_events.append_events(
        db_session,
        run,
        [
            EventEntry(seq=1, type="turn.started", public_payload={}),
            EventEntry(
                seq=2,
                type="assistant.text_delta",
                public_payload={"role": "assistant", "delta": "half an answer"},
            ),
            EventEntry(
                seq=3,
                type="assistant.text_reset",
                public_payload={"role": "assistant"},
            ),
        ],
    )
    db_session.commit()
    assert [row.type for row in accepted] == [
        "turn.started",
        "assistant.text_delta",
        "assistant.text_reset",
    ]
    assert duplicates == []
    # 关键不变量：临时事件不物化历史行（会话里只有 fixture 的用户消息）。
    assert (
        db_session.scalars(
            select(AgentMessage).where(
                AgentMessage.session_id == session.id, AgentMessage.role == "assistant"
            )
        ).all()
        == []
    )
    # 公开载荷就是白名单投影本身，未混入服务端字段。
    delta_row = db_session.scalar(
        select(AgentRunEvent).where(
            AgentRunEvent.run_id == run.id, AgentRunEvent.type == "assistant.text_delta"
        )
    )
    assert delta_row is not None
    assert delta_row.public_payload == {"role": "assistant", "delta": "half an answer"}


def test_provisional_delta_rejects_extra_fields_and_oversize(db_session):
    """形状 fail-closed：额外字段、空 delta、超长分片都在落库前拒绝。"""
    user, space = create_agent_fixture(db_session, name="pv2")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    agent_events.append_events(
        db_session, run, [EventEntry(seq=1, type="turn.started", public_payload={})]
    )

    with pytest.raises(HTTPException) as extra:
        agent_events.append_events(
            db_session,
            run,
            [
                EventEntry(
                    seq=2,
                    type="assistant.text_delta",
                    public_payload={"role": "assistant", "delta": "x", "message_id": 7},
                )
            ],
        )
    assert _error(extra.value)["code"] == "AGENT_EVENT_INVALID"

    with pytest.raises(HTTPException) as empty:
        agent_events.append_events(
            db_session,
            run,
            [
                EventEntry(
                    seq=2,
                    type="assistant.text_delta",
                    public_payload={"role": "assistant", "delta": ""},
                )
            ],
        )
    assert _error(empty.value)["code"] == "AGENT_EVENT_INVALID"

    with pytest.raises(HTTPException) as oversize:
        agent_events.append_events(
            db_session,
            run,
            [
                EventEntry(
                    seq=2,
                    type="assistant.text_delta",
                    public_payload={
                        "role": "assistant",
                        "delta": "x" * (agent_events.MAX_PROVISIONAL_DELTA_CHARS + 1),
                    },
                )
            ],
        )
    assert _error(oversize.value)["code"] == "AGENT_EVENT_INVALID"

    with pytest.raises(HTTPException) as reset_delta:
        agent_events.append_events(
            db_session,
            run,
            [
                EventEntry(
                    seq=2,
                    type="assistant.text_reset",
                    public_payload={"role": "assistant", "delta": "x"},
                )
            ],
        )
    assert _error(reset_delta.value)["code"] == "AGENT_EVENT_INVALID"

    # 全部拒绝：除已提交的 turn.started 外未落任何事件（seq 2 仍空闲）。
    assert (
        db_session.scalars(
            select(AgentRunEvent).where(AgentRunEvent.run_id == run.id, AgentRunEvent.seq > 1)
        ).all()
        == []
    )


def test_authoritative_message_supersedes_provisional_text(db_session):
    """终态权威消息仍按原合同物化历史行，与临时分片并存但不合并。"""
    user, space = create_agent_fixture(db_session, name="pv3")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    agent_events.append_events(
        db_session,
        run,
        [
            EventEntry(seq=1, type="turn.started", public_payload={}),
            EventEntry(
                seq=2,
                type="assistant.text_delta",
                public_payload={"role": "assistant", "delta": "half"},
            ),
            EventEntry(
                seq=3,
                type="message.assistant_added",
                public_payload={"role": "assistant", "text": "the full answer"},
            ),
        ],
    )
    db_session.commit()
    messages = db_session.scalars(
        select(AgentMessage).where(
            AgentMessage.session_id == session.id, AgentMessage.role == "assistant"
        )
    ).all()
    assert len(messages) == 1
    assert messages[0].content_json["text"] == "the full answer"
