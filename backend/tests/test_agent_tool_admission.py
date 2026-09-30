"""工具执行准入：连接池/工作线程饥饿回归（09-30 AC-1 / AC-2 / R1）。

修复前的缺陷（受控复现见 `.trellis/tasks/09-30-steward-event-loop-block/research/`
`evidence/r0-root-cause.md`）：

    连接池上限 15（POOL_SIZE 5 + POOL_MAX_OVERFLOW 10）**小于** AnyIO 工作线程上限 40
    每个工具请求都要写库（fence 写锁 → 准入 CAS → 幂等占位 → 审计）→ 每个在途工具占 1 个连接
    池被占满后，后续请求在**工作线程内**等连接最长 pool_timeout=30s
    → 40 个这样的请求耗尽全部工作线程 → 心跳/lease/health 一起拿不到线程

本用例固定「工具突发 + 池余量被占」这一场景（规模、占用与预算在修复前固定，
不随后调整），断言三件事：

1. 工具执行占用的共享工作线程不超过配置名额（等名额既不占线程也不占连接）；
2. 突发期间心跳仍在预算内被服务（不靠放宽 sidecar 超时通过）；
3. 工具批次最终全部完成（不是靠拒绝全部工具通过）。

占用连接池用 ``engine.connect()`` 而不是 SQLite 写锁：SQLite 单写者，写锁无法
耗尽连接池。工具体本身被替换为一个**持有连接期间不退出的**桩，代表真实工具
（真实运行中工具持连接的时间由查询决定）；不这样做时桩在微秒级返回，池来不及
被占满，用例就测不到缺陷。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import anyio
import pytest
from test_agent_execution_fence import _steward_world

from app import config
from app.services import steward_tools
from app.services.agent_tokens import issue_run_token

# 工具突发规模：真实数据一轮 21–26 个调用、并发峰值 8–23（run 147=23、run 189=16）。
# 取 40 = AnyIO 工作线程上限，即「足够多的请求在池等待即可耗尽全部工作线程」。
TOOL_BURST = 40
# 桩工具持连接时长：让池在突发期间保持占满，从而区分「在事件循环上等名额」与
# 「在工作线程内等连接」。
TOOL_HOLD_SECONDS = 1.0
# 心跳预算：远小于 sidecar 的 15s 请求超时（修复前固定，不随后调大）。
HEARTBEAT_BUDGET_SECONDS = 1.0
# 不外部占用连接：真实场景是**工具自己**把池占满（每个在途工具持 1 条连接）。
# 修复前 40 个工具全部进入工作线程并各要一条连接 → 池（15）被打满 → 其余 25 个在
# **工作线程内**等连接（最长 pool_timeout）→ 工作线程被耗尽 → 心跳拿不到执行机会。
# 修复后名额（8）限制同时执行的工具数 → 最多占 8 条连接 → 余下 7 条供心跳等端点使用。


def _await_condition(check: Callable[[], bool], *, timeout: float, message: str) -> None:
    """轮询等待条件成立；超时即前提/收敛失败（禁止无限等待挂死套件）。"""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if check():
            return
        time.sleep(0.05)
    pytest.fail(message)


def _borrowed_workers(client) -> int:
    """当前 AnyIO 工作线程借用数（共享线程池，与心跳等端点共用）。"""
    limiter = client.portal.call(anyio.to_thread.current_default_thread_limiter)
    return int(limiter.borrowed_tokens)


def _peak_borrowed_workers(client, seconds: float) -> int:
    peak = 0
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        peak = max(peak, _borrowed_workers(client))
        time.sleep(0.02)
    return peak


def _steward_tool_world(db_session) -> tuple[int, int, str]:
    """最小 steward 工具世界：返回 (run_id, steward_job_id, run token)。"""
    user, space, job, _plan, run, attempt = _steward_world(db_session, assist_kind="terminology")
    run.tool_allowlist_json = [steward_tools.TOOL_GET_SPACE_SNAPSHOT]
    db_session.commit()
    token = issue_run_token(
        run_id=run.id,
        job_id=job.id,
        attempt=run.attempt,
        agent_kind="steward",
        space_id=space.id,
        tool_allowlist=list(run.tool_allowlist_json),
        steward_attempt_id=attempt.id,
        viewer_account_id=user.account.id,
    )
    return run.id, job.id, token


def test_pool_exhaustion_still_serves_heartbeat_and_converges(
    internal_client, db_session, monkeypatch
) -> None:
    # 单个共享事件循环（`with` 让 TestClient 常驻一个 portal）：不带 `with` 时
    # TestClient 会为**每个请求**新建 loop 与线程池，测不到共享资源竞争。
    with internal_client:
        _run_pool_exhaustion_scenario(internal_client, db_session, monkeypatch)


def _run_pool_exhaustion_scenario(internal_client, db_session, monkeypatch) -> None:
    run_id, job_id, token = _steward_tool_world(db_session)
    headers = {"Authorization": f"Bearer {token}"}
    tool_url = (
        f"/internal/agent/runs/{run_id}/tools/{steward_tools.TOOL_GET_SPACE_SNAPSHOT}/execute"
    )
    heartbeat_url = f"/internal/agent/jobs/{job_id}/heartbeat"

    # 桩工具：真结果照常产生，但在**持有连接期间**多停留 TOOL_HOLD_SECONDS。
    original_execute = steward_tools.execute_steward_tool

    def held_execute(db, *, execution, name, input_payload):
        result = original_execute(db, execution=execution, name=name, input_payload=input_payload)
        time.sleep(TOOL_HOLD_SECONDS)
        return result

    monkeypatch.setattr(steward_tools, "execute_steward_tool", held_execute)

    responses: list[tuple[int, dict[str, object]]] = []
    responses_lock = threading.Lock()
    heartbeat: dict[str, object] = {}

    def fire_tool(index: int) -> None:
        try:
            response = internal_client.post(
                tool_url,
                headers=headers,
                json={"version": 1, "input": {}, "tool_call_id": f"tc-{index}"},
            )
            body = response.json()
        except Exception as exc:  # noqa: BLE001 - 观测：任何客户端失败也算一次结果
            with responses_lock:
                responses.append((-1, {"error": type(exc).__name__}))
            return
        with responses_lock:
            responses.append((response.status_code, body))

    def fire_heartbeat() -> None:
        started = time.perf_counter()
        try:
            response = internal_client.post(heartbeat_url, headers=headers, json={})
            heartbeat["status"] = response.status_code
        except Exception as exc:  # noqa: BLE001 - 观测：失败也要记录，不能静默
            heartbeat["error"] = type(exc).__name__
        heartbeat["seconds"] = time.perf_counter() - started

    with ThreadPoolExecutor(max_workers=TOOL_BURST) as pool:
        futures = [pool.submit(fire_tool, index) for index in range(TOOL_BURST)]
        # 前提：突发确实占住了共享工作线程（修复前 40/40，修复后 ≤ 名额）。
        _await_condition(
            lambda: _borrowed_workers(internal_client) >= 1,
            timeout=10.0,
            message="工具突发未在超时内进入执行（用例前提不成立）",
        )

        heartbeat_thread = threading.Thread(target=fire_heartbeat, daemon=True)
        heartbeat_thread.start()
        heartbeat_thread.join(timeout=HEARTBEAT_BUDGET_SECONDS)
        assert not heartbeat_thread.is_alive(), (
            f"心跳在 {HEARTBEAT_BUDGET_SECONDS}s 预算内未被服务："
            "工具突发把共享工作线程/连接占满，心跳拿不到执行机会"
        )
        assert heartbeat.get("error") is None, f"心跳请求失败：{heartbeat.get('error')}"
        assert heartbeat["status"] == 200, f"心跳状态码异常：{heartbeat}"
        assert (
            float(heartbeat["seconds"]) < HEARTBEAT_BUDGET_SECONDS
        ), f"心跳延迟 {heartbeat['seconds']}s 超出预算 {HEARTBEAT_BUDGET_SECONDS}s"

        borrowed = _peak_borrowed_workers(internal_client, seconds=0.5)
        # R1：等执行机会不得占用共享工作线程（否则心跳可用只是运气好）。
        assert borrowed <= config.AGENT_TOOL_MAX_CONCURRENT_EXECUTIONS, (
            "工具执行占用的共享工作线程超过配置名额 "
            f"({borrowed} > {config.AGENT_TOOL_MAX_CONCURRENT_EXECUTIONS})："
            "等待执行机会的请求不应占用工作线程"
        )

        # 工具批次必须最终完成：不得靠拒绝全部工具取得「心跳可用」。
        _await_condition(
            lambda: all(future.done() for future in futures),
            timeout=30.0,
            message=f"工具批次未收敛（{sum(1 for f in futures if f.done())}/{TOOL_BURST}）",
        )
    with responses_lock:
        observed = list(responses)
    assert len(observed) == TOOL_BURST, f"工具批次结果数不符：{len(observed)}"
    failures = [
        (status, body) for status, body in observed if status != 200 or body.get("ok") is not True
    ]
    assert not failures, f"工具批次存在失败：{failures[:3]}"


def test_cancelled_waiter_releases_nothing_and_the_slot_is_reusable() -> None:
    """等待中被取消不得泄漏名额（R1：排队取消移除等待者）。

    协程在等待名额时被取消（客户端断开），必须把等待者移出队列且**不消耗**名额；
    否则反复取消会逐步吃掉并发上限，最终连正常工具请求也排不进来。
    """
    import asyncio

    from app import config
    from app.api import internal_agent

    async def scenario() -> None:
        cap = config.AGENT_TOOL_MAX_CONCURRENT_EXECUTIONS
        limiter = internal_agent._tool_slot_limiter()
        for _ in range(cap):
            await limiter.acquire()

        waiter = asyncio.ensure_future(internal_agent._acquire_tool_slot(1))
        await asyncio.sleep(0.05)
        assert not waiter.done(), "名额已满时不应立即获得名额"
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        # 释放全部名额后仍应能完整取回 cap 个（取消没有吃掉任何一个）。
        for _ in range(cap):
            limiter.release()
        for _ in range(cap):
            acquired = await asyncio.wait_for(
                asyncio.ensure_future(internal_agent._acquire_tool_slot(1)), timeout=1.0
            )
            internal_agent._release_tool_slot(acquired)

    asyncio.run(scenario())


def test_admission_cap_must_leave_connections_for_other_endpoints() -> None:
    """名额上界由 ensure_ready 强制：必须小于连接池上限。

    名额 ≥ 池上限时，工具执行可把池占满，等待连接的请求随即占满工作线程，
    重现「连接池等待耗尽线程池」的缺陷（本文件主用例覆盖的现象）。
    """
    from app import config
    from app.db import POOL_MAX_CONNECTIONS

    assert config.AGENT_TOOL_MAX_CONCURRENT_EXECUTIONS <= POOL_MAX_CONNECTIONS - 1

    original = config.AGENT_TOOL_MAX_CONCURRENT_EXECUTIONS
    try:
        config.AGENT_TOOL_MAX_CONCURRENT_EXECUTIONS = POOL_MAX_CONNECTIONS
        with pytest.raises(RuntimeError, match="AGENT_TOOL_MAX_CONCURRENT_EXECUTIONS"):
            config._validate_agent_tool_admission()
        config.AGENT_TOOL_MAX_CONCURRENT_EXECUTIONS = 0
        with pytest.raises(RuntimeError, match="AGENT_TOOL_MAX_CONCURRENT_EXECUTIONS"):
            config._validate_agent_tool_admission()
    finally:
        config.AGENT_TOOL_MAX_CONCURRENT_EXECUTIONS = original
