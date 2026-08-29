"""进程内后台维护循环：Agent reaper + canonical StewardJob 生产入口。

P1 收口（08-29）：steward 的 lease/run/settle/reaper 领域执行器此前只有测试
调用，缺生产 scheduler/worker 闭环；AgentRun 的 reaper_pass 同样无后台调用。
本模块在 FastAPI lifespan 内启动单个 asyncio 任务，把两者串联：

- AGENT_RUNTIME_ENABLED：周期执行 ``agent_queue.reaper_pass``
  （过期 lease 按 cancelled 收敛，不回队重试）；
- STEWARD_ENABLED 且 STEWARD_WORKER_ENABLED：周期执行 ``steward.reaper_pass``
  （过期 lease 回队或判 expired），并把 queued 作业 lease→execute（含失败
  结算）连续泵干。

DB 操作全部走 SessionLocal 独立会话，与请求会话隔离；单次 tick 异常只记日志
不终止循环（调度器自身 fail-open；领域事务内部仍 fail-closed）。

开关默认关闭：测试与单进程开发不启动后台任务；compose 的 api 服务显式开启。
serve.py 双 listener 共享 lifespan，用进程级单例防止重复启动。
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app import config
from app.db import SessionLocal
from app.models.steward import StewardJob
from app.services import agent_queue, steward

logger = logging.getLogger(__name__)

#: 单次 tick 最多连续执行的 Steward 作业数（防止单 tick 长时间占住事件循环外线程）
_MAX_JOBS_PER_TICK = 10

_task: asyncio.Task[None] | None = None
# lifespan 持有计数：serve.py 双 listener 共享同一 lifespan，两侧都在运行时
# 循环只启动一次；任一侧优雅停机不得取消另一侧仍在使用的循环（P1 修复）。
_holders = 0


def run_maintenance_tick() -> dict[str, int]:
    """执行一轮维护：返回各部分处理计数（同步，独立会话，可单测直接调用）。"""
    counters = {
        "agent_reaped": 0,
        "steward_reaped": 0,
        "steward_executed": 0,
        "steward_failed": 0,
    }
    db = SessionLocal()
    try:
        if config.AGENT_RUNTIME_ENABLED:
            counters["agent_reaped"] = agent_queue.reaper_pass(db)
        if config.STEWARD_ENABLED and config.STEWARD_WORKER_ENABLED:
            counters["steward_reaped"] = steward.reaper_pass(db)
            for _ in range(_MAX_JOBS_PER_TICK):
                job = steward.lease_next_steward_job(db, leased_by="inproc-steward-worker")
                if job is None:
                    break
                try:
                    # execute_steward_job：成功→succeeded；异常→failed 结算（独立
                    # 事务已提交）后原样抛出。单个毒药作业不得卡死整轮泵。
                    steward.execute_steward_job(db, job)
                    counters["steward_executed"] += 1
                except Exception:
                    counters["steward_failed"] += 1
                    logger.exception("steward job %s failed and was settled", job.id)
        db.commit()
        return counters
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _pending_queued_jobs() -> int:
    db = SessionLocal()
    try:
        return int(
            db.scalar(select(StewardJob.id).where(StewardJob.status == "queued").limit(1))
            is not None
        )
    finally:
        db.close()


async def maintenance_loop(interval_seconds: float) -> None:
    """周期维护循环；取消时安静退出。"""
    while True:
        try:
            counters = await asyncio.to_thread(run_maintenance_tick)
            if any(counters.values()):
                logger.info("maintenance tick", extra={"counters": counters})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("maintenance tick failed; retrying next interval")
        await asyncio.sleep(interval_seconds)


def start_maintenance_loop() -> asyncio.Task[None] | None:
    """登记一个 lifespan 持有者；首个持有者按配置启动循环（未开启职责返回 None）。

    返回 None 时持有计数仍递增（release 对称扣减），保证双 listener 生命周期
    交叉时计数不漂移。
    """
    global _task, _holders
    _holders += 1
    if not (config.AGENT_RUNTIME_ENABLED or config.STEWARD_ENABLED):
        return None
    if _task is not None and not _task.done():
        return _task
    interval = max(config.MAINTENANCE_INTERVAL_SECONDS, 0.5)
    _task = asyncio.get_running_loop().create_task(
        maintenance_loop(interval), name="familygraph-maintenance"
    )
    logger.info("maintenance loop started (interval=%.1fs, holders=%d)", interval, _holders)
    return _task


async def stop_maintenance_loop() -> None:
    """注销一个 lifespan 持有者；仅当最后一个持有者退出时停止循环（幂等）。"""
    global _task, _holders
    if _holders > 0:
        _holders -= 1
    if _holders > 0:
        return
    if _task is None:
        return
    task, _task = _task, None
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
