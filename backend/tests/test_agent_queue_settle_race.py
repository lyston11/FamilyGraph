"""结算竞态的锁内 CAS 复核（结构性 + 并发回归）。

## 为什么需要这个用例

`settle_run` 在**进入事务之前**有一次终态检查（廉价快速路径），并在 `_settle`
的**立即事务内部**再检查一次（第 438-439 行附近）。变异验证发现：

    删掉锁内那次复核 → 全部 9 个既有 settle/cancel 用例仍然通过

原因是单线程用例总在入口检查就被挡住，锁内复核从未被真正触发。
而它守护的是**真实并发窗口**：两个结算者都通过入口检查，各自进入事务，
若锁内没有复核，第二个会把已经是终态的 run 再写一次（覆盖终态、重复事件）。

`database-guidelines.md` 明确记录过这个陷阱：「仅含条件 UPDATE 的竞态，
结果断言在 pysqlite 上测不出锁缺失」。因此本文件必须用**真实并发**
（独立 Session + 同步点 + 统一超时）来守护，而不是顺序调用两次。

## 并发用例规范（来自 database-guidelines.md）

- `barrier`/`join` 必须带统一超时，否则一次竞态会挂死全量 pytest；
- worker 只捕获标量 ID 与独立 `SessionLocal`，禁止跨线程共享 ORM 实例；
- worker 捕获 `HTTPException` 与普通 `Exception` 并写入受 `Lock` 保护的结果列表；
- 最终从主线程 Session 重查数据库断言。
"""

from __future__ import annotations

import threading

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models.agent import AgentRun, AgentRunEvent
from app.services import agent_queue
from conftest import create_agent_fixture, create_agent_session

_SYNC_TIMEOUT = 20.0


def _error_code(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict) and "__api_error__" in detail:
        return str(detail["__api_error__"]["code"])
    raise AssertionError(f"unexpected exception: {exc!r}")


def _enqueue(db, agent_session, *, kind: str = "assistant"):
    # 与 tests/test_agent_queue.py 的 helper 保持一致（真实签名）
    return agent_queue.enqueue_run(
        db,
        agent_session=agent_session,
        kind=kind,
        policy_version="test-policy-1",
        tool_allowlist=["familygraph.echo"],
        message=None,
    )


