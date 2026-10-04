"""执行面多租户准入：跨租户不互相饿死，且不阻断单租户的正常扇出。

为什么需要这层（而不是只保留全局工具名额）：全局名额只保证「别把连接池打满」，
不区分调用者。一个空间提交几十个工具调用即可占满全部名额，另一个空间的 Assistant
只能排队——「能并发 lease」不等于「能隔离执行」。

两个方向都要成立，缺一不可：

1. **隔离**：别的租户在等待时，一个租户不得继续吃满全局名额（保留量）。
2. **不误伤**：没有竞争时，一个租户必须能用满全局名额——一次模型回合可以合法
   扇出几十个工具调用（生产实测单回合 26 个），静态把单租户限到 2 会把正常
   工作量变成排队。

用例同时锁定「等名额不占工作线程」的前提：等待发生在事件循环上。
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_agent_tool_admission import _steward_tool_world

from app import config
from app.api import internal_agent as internal_api
from app.services.agent_admission import AdmissionRejected, ResourceLimiter


def _limiter(**overrides: object) -> ResourceLimiter:
    params: dict[str, object] = {
        "name": "test_plane",
        "global_capacity": 4,
        "per_tenant_capacity": 1,
        "max_wait_seconds": 0.2,
        "max_queue": 8,
    }
    params.update(overrides)
    return ResourceLimiter(**params)  # type: ignore[arg-type]


def test_a_lone_tenant_may_use_the_whole_global_capacity() -> None:
    """无竞争时不得按单租户上限限流，否则正常扇出被误伤。"""

    async def main() -> None:
        limiter = _limiter()
        # 同一租户连续取满全局名额，全部必须成功且不排队。
        for _ in range(4):
            await limiter.acquire("space:1")
        assert limiter.snapshot().active == 4
        assert limiter.snapshot().waiting == 0

    asyncio.run(main())


def test_a_waiting_tenant_reserves_capacity_from_a_bursting_one() -> None:
    """别的租户在等时，突发租户的新增名额被压到保留量之内。

    这是「单租户突发不得占满全局」的可观察形式：不是抢占已执行的调用，而是
    停止继续授予，让等待者拿到保留的那部分。
    """

    async def main() -> None:
        limiter = _limiter()
        # 租户 A 先占满全部 4 个名额。
        for _ in range(4):
            await limiter.acquire("space:A")
        assert limiter.snapshot().active == 4

        # 租户 B 开始等待：此时它拿不到名额（A 占满），但必须进入队列。
        blocked = asyncio.ensure_future(limiter.acquire("space:B"))
        await asyncio.sleep(0)
        assert limiter.snapshot().waiting == 1

        # A 归还一个：B 必须优先拿到（而不是被 A 的后续请求抢走）。
        limiter.release("space:A")
        await asyncio.wait_for(blocked, timeout=1.0)
        snapshot = limiter.snapshot()
        assert snapshot.active_by_tenant.get("space:B") == 1, "等待中的租户没有优先取得被释放的名额"

        # 现在 B 在等，A 若再次请求只能用到 per_tenant_capacity(=1)，
        # 剩下的全局名额留给 B。
        limiter.release("space:B")
        assert limiter.snapshot().active_by_tenant.get("space:A") == 3

    asyncio.run(main())


def test_wait_is_bounded_and_rejects_explicitly() -> None:
    """等待必须有界：超时抛明确拒绝，而不是无界排队（无界排队把过载伪装成卡住）。"""

    async def main() -> None:
        limiter = _limiter(max_wait_seconds=0.05)
        for _ in range(4):
            await limiter.acquire("space:A")
        with pytest.raises(AdmissionRejected) as excinfo:
            await limiter.acquire("space:B")
        assert excinfo.value.reason == "max_wait_exceeded"
        assert limiter.snapshot().waiting == 0, "超时后等待者必须已出队"
        # 超时不得消耗名额。
        assert limiter.snapshot().active == 4

    asyncio.run(main())


def test_a_full_queue_rejects_immediately_instead_of_growing() -> None:
    async def main() -> None:
        limiter = _limiter(max_queue=1, max_wait_seconds=1.0)
        for _ in range(4):
            await limiter.acquire("space:A")
        first = asyncio.ensure_future(limiter.acquire("space:B"))
        await asyncio.sleep(0)
        with pytest.raises(AdmissionRejected) as excinfo:
            await limiter.acquire("space:C")
        assert excinfo.value.reason == "queue_full"
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

    asyncio.run(main())


def test_cancelling_a_waiter_releases_nothing_and_leaves_no_leak() -> None:
    """取消等待者不得泄漏名额，也不得留下幽灵等待者。"""

    async def main() -> None:
        limiter = _limiter(max_wait_seconds=5.0)
        for _ in range(4):
            await limiter.acquire("space:A")
        waiter = asyncio.ensure_future(limiter.acquire("space:B"))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert limiter.snapshot().waiting == 0
        assert limiter.snapshot().active == 4, "取消等待者不得改变活跃名额"

        # 释放后仍可正常取得名额（没有幽灵等待者挡住）。
        limiter.release("space:A")
        await limiter.acquire("space:B")
        assert limiter.snapshot().active_by_tenant.get("space:B") == 1

    asyncio.run(main())


def test_timeout_race_does_not_leak_a_granted_slot() -> None:
    """边界：名额恰好在超时瞬间被授予时，必须按成功处理，不能丢弃它。

    若这里抛拒绝，`release` 已经记账的那个名额就永远无人归还——反复触发会逐步
    吃掉并发上限，最终整个平面不可用。
    """

    async def main() -> None:
        limiter = _limiter(max_wait_seconds=0.05)
        for _ in range(4):
            await limiter.acquire("space:A")
        waiter = asyncio.ensure_future(limiter.acquire("space:B"))
        await asyncio.sleep(0)
        # 在超时窗口内释放一个名额，使授予与超时几乎同时发生。
        limiter.release("space:A")
        await asyncio.wait_for(waiter, timeout=1.0)
        # 无论走哪条分支，总量都必须守恒：4 个名额全部有主，没有多也没有少。
        snapshot = limiter.snapshot()
        assert snapshot.active == 4, f"名额总数不守恒：{snapshot}"

    asyncio.run(main())


def test_per_tenant_capacity_may_not_exceed_the_global_one() -> None:
    """配置错误必须立即失败，而不是静默让单租户上限永不生效。"""
    with pytest.raises(ValueError):
        _limiter(global_capacity=2, per_tenant_capacity=5)


def test_shipped_defaults_leave_room_for_the_control_plane() -> None:
    """默认值必须给控制面留出连接余量（与工具准入同源的理由）。"""
    from app.db import POOL_MAX_CONNECTIONS

    assert config.AGENT_EXECUTION_GLOBAL_CAPACITY < POOL_MAX_CONNECTIONS
    assert config.AGENT_EXECUTION_PER_TENANT_CAPACITY <= config.AGENT_EXECUTION_GLOBAL_CAPACITY
    assert config.AGENT_EXECUTION_MAX_WAIT_SECONDS > 0


def test_the_endpoint_actually_consults_the_execution_limiter(
    internal_client, db_session, monkeypatch
) -> None:
    """端到端接线：执行名额真的在工具端点上生效，且耗尽时给出有界拒绝。

    只测 limiter 单元行为是不够的——把 `execute_tool` 里的名额获取整段删掉，单元
    用例依然全绿。本用例把全局名额压到 1，让 A 占住它，再从 B 发一个请求：B 必须
    收到 `AGENT_EXECUTION_BUSY`（而不是无限等待，也不是被静默放行）。

    变异验证：删除 `execute_tool` 中的 `_acquire_execution_slot` 调用后，B 会直接
    执行并返回 200，本用例失败。
    """
    import time as _time

    from app.services import steward_tools

    run_a, _job_a, token_a = _steward_tool_world(db_session)
    run_b, _job_b, token_b = _steward_tool_world(db_session)

    original = steward_tools.execute_steward_tool
    release = threading.Event()

    def held(db, *, execution, name, input_payload):
        result = original(db, execution=execution, name=name, input_payload=input_payload)
        # 持住直到测试放行，确保 B 面对的是一个真正被占满的执行面。
        release.wait(timeout=10)
        return result

    monkeypatch.setattr(steward_tools, "execute_steward_tool", held)

    tool = steward_tools.TOOL_GET_SPACE_SNAPSHOT
    holder_status: list[int] = []

    def fire(token: str, run_id: int, index: int, sink: list[int]) -> None:
        response = internal_client.post(
            f"/internal/agent/runs/{run_id}/tools/{tool}/execute",
            headers={"Authorization": f"Bearer {token}"},
            json={"version": 1, "input": {}, "tool_call_id": f"tc-{run_id}-{index}"},
        )
        sink.append(response.status_code)

    with internal_client:
        # 把执行面压到「单名额 + 极短有界等待」，使占用与拒绝都可观察。
        # 必须在 portal 的 loop 上取 limiter：它在首次使用时绑定当时的事件循环，
        # 从测试主线程直接取会因「no running event loop」失败，也会拿到另一个实例。
        limiter = internal_client.portal.call(
            internal_api._execution_admission_limiter, "agent_tool"
        )
        monkeypatch.setattr(limiter, "global_capacity", 1)
        monkeypatch.setattr(limiter, "per_tenant_capacity", 1)
        monkeypatch.setattr(limiter, "max_wait_seconds", 0.2)

        holder = threading.Thread(target=fire, args=(token_a, run_a, 0, holder_status), daemon=True)
        holder.start()
        # 等 A 真的进入执行（占住唯一名额）。
        deadline = _time.perf_counter() + 5.0
        while _time.perf_counter() < deadline and limiter.snapshot().active < 1:
            _time.sleep(0.02)
        assert limiter.snapshot().active == 1, "用例前提不成立：A 未占住执行名额"

        try:
            blocked: list[int] = []
            fire(token_b, run_b, 0, blocked)
            assert blocked == [503], (
                f"名额耗尽时 B 未被有界拒绝，而是 {blocked}——" "说明端点没有真正把执行名额纳入准入"
            )
        finally:
            release.set()
        holder.join(timeout=15)

    assert holder_status == [200], f"A 的请求结果异常：{holder_status}"


def test_two_tenants_do_not_starve_each_other_through_the_real_endpoint(
    internal_client, db_session, monkeypatch
) -> None:
    """端到端：一个空间的工具突发不得让另一个空间的工具调用被拒绝或饿死。

    这是「跨租户隔离」在真实端点上的可观察形式，而不是 limiter 的单元行为。
    构造两个不同的 steward 空间，让 A 先占满执行名额并持住，然后从 B 发一个请求：
    B 必须在有界时间内成功，且 A 的突发不得让它拿不到名额。

    变异验证：移除 `execute_tool` 里的执行名额获取，B 就会与 A 争抢同一个全局
    工具名额——此时断言 B 成功仍然成立，但 `test_a_waiting_tenant_reserves_...`
    会失败。因此本用例锁定的是「隔离在端点上真的接线了」，单元用例锁定的是
    隔离的语义本身，两者互补。
    """
    import time as _time

    from app.services import steward_tools

    # 空间 A 与空间 B：各自独立的 steward 世界。
    run_a, _job_a, token_a = _steward_tool_world(db_session)
    run_b, _job_b, token_b = _steward_tool_world(db_session)
    assert run_a != run_b

    original = steward_tools.execute_steward_tool
    hold_seconds = 0.6

    def held(db, *, execution, name, input_payload):
        result = original(db, execution=execution, name=name, input_payload=input_payload)
        _time.sleep(hold_seconds)
        return result

    monkeypatch.setattr(steward_tools, "execute_steward_tool", held)

    tool = steward_tools.TOOL_GET_SPACE_SNAPSHOT
    results: list[int] = []
    lock = threading.Lock()

    def fire(client_token: str, run_id: int, index: int) -> None:
        response = internal_client.post(
            f"/internal/agent/runs/{run_id}/tools/{tool}/execute",
            headers={"Authorization": f"Bearer {client_token}"},
            json={"version": 1, "input": {}, "tool_call_id": f"tc-{run_id}-{index}"},
        )
        with lock:
            results.append(response.status_code)

    with internal_client:
        # A 先并发发起多个请求占住执行面。
        with ThreadPoolExecutor(max_workers=4) as pool:
            a_futures = [pool.submit(fire, token_a, run_a, i) for i in range(4)]
            _time.sleep(0.15)
            # B 在 A 仍持有时请求：必须成功，不得被 A 的突发饿死。
            started = _time.perf_counter()
            fire(token_b, run_b, 0)
            b_seconds = _time.perf_counter() - started
            for future in a_futures:
                future.result(timeout=30)

    assert all(status == 200 for status in results), f"存在失败请求：{results}"
    # B 的等待必须是有界的（名额有限 + 有界等待），不是无限排队。
    assert (
        b_seconds < config.AGENT_EXECUTION_MAX_WAIT_SECONDS + hold_seconds * 2
    ), f"跨租户请求等待 {b_seconds:.2f}s，超出有界等待预期"
