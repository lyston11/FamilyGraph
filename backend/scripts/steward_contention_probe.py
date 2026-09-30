"""P0 定位探针：同一 steward run 并发工具执行与心跳竞争。

设计（09-30-steward-event-loop-block）要求先补齐的证据，本脚本测量：

- 事件循环延迟（loop lag，独立线程采样）
- AnyIO threadpool 占用（borrowed/total tokens）
- 数据库连接池占用（checkedout）
- 心跳请求延迟（与工具执行并发）

关键：使用**真实 HTTP 服务器**（uvicorn），不是 TestClient。TestClient 直接把
请求投给 ASGI app，不经过 AnyIO worker 线程池，因此无法复现线程饥饿。

用法（必须显式设 DATA_DIR，避免污染开发库）：

    TMP=$(mktemp -d) && DATA_DIR="$TMP" .venv/bin/python scripts/steward_contention_probe.py

输出为结构化 JSON，供 research/evidence 引用。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

if not os.environ.get("DATA_DIR"):
    sys.exit("必须显式设置 DATA_DIR（隔离目录），避免写入开发库")

# 与 tests/conftest.py 同口径的环境变量，必须在导入任何 app 模块前注入。
# DATA_DIR 不在此覆盖：探针要求调用方显式提供隔离目录。
os.environ.setdefault("SECRET_KEY", "probe-secret-key")
os.environ.setdefault("BCRYPT_ROUNDS", "4")
os.environ.setdefault("ADMIN_JWT_SECRET", "probe-admin-jwt-secret-0123456789abcdef")
os.environ.setdefault("ADMIN_JWT_ISSUER", "familygraph-admin-probe")
os.environ.setdefault("ADMIN_JWT_AUDIENCE", "familygraph-admin-web-probe")
os.environ.setdefault("AGENT_SERVICE_SECRET", "probe-agent-service-secret")
os.environ.setdefault("AGENT_RUNTIME_ENABLED", "1")
os.environ.setdefault("STEWARD_ENABLED", "1")
os.environ.setdefault("STEWARD_PI_RUNTIME_ENABLED", "1")
os.environ.setdefault("MEMORY_ENABLED", "1")
os.environ.setdefault("RAG_ENABLED", "1")
os.environ.setdefault("BEHAVIOR_PROJECTION_ENABLED", "1")
os.environ.setdefault("POLICY_GUARD_ENABLED", "1")

# 环境变量就绪后才能导入 config
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anyio.to_thread  # noqa: E402
import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.api.internal_agent import router as internal_router  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models.agent import AgentRun  # noqa: E402
from app.models.steward import (  # noqa: E402
    StewardAssistPlan,
    StewardJob,
    StewardModelCall,
    StewardPublication,
)
from app.services import (  # noqa: E402
    agent_tokens,
    provider_proxy,
    steward_tools,
)
from app.utils import timeutil  # noqa: E402

# 并发规模：历史观测一轮 21–26 个工具调用（run 135=26, run 189=24）。
TOOL_CONCURRENCY = int(os.environ.get("PROBE_TOOL_CONCURRENCY", "26"))
HEARTBEAT_INTERVAL = 0.2
BURST_SECONDS = float(os.environ.get("PROBE_BURST_SECONDS", "20"))
STREAM_SECONDS = float(os.environ.get("PROBE_STREAM_SECONDS", "8"))
PORT = int(os.environ.get("PROBE_PORT", "8791"))


def build_world() -> tuple[int, str, int, int]:
    """最小可用 steward world：job → plan → attempt → run，工具在 allowlist 内。

    若库中已有带已发布 generation 的空间（真实数据副本），复用它——空库上
    ``get_space_snapshot`` 会立刻返回 ``not_published``，几乎不触碰数据库，
    无法反映真实查询成本。
    """
    from tests.conftest import create_agent_fixture

    db = SessionLocal()
    try:
        existing = db.scalar(
            select(StewardPublication.space_id).order_by(StewardPublication.space_id.desc())
        )
        if existing is not None:
            return _world_for_existing_space(db, int(existing))
        user, space = create_agent_fixture(db, name="probe-contention")
        now = timeutil.utcnow()
        job = StewardJob(
            space_id=space.id,
            cause="integrity_scan",
            trigger_cursor=1,
            status="succeeded",
            attempt=1,
            max_attempts=3,
            checkpoint_json={},
            policy_version="p1",
            lease_expires_at=now + timedelta(seconds=3600),
            leased_by="probe",
            created_at=now,
            updated_at=now,
        )
        db.add(job)
        db.flush()
        plan = StewardAssistPlan(
            space_id=space.id,
            job_id=job.id,
            evidence_hash="e" * 64,
            policy_version="p1",
            fence_json={},
            created_at=now,
            deadline_at=now + timedelta(seconds=3600),
        )
        db.add(plan)
        db.flush()
        tool = steward_tools.TOOL_GET_SPACE_SNAPSHOT
        run = AgentRun(
            session_id=None,
            message_id=None,
            job_id=None,
            kind="steward",
            status="running",
            attempt=1,
            max_attempts=1,
            policy_version="p1",
            tool_allowlist_json=[tool],
            lease_expires_at=now + timedelta(seconds=3600),
            heartbeat_at=now,
            cancel_requested=False,
            created_at=now,
            updated_at=now,
        )
        db.add(run)
        db.flush()
        attempt = StewardModelCall(
            space_id=space.id,
            job_id=job.id,
            policy_version="p1",
            assist_kind="candidate",
            prompt_digest="d" * 64,
            prompt_chars=100,
            status="in_flight",
            seq=1,
            created_at=now,
            plan_id=plan.id,
            subject_key="facts",
            input_hash="h" * 64,
            attempt_no=1,
            carrier="pi",
            lease_owner="probe",
            lease_until=now + timedelta(seconds=3600),
            run_id=run.id,
        )
        db.add(attempt)
        db.commit()
        token = agent_tokens.issue_run_token(
            run_id=run.id,
            job_id=job.id,
            attempt=run.attempt,
            agent_kind="steward",
            space_id=space.id,
            tool_allowlist=[tool],
            steward_attempt_id=attempt.id,
        )
        return run.id, token, job.id, attempt.id
    finally:
        db.close()


def ensure_probe_provider(db, space_id: int) -> int:
    """建一个探针自有 provider，使 provider 流能在真实 HTTP 路径上建立。

    复用开发库副本里的 provider 行会因 SECRET_KEY 不同而解密失败
    （``resolve_runtime`` 返回 None），那样流根本建立不起来，
    「流期间是否持有池连接」就测不到。
    """
    from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
    from app.utils import secretbox

    now = timeutil.utcnow()
    existing = db.scalar(select(AgentProvider).where(AgentProvider.name == "probe-provider"))
    if existing is None:
        existing = AgentProvider(
            name="probe-provider",
            kind="openai_compatible",
            api="openai-completions",
            base_url="https://probe.invalid/v1",
            secret_ciphertext=secretbox.encrypt_secret("sk-probe-secret-value"),
            allowed_models_json=["probe-model"],
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        db.add(existing)
        db.flush()
    setting = db.scalar(
        select(AgentSpaceProviderSetting).where(
            AgentSpaceProviderSetting.space_id == space_id,
            AgentSpaceProviderSetting.agent_kind == "steward",
        )
    )
    if setting is None:
        setting = AgentSpaceProviderSetting(
            space_id=space_id,
            agent_kind="steward",
            provider_id=existing.id,
            model="probe-model",
            cloud_allowed=True,
            local_required=False,
            enabled=True,
        )
        db.add(setting)
    else:
        setting.provider_id = existing.id
        setting.model = "probe-model"
        setting.enabled = True
    db.commit()
    return existing.id


def _world_for_existing_space(db, space_id: int) -> tuple[int, str, int, int]:
    """在已有已发布 generation 的空间上建 job/plan/attempt/run（真实查询成本）。"""
    now = timeutil.utcnow()
    ensure_probe_provider(db, space_id)
    job = StewardJob(
        space_id=space_id,
        cause="integrity_scan",
        trigger_cursor=1,
        status="succeeded",
        attempt=1,
        max_attempts=3,
        checkpoint_json={},
        policy_version="p1",
        lease_expires_at=now + timedelta(seconds=3600),
        leased_by="probe",
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.flush()
    plan = StewardAssistPlan(
        space_id=space_id,
        job_id=job.id,
        evidence_hash="e" * 64,
        policy_version="p1",
        fence_json={},
        created_at=now,
        deadline_at=now + timedelta(seconds=3600),
    )
    db.add(plan)
    db.flush()
    tool = steward_tools.TOOL_GET_SPACE_SNAPSHOT
    run = AgentRun(
        session_id=None,
        message_id=None,
        job_id=None,
        kind="steward",
        status="running",
        attempt=1,
        max_attempts=1,
        policy_version="p1",
        tool_allowlist_json=[tool],
        lease_expires_at=now + timedelta(seconds=3600),
        heartbeat_at=now,
        cancel_requested=False,
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    db.flush()
    attempt = StewardModelCall(
        space_id=space_id,
        job_id=job.id,
        policy_version="p1",
        assist_kind="candidate",
        prompt_digest="d" * 64,
        prompt_chars=100,
        status="in_flight",
        seq=999_999,
        created_at=now,
        plan_id=plan.id,
        subject_key="facts",
        input_hash="h" * 64,
        attempt_no=1,
        carrier="pi",
        lease_owner="probe",
        lease_until=now + timedelta(seconds=3600),
        run_id=run.id,
    )
    db.add(attempt)
    db.commit()
    token = agent_tokens.issue_run_token(
        run_id=run.id,
        job_id=job.id,
        attempt=run.attempt,
        agent_kind="steward",
        space_id=space_id,
        tool_allowlist=[tool],
        steward_attempt_id=attempt.id,
    )
    return run.id, token, job.id, attempt.id


REAL_ASYNC_CLIENT = httpx.AsyncClient


def install_fake_upstream(gap: float) -> None:
    """让 provider 流在探针内可建立（生产由真实上游提供）。

    ``provider_proxy.httpx`` 与本模块的 ``httpx`` 是同一个模块对象，所以替换会
    同时影响探针自己的客户端；调用方必须用 ``REAL_ASYNC_CLIENT`` 发探针请求。
    """
    _FakeAsyncClient.response = _SlowUpstream(chunks=10_000, gap=gap)
    provider_proxy.httpx.AsyncClient = _FakeAsyncClient  # type: ignore[assignment]


def build_app() -> FastAPI:
    """只挂 internal router，不启动 lifespan（避免维护循环干扰探针）。"""
    app = FastAPI()
    app.include_router(internal_router, prefix="/internal/agent")
    return app


class LoopLagSampler(threading.Thread):
    """独立线程采样事件循环延迟。

    用一个极短的 asyncio 任务往返测量：延迟高说明 loop 被同步代码占住。
    """

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.samples: list[float] = []
        self._halt = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def run(self) -> None:
        while not self._halt.is_set():
            loop = self._loop
            if loop is None or loop.is_closed():
                time.sleep(0.01)
                continue
            fut: asyncio.Future[float] = loop.create_future()

            def stamp(target: asyncio.Future[float] = fut) -> None:
                # 默认参数绑定当前 future：闭包捕获循环变量会读到最后一次的值。
                if not target.done():
                    target.set_result(time.perf_counter())

            try:
                t0 = time.perf_counter()
                loop.call_soon_threadsafe(stamp)
                # 等待 loop 执行回调；用 busy-wait 避免把等待算进 lag
                deadline = time.perf_counter() + 2.0
                while not fut.done() and time.perf_counter() < deadline:
                    time.sleep(0.0002)
                if fut.done():
                    self.samples.append(fut.result() - t0)
            except RuntimeError:
                break
            time.sleep(0.01)

    def stop(self) -> None:
        self._halt.set()


def resource_sample() -> dict[str, int]:
    limiter = anyio.to_thread.current_default_thread_limiter()
    return {
        "anyio_borrowed": limiter.borrowed_tokens,
        "anyio_total": limiter.total_tokens,
        "pool_checkedout": engine.pool.checkedout(),
        "pool_size": engine.pool.size(),
    }


class _SlowUpstream:
    """慢速上游：每 gap 秒产出一个 chunk，模拟长 SSE 流。"""

    def __init__(self, chunks: int, gap: float) -> None:
        self._chunks = [b"data: {}\n\n"] * chunks
        self._gap = gap
        self.status_code = 200
        self.headers = {"content-type": "text/event-stream"}

    async def aiter_raw(self):
        for chunk in self._chunks:
            await asyncio.sleep(self._gap)
            yield chunk

    async def aclose(self) -> None:
        pass


class _FakeAsyncClient:
    """替换 httpx.AsyncClient，使 provider 流无需真实凭据即可建立。"""

    response: _SlowUpstream | None = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def build_request(self, method: str, url: str, content: object = None, headers: object = None):
        return {"method": method, "url": url, "content": content, "headers": dict(headers or {})}

    async def send(self, request: object, *, stream: bool = False):
        return self.response

    async def aclose(self) -> None:
        pass


stream_status: list[object] = []
stream_bytes: list[int] = []


async def open_slow_stream(client: httpx.AsyncClient, run_id: int, headers: dict[str, str]) -> None:
    """打开一条 provider 流并保持读取，模拟 sidecar 的长模型调用。

    真实路径下请求级 Session 由 get_db 创建，而流式 generator 在流结束前一直
    持有它；因此这条连接会在整个流期间被占用。探针据此观察连接池与心跳。
    """
    try:
        async with client.stream(
            "POST",
            f"/internal/agent/runs/{run_id}/provider/chat/completions",
            headers=headers,
            json={
                "model": "probe-model",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        ) as response:
            stream_status.append(response.status_code)
            async for chunk in response.aiter_raw():
                stream_bytes.append(len(chunk))
    except Exception as exc:  # noqa: BLE001 - 流被取消/上游不可用都算观测
        stream_status.append(f"ERR:{type(exc).__name__}")


async def run_probe() -> dict[str, object]:
    run_id, token, job_id, attempt_id = build_world()
    tool = steward_tools.TOOL_GET_SPACE_SNAPSHOT
    base = f"http://127.0.0.1:{PORT}"
    headers = {"Authorization": f"Bearer {token}"}

    install_fake_upstream(gap=0.05)
    server = uvicorn.Server(
        uvicorn.Config(build_app(), host="127.0.0.1", port=PORT, log_level="warning")
    )
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)

    sampler = LoopLagSampler()
    sampler.attach(asyncio.get_running_loop())
    sampler.start()

    tool_latencies: list[float] = []
    heartbeat_latencies: list[float] = []
    resource_peaks: list[dict[str, int]] = []
    stop = asyncio.Event()

    async with REAL_ASYNC_CLIENT(base_url=base, timeout=120) as client:

        async def fire_tool(n: int) -> None:
            t0 = time.perf_counter()
            try:
                await client.post(
                    f"/internal/agent/runs/{run_id}/tools/{tool}/execute",
                    headers=headers,
                    json={"version": 1, "input": {}, "tool_call_id": f"probe-{n}"},
                )
            except Exception:  # noqa: BLE001 - 超时也算一个观测
                pass
            tool_latencies.append(time.perf_counter() - t0)

        async def heartbeat_loop() -> None:
            while not stop.is_set():
                t0 = time.perf_counter()
                try:
                    await client.post(
                        f"/internal/agent/jobs/{job_id}/heartbeat",
                        headers=headers,
                        json={},
                    )
                except Exception:  # noqa: BLE001
                    pass
                heartbeat_latencies.append(time.perf_counter() - t0)
                await asyncio.sleep(HEARTBEAT_INTERVAL)

        async def sampler_loop() -> None:
            while not stop.is_set():
                resource_peaks.append(resource_sample())
                await asyncio.sleep(0.05)

        hb = asyncio.create_task(heartbeat_loop())
        rs = asyncio.create_task(sampler_loop())

        # 突发：模拟模型一轮返回多个工具调用
        burst_start = time.perf_counter()
        await asyncio.gather(*[fire_tool(i) for i in range(TOOL_CONCURRENCY)])
        burst_wall = time.perf_counter() - burst_start

        # 阶段 2：一条长 provider 流 + 并发工具 + 心跳。
        # 这测的是「流式转发期间连接/线程是否被长期占住」——历史静默
        # （96–224s）远长于工具突发（<1s），必须有长时间持有者。
        stream_task = asyncio.create_task(open_slow_stream(client, run_id, headers))
        stream_pool: list[int] = []
        stream_start = time.perf_counter()
        while time.perf_counter() - stream_start < STREAM_SECONDS:
            stream_pool.append(engine.pool.checkedout())
            await asyncio.sleep(0.1)
        stream_task.cancel()
        try:
            await stream_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

        # 再持续一段，观察心跳是否恢复
        await asyncio.sleep(max(0.0, BURST_SECONDS - burst_wall))
        stop.set()
        await asyncio.gather(hb, rs)

    sampler.stop()
    sampler.join(timeout=2)
    server.should_exit = True
    await server_task

    def pct(values: list[float], p: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        idx = min(len(ordered) - 1, int(len(ordered) * p))
        return ordered[idx] * 1000.0

    peaks = {
        key: max((s[key] for s in resource_peaks), default=0)
        for key in ("anyio_borrowed", "anyio_total", "pool_checkedout", "pool_size")
    }
    lag = sampler.samples
    return {
        "tool_concurrency": TOOL_CONCURRENCY,
        "burst_wall_seconds": round(burst_wall, 3),
        "tool_latency_ms": {
            "count": len(tool_latencies),
            "p50": round(pct(tool_latencies, 0.5), 1),
            "p95": round(pct(tool_latencies, 0.95), 1),
            "max": round(max(tool_latencies, default=0) * 1000.0, 1),
        },
        "heartbeat_latency_ms": {
            "count": len(heartbeat_latencies),
            "p50": round(pct(heartbeat_latencies, 0.5), 1),
            "p95": round(pct(heartbeat_latencies, 0.95), 1),
            "max": round(max(heartbeat_latencies, default=0) * 1000.0, 1),
        },
        "loop_lag_ms": {
            "samples": len(lag),
            "max": round(max(lag, default=0.0) * 1000.0, 2),
            "p95": round(pct(lag, 0.95), 2),
        },
        "resource_peaks": peaks,
        "stream_status": [str(x) for x in stream_status],
        "stream_chunks": len(stream_bytes),
        "stream_bytes_total": sum(stream_bytes),
        "stream_pool_checkedout": {
            "samples": len(stream_pool),
            "max": max(stream_pool, default=0),
            "min": min(stream_pool, default=0),
            "nonzero": sum(1 for v in stream_pool if v),
        },
        "samples_taken": len(resource_peaks),
        "ids": {"run_id": run_id, "job_id": job_id, "attempt_id": attempt_id},
    }


def main() -> None:
    result = asyncio.run(run_probe())
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
