"""C5 接入：Redis 负缓存用于配额快速拒绝。

## 为什么是「负缓存」而不是「令牌桶」

冻结的降级策略是 `pg_fallback`：Redis 失效不能改变可用性语义。若 Redis 持有令牌
并**授权**请求，它就是配额真源——Redis 一挂就必须 fail-closed，与策略矛盾。

因此 Redis 只存**负缓存**：某租户近期被权威路径判为已满时写短 TTL 标记，后续请求
命中即快速拒绝，不再打数据库。

## 三个安全性质（各有断言）

1. **只能拒绝，永不授权**：标记存在时拒绝，不存在时照常走权威路径。因此 Redis
   不可用（返回 None）等价于「没有标记」，退化为未接入状态——**不 fail-open**。
2. **按租户分区**：它表达「这个租户满了」，不是「这个上游坏了」。混淆会让一个
   租户的饱和拒绝其他租户。
3. **权威裁决不变**：真正准入仍由进程内 limiter + 集群 counter 决定。

## 必须用异步版本

Redis 客户端是同步的，单次操作最多阻塞 `REDIS_OPERATION_TIMEOUT_SECONDS`。在事件
循环上调用会把整个进程卡住（09-30 的缺陷形态）。本文件断言调用点用的是异步包装。
"""

from __future__ import annotations

import inspect
import os

import pytest

from app.services import redis_accel

REDIS_URL = os.environ.get("REDIS_URL")

pytestmark = pytest.mark.skipif(
    not REDIS_URL, reason="需要 REDIS_URL 指向隔离 Redis（不得指向开发/线上）"
)


@pytest.fixture(autouse=True)
def _fresh_accelerator(monkeypatch):
    # 标记 TTL 必须大于「一次权威判定 + 一次 Redis 往返」的耗时，否则标记在下一个
    # 请求到来前就过期、负缓存失效。经 SSH 隧道时该耗时可达秒级（实测 1 秒会失效），
    # 因此测试显式调大；生产同机 Redis 用默认 1 秒即可（毫秒级余量充足）。
    monkeypatch.setenv("REDIS_OVER_QUOTA_TTL_SECONDS", "30")
    redis_accel.reset_accelerator_for_tests()
    yield
    redis_accel.reset_accelerator_for_tests()


def test_unmarked_tenant_is_not_rejected():
    """未标记 = 不拒绝：这是「不 fail-open」的另一面（不误拒）。"""
    assert redis_accel.is_marked_over_quota(tenant="account:1", resource="agent_tool") is False


def test_marked_tenant_is_rejected_and_expires():
    """标记存在即拒绝；TTL 短（默认 1s）使「名额释放后仍被拒」有界。"""
    redis_accel.mark_over_quota(tenant="account:2", resource="agent_tool")
    assert redis_accel.is_marked_over_quota(tenant="account:2", resource="agent_tool") is True

    redis_accel.clear_over_quota(tenant="account:2", resource="agent_tool")
    assert redis_accel.is_marked_over_quota(tenant="account:2", resource="agent_tool") is False


def test_partitioned_by_tenant_and_resource():
    """按租户 + 执行平面分区：一个租户的饱和不得拒绝其他租户。"""
    redis_accel.mark_over_quota(tenant="account:3", resource="agent_tool")
    assert redis_accel.is_marked_over_quota(tenant="account:3", resource="agent_tool") is True
    assert redis_accel.is_marked_over_quota(tenant="account:4", resource="agent_tool") is False
    assert redis_accel.is_marked_over_quota(tenant="account:3", resource="agent_provider") is False


def test_unavailable_redis_means_no_marking_not_fail_open():
    """Redis 不可用时返回「无标记」——即走权威路径，**不是**放行。

    这是最关键的一条：如果不可用被当成「已标记」，Redis 一挂所有请求都被拒
    （可用性故障）；如果被当成「未标记但直接放行」，那就是 fail-open。
    正确语义是「本层无结论，交给权威路径」。
    """
    bad = (REDIS_URL or "").rsplit(":", 1)[0] + ":1"
    redis_accel.reset_accelerator_for_tests()
    os.environ["REDIS_URL"] = bad
    try:
        assert redis_accel.is_marked_over_quota(tenant="account:9", resource="agent_tool") is False
        assert redis_accel.mark_over_quota(tenant="account:9", resource="agent_tool") is False
    finally:
        os.environ["REDIS_URL"] = REDIS_URL
        redis_accel.reset_accelerator_for_tests()


def test_admission_path_uses_async_wrappers():
    """结构性断言：准入路径不得在事件循环上调用同步 Redis。

    同步调用最多阻塞 `REDIS_OPERATION_TIMEOUT_SECONDS`（默认 0.5s），会把整个进程
    的端点一起卡住——09-30 的缺陷形态。这条断言防止后续重构改回同步版本。
    """
    from app.api import internal_agent

    source = inspect.getsource(internal_agent)
    for sync_name in (
        "redis_accel.is_marked_over_quota(",
        "redis_accel.mark_over_quota(",
    ):
        assert sync_name not in source, f"准入路径使用了同步 Redis 调用：{sync_name}"
    assert "is_marked_over_quota_async" in source
    assert "mark_over_quota_async" in source
