"""Agent Run SSE 流测试：回放、Last-Event-ID 断点续传、挂起至事件、终态关闭、鉴权。"""

import threading
import time

from conftest import (
    auth_header,
    create_agent_fixture,
    create_agent_message,
    create_agent_session,
    create_space_member,
    login,
)
from sqlalchemy.orm import Session

from app import config
from app.models.agent import AgentRun, AgentSession
from app.services import agent_events, agent_queue
from app.services.agent_events import EventEntry


def _seed_run(db: Session, name: str) -> tuple[object, object, AgentSession, AgentRun]:
    user, space = create_agent_fixture(db, name=name)
    create_space_member(db, space.id, user.id)
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


def _append(db: Session, run: AgentRun, seq: int, event_type: str, payload=None) -> None:
    accepted, duplicates = agent_events.append_events(
        db, run, [EventEntry(seq=seq, type=event_type, public_payload=payload or {})]
    )
    assert len(accepted) == 1 and not duplicates
    db.commit()
    agent_events.notifier.publish(run.id)


def _parse_sse(lines: list[str]) -> list[dict]:
    """把 SSE 原始行解析为事件对象（忽略 keepalive 注释与空行）。"""
    events: list[dict] = []
    current: dict = {}
    for line in lines:
        if line.startswith(":"):
            continue
        if not line:
            if current:
                events.append(current)
                current = {}
            continue
        field, _, value = line.partition(": ")
        if field == "id":
            current["id"] = int(value)
        elif field == "event":
            current["event"] = value
        elif field == "data":
            current["data"] = value
    if current:
        events.append(current)
    return events


def _drain_stream(client, url: str, headers: dict[str, str], sink: list):
    """后台线程消费 SSE 流直至服务端关闭；行/状态/错误全部收集进 sink。"""

    def _run() -> None:
        try:
            with client.stream("GET", url, headers=headers) as response:
                sink.append(("__status__", response.status_code))
                for line in response.iter_lines():
                    sink.append(line)
        except Exception as exc:  # noqa: BLE001 - 测试线程收集任意传输错误
            sink.append(("__error__", repr(exc)))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def test_replay_from_last_event_id_no_gap(client, db_session):
    _user, _space, _agent_session, run = _seed_run(db_session, "ssereplay")
    _append(db_session, run, 1, "turn.started")
    _append(db_session, run, 2, "message.assistant_added", {"text": "回答"})
    _append(db_session, run, 3, "run.failed", {"error_code": "X"})

    headers = auth_header(login(client, "ssereplay", "123456").json())
    response = client.get(
        f"/api/agent/runs/{run.id}/events",
        headers={**headers, "Last-Event-ID": "1"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text.splitlines())
    # 只补发 seq>1，无漏序乱序；终态后服务端关闭（流自然结束）
    assert [e["id"] for e in events] == [2, 3]
    assert [e["event"] for e in events] == ["message.assistant_added", "run.failed"]
    payload_first = events[0]["data"]
    assert '"type": "message.assistant_added"' in payload_first
    assert '"seq": 2' in payload_first


def test_full_replay_and_terminal_close_without_cursor(client, db_session):
    _user, _space, _agent_session, run = _seed_run(db_session, "ssefull")
    _append(db_session, run, 1, "turn.started")

    # 未终态前：取消写入终态事件后，流应完整回放并关闭
    settled = agent_queue.request_cancel(db_session, run)
    db_session.expire(settled)
    assert settled.status == "cancelled"

    headers = auth_header(login(client, "ssefull", "123456").json())
    response = client.get(f"/api/agent/runs/{run.id}/events", headers=headers)
    events = _parse_sse(response.text.splitlines())
    assert [e["id"] for e in events] == [0, 1, 2]
    assert [e["event"] for e in events] == [
        "message.user_added",
        "turn.started",
        "run.cancelled",
    ]


def test_after_event_id_and_header_take_larger(client, db_session):
    _user, _space, _agent_session, run = _seed_run(db_session, "ssecursor")
    for seq in (1, 2, 3):
        _append(db_session, run, seq, "turn.started" if seq == 1 else "turn.completed")
    _append(db_session, run, 4, "run.cancelled")

    headers = auth_header(login(client, "ssecursor", "123456").json())
    both = client.get(
        f"/api/agent/runs/{run.id}/events",
        headers={**headers, "Last-Event-ID": "1"},
        params={"after_event_id": 3},
    )
    events = _parse_sse(both.text.splitlines())
    assert [e["id"] for e in events] == [4]


def test_stream_hangs_until_new_events_then_closes_on_terminal(client, db_session, monkeypatch):
    monkeypatch.setattr(config, "AGENT_SSE_POLL_SECONDS", 0.05)
    _user, _space, _agent_session, run = _seed_run(db_session, "ssehang")
    headers = auth_header(login(client, "ssehang", "123456").json())

    sink: list = []
    thread = _drain_stream(
        client, f"/api/agent/runs/{run.id}/events?after_event_id=0", headers, sink
    )
    try:
        # 连接建立但无新事件：短时间内不应收到任何数据行
        time.sleep(0.4)
        assert not any(isinstance(item, str) and item.startswith("id:") for item in sink)

        _append(db_session, run, 1, "turn.started")  # 轮询兜底应在此后一个 poll 周期送达
        _append(db_session, run, 2, "run.failed", {"error_code": "DONE"})
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        if thread.is_alive():
            pass  # daemon 线程随测试进程退出，不阻塞套件

    statuses = [item for item in sink if isinstance(item, tuple)]
    assert statuses and statuses[0] == ("__status__", 200)
    lines = [item for item in sink if isinstance(item, str)]
    events = _parse_sse(lines)
    assert [e["id"] for e in events] == [1, 2]
    assert events[-1]["event"] == "run.failed"


def test_stream_authz_other_user_404_and_unauthenticated_401(client, db_session):
    _user, _space, _agent_session, run = _seed_run(db_session, "sseauth")
    create_agent_fixture(db_session, name="ssestranger")  # 陌生账号（非本人）
    stranger = auth_header(login(client, "ssestranger", "123456").json())
    forbidden = client.get(f"/api/agent/runs/{run.id}/events", headers=stranger)
    assert forbidden.status_code == 404
    unauth = client.get(f"/api/agent/runs/{run.id}/events")
    assert unauth.status_code == 401


def test_invalid_last_event_id_is_ignored(client, db_session):
    _user, _space, _agent_session, run = _seed_run(db_session, "ssebadcursor")
    _append(db_session, run, 1, "turn.started")
    _append(db_session, run, 2, "run.settled")
    headers = auth_header(login(client, "ssebadcursor", "123456").json())
    response = client.get(
        f"/api/agent/runs/{run.id}/events",
        headers={**headers, "Last-Event-ID": "not-a-number"},
    )
    events = _parse_sse(response.text.splitlines())
    assert [e["id"] for e in events] == [0, 1, 2]