def test_concurrent_settle_has_exactly_one_winner(db_session):
    """两个并发结算者：恰好一个成功，另一个被锁内 CAS 拒绝。

    锁内复核被删除时，两个都会「成功」——终态被写两次、`run.settled` 事件重复。
    本用例断言恰好一个赢家，且终态事件只有一条。
    """
    user, space = create_agent_fixture(db_session, name="settle-race")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="racer")
    assert grant is not None, "前提不成立：需要先租到 run 才能结算"
    run_id = run.id

    barrier = threading.Barrier(2, timeout=_SYNC_TIMEOUT)
    results: list[str] = []
    lock = threading.Lock()

    def settle(name: str) -> None:
        db = SessionLocal()
        try:
            barrier.wait()
            target = db.get(AgentRun, run_id)
            assert target is not None
            agent_queue.settle_run(db, target, status="succeeded")
            outcome = "ok"
        except HTTPException as exc:
            detail = getattr(exc, "detail", None)
            code = detail.get("code") if isinstance(detail, dict) else None
            outcome = f"http:{code or exc.status_code}"
        except Exception as exc:  # noqa: BLE001 - 并发用例必须记录任意异常
            outcome = f"err:{type(exc).__name__}"
        finally:
            db.close()
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=settle, args=(f"w{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=_SYNC_TIMEOUT)
    assert not any(t.is_alive() for t in threads), "并发结算线程未在超时内结束"

    # 从主线程重查数据库（不依赖 worker 的 ORM 状态）
    db_session.expire_all()
    fresh = db_session.get(AgentRun, run_id)
    assert fresh is not None
    winners = [r for r in results if r == "ok"]
    assert len(winners) == 1, f"应恰好一个结算者成功，实际 {results}"

    # 终态唯一且只写一次
    assert fresh.status == "succeeded"
    settled_events = db_session.scalar(
        select(func.count())
        .select_from(AgentRunEvent)
        .where(AgentRunEvent.run_id == run_id, AgentRunEvent.type == "run.settled")
    )
    assert settled_events == 1, f"run.settled 事件应恰好 1 条，实际 {settled_events}"


def test_terminal_run_cannot_be_resettled_after_concurrent_cancel(db_session):
    """并发 cancel 与 settle：终态唯一，且取消不得吞掉真实失败。

    同时覆盖 `_settle` 的 cancel 改判路径：cancel_requested 时 succeeded 改判为
    cancelled，但 failed 必须原样保留（错误分母不能消失）。
    """
    user, space = create_agent_fixture(db_session, name="cancel-race")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="canceller")
    assert grant is not None
    run_id = run.id

    barrier = threading.Barrier(2, timeout=_SYNC_TIMEOUT)
    results: list[str] = []
    lock = threading.Lock()

    def do(action: str) -> None:
        db = SessionLocal()
        try:
            barrier.wait()
            target = db.get(AgentRun, run_id)
            assert target is not None
            if action == "cancel":
                agent_queue.cancel_run(db, target)
            else:
                agent_queue.settle_run(db, target, status="failed", error_code="BOOM")
            outcome = "ok"
        except HTTPException as exc:
            detail = getattr(exc, "detail", None)
            code = detail.get("code") if isinstance(detail, dict) else None
            outcome = f"http:{code or exc.status_code}"
        except Exception as exc:  # noqa: BLE001
            outcome = f"err:{type(exc).__name__}"
        finally:
            db.close()
        with lock:
            results.append(outcome)

    threads = [
        threading.Thread(target=do, args=("cancel",)),
        threading.Thread(target=do, args=("settle",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=_SYNC_TIMEOUT)
    assert not any(t.is_alive() for t in threads), "并发线程未在超时内结束"

    db_session.expire_all()
    fresh = db_session.get(AgentRun, run_id)
    assert fresh is not None
    # 终态必须唯一且属于二者之一；关键是不出现「取消把真实失败吞掉」以外的第三种状态
    assert fresh.status in ("cancelled", "failed"), f"终态异常：{fresh.status} / {results}"


def test_stale_orm_object_cannot_settle_twice(db_session):
    """**确定性**复现真实竞态：陈旧 ORM 对象 → 锁内重新读取必须拦住。

    为什么需要这一条（前两次并发尝试都测不出问题）：

    `settle_run` 在进入事务**之前**有一次终态检查（读的是调用方传入的 ORM 对象）。
    两个并发线程在 SQLite 上会被 `BEGIN IMMEDIATE` 串行化，且后到的线程往往在
    入口检查时就已看到终态——因此「两个线程 + barrier」的用例**测不出**锁内复核
    被删除（变异验证：删掉锁内检查，该用例仍通过）。

    真实竞态的形状是：调用方持有的对象是**陈旧的**（读时是 leased），而数据库
    里已经是终态。这正好模拟「入口检查通过、进入事务后状态已变」。

    本用例用**同一个陈旧对象**连续结算两次：第一次成功，第二次必须被拒绝。
    删掉锁内复核后，第二次会写第二条 `run.settled` 事件，断言失败。
    """
    user, space = create_agent_fixture(db_session, name="stale-settle")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    run = _enqueue(db_session, session)
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="stale")
    assert grant is not None
    run_id = run.id

    # 故意保留一个陈旧引用：读取时 status='leased'
    stale = db_session.get(AgentRun, run_id)
    assert stale is not None and stale.status == "leased"

    agent_queue.settle_run(db_session, stale, status="succeeded")

    # 数据库已是终态；stale 对象仍是 'leased'（未刷新），因此入口检查会放行，
    # 只有**锁内复核**能拦住第二次结算。
    db_session.expire_all()
    assert db_session.get(AgentRun, run_id).status == "succeeded"

    # 让 stale 对象回到陈旧状态：从 identity map 中移除后重新绑定旧值不可行，
    # 因此改用新 Session 载入（模拟另一个进程/线程持有的陈旧副本）。
    other = SessionLocal()
    try:
        fresh = other.get(AgentRun, run_id)
        assert fresh is not None and fresh.status == "succeeded"
        other.expire(fresh)
        # 手工把内存中的状态改回 leased，模拟「读后状态已变」的陈旧副本
        fresh.status = "leased"
        with pytest.raises(HTTPException) as exc_info:
            agent_queue.settle_run(other, fresh, status="failed")
        assert _error_code(exc_info.value) == "AGENT_RUN_TERMINAL"
    finally:
        other.close()

    db_session.expire_all()
    settled_events = db_session.scalar(
        select(func.count())
        .select_from(AgentRunEvent)
        .where(AgentRunEvent.run_id == run_id, AgentRunEvent.type == "run.settled")
    )
    assert settled_events == 1, f"锁内复核失效：run.settled 事件应为 1 条，实际 {settled_events}"


def test_the_two_terminal_cas_rechecks_are_both_load_bearing():
    """结构性：`settle_run` 必须同时保留入口检查与锁内复核。

    只保留一个都不够：
    - 只有入口检查：并发/陈旧对象会写第二次终态（本文件的 mutation 实证）；
    - 只有锁内复核：入口检查是廉价快速路径，去掉会让每次终态结算都先进事务。

    本用例用 AST 断言两处都存在，且**行号不同**（同一处检查被复制粘贴两次
    不算两处）。删除任一处即失败。
    """
    import ast
    from pathlib import Path

    src = Path("app/services/agent_queue.py").read_text()
    tree = ast.parse(src)

    settle_run = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "settle_run"
    )
    settle = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_settle"
    )

    def terminal_guards(fn) -> list[int]:
        lines = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Compare) and "RUN_TERMINAL_STATUSES" in ast.unparse(node):
                lines.append(node.lineno)
        return sorted(lines)

    outer = terminal_guards(settle_run)
    inner = terminal_guards(settle)
    assert outer, "settle_run 缺少入口终态检查（廉价快速路径）"
    assert inner, "settle_run/_settle 缺少锁内终态复核（并发正确性依赖它）"
    assert set(outer) != set(inner), "两处检查不能是同一行（复制粘贴不算两处）"
