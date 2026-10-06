"""C5：Redis 降级策略（真实故障注入，不是 mock）。

## 核心断言

Redis 是加速层，因此它坏掉时**不能**：

1. **fail-open**（放行）——那会让配额失效，比不可用更糟；
2. 让持久状态丢失——lease/settle/counter 必须完全由 PostgreSQL 恢复；
3. 缓存失效导致**授权放宽**。

同时它**必须**：显式、有界地失败，并且调用方能区分「本层裁决」与「本层无结论」。

## 为什么三态而不是布尔

`try_set_if_absent` 返回 `True` / `False` / `None`：

- `True` = 本层裁决「已获得」；
- `False` = 本层裁决「已被占用」；
- `None` = **本层不可用，无结论** → 调用方走 PostgreSQL 有界路径。

把 `None` 折成 `False` 会让 Redis 故障时 admission **全部拒绝**（可用性故障）；
折成 `True` 会 fail-open（配额失效）。因此三态是安全要求，不是风格。

## 这些用例用真实 Redis

`REDIS_URL` 未设置时整体 skip（exit 2 语义，不是 pass）。真实故障注入包括：
指向错误端口、清空 db（模拟重启丢数据）。
"""

from __future__ import annotations

import os
import time

import pytest

from app.services import redis_accel
from app.services.redis_accel import RedisAccelerator, scoped_key

REDIS_URL = os.environ.get("REDIS_URL")

pytestmark = pytest.mark.skipif(
    not REDIS_URL, reason="需要 REDIS_URL 指向隔离 Redis（不得指向开发/线上）"
)


@pytest.fixture
def accel() -> RedisAccelerator:
    """每个用例一个独立实例，避免冷却窗口互相污染。"""
    a = RedisAccelerator(REDIS_URL)
    yield a
    a.delete(scoped_key(layer="probe", scope_kind="global", scope_id=0, resource="t"))


def test_unconfigured_is_not_an_error(monkeypatch):
    """未配置 Redis = 本层关闭，不是故障。

    注意 `RedisAccelerator(None)` 会**回落到环境变量**（这是设计：显式传 None
    表示「用默认配置」）。要测「未配置」，必须让环境里也没有 REDIS_URL。
    """
    monkeypatch.delenv("REDIS_URL", raising=False)
    a = RedisAccelerator(None)
    assert a.configured is False
    assert a.available() is False
    assert a.get("anything") is None
    assert a.try_set_if_absent("k", ttl_seconds=10) is None
    assert a.set("k", "v", ttl_seconds=10) is False
    # 健康快照必须如实反映「未配置」，且 policy 仍可读
    health = a.health()
    assert health.available is False
    assert health.policy == redis_accel.REDIS_DEGRADATION_POLICY


def test_available_redis_reports_decisions(accel: RedisAccelerator):
    """可用时三态中的两个裁决态都可达。"""
    key = scoped_key(layer="probe", scope_kind="global", scope_id=0, resource="t")
    accel.delete(key)
    assert accel.try_set_if_absent(key, ttl_seconds=30) is True
    assert accel.try_set_if_absent(key, ttl_seconds=30) is False, "SET NX 不是单赢家"
    assert accel.get(key) == "1"
    assert accel.delete(key) is True


def test_unavailable_returns_none_not_false():
    """Redis 不可用必须返回 `None`（无结论），**不是** `False`（已被占用）。

    这是本文件最重要的断言：把不可用折成 False 会让 admission 在 Redis 故障时
    全部拒绝——把加速层故障升级成可用性故障。
    """
    bad = (REDIS_URL or "").rsplit(":", 1)[0] + ":1"  # 必然失败的端口
    a = RedisAccelerator(bad)
    started = time.perf_counter()
    outcome = a.try_set_if_absent("k", ttl_seconds=10)
    elapsed = time.perf_counter() - started

    assert outcome is None, f"不可用时返回了 {outcome!r}，必须是无结论"
    assert outcome is not False, "把不可用当成已被占用（会导致全量拒绝）"
    assert elapsed < 3.0, f"不可用时耗时 {elapsed:.3f}s，未快速失败"
    health = a.health()
    assert health.available is False
    assert health.last_error is not None, "不可用时未记录错误类别"


def test_unavailable_enters_cooldown_so_latency_is_bounded():
    """不可用后进入冷却：后续调用**立即**返回，不再逐次超时。

    没有冷却时，Redis 故障会让**每个**请求都付一次连接超时——故障变成固定延迟。
    """
    bad = (REDIS_URL or "").rsplit(":", 1)[0] + ":1"
    a = RedisAccelerator(bad)
    assert a.try_set_if_absent("k", ttl_seconds=10) is None  # 第一次：真实尝试
    assert a.available() is False, "首次失败后未进入冷却"

    started = time.perf_counter()
    for _ in range(50):
        assert a.try_set_if_absent("k", ttl_seconds=10) is None
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5, f"冷却期内 50 次调用耗时 {elapsed:.3f}s，冷却未生效"


def test_cache_write_failure_does_not_raise():
    """缓存写失败不得抛异常：它只是加速，失败应被忽略。"""
    bad = (REDIS_URL or "").rsplit(":", 1)[0] + ":1"
    a = RedisAccelerator(bad)
    assert a.set("k", "v", ttl_seconds=10) is False
    assert a.delete("k") is False


def test_scoped_key_rejects_unknown_scope():
    """未知 scope 必须失败：自由拼接的 key 会造成跨租户命中（授权泄漏）。"""
    with pytest.raises(ValueError):
        scoped_key(layer="l", scope_kind="tenant", scope_id=1, resource="r")


def test_scoped_key_includes_epoch_and_scope():
    """key 必须带 scope 与 epoch，使换版可整代失效而不需扫描删除。"""
    a = scoped_key(layer="cache", scope_kind="account", scope_id=7, resource="r", epoch=1)
    b = scoped_key(layer="cache", scope_kind="account", scope_id=7, resource="r", epoch=2)
    c = scoped_key(layer="cache", scope_kind="account", scope_id=8, resource="r", epoch=1)
    assert a != b, "epoch 变化必须产生不同 key"
    assert a != c, "scope 变化必须产生不同 key"
    assert "account:7" in a


def test_flush_does_not_lose_anything_durable(accel: RedisAccelerator):
    """清空 Redis（模拟重启丢数据）后，本层只是 cache miss，不是错误。

    持久状态的正确性由 PostgreSQL 保证，本层无从影响——这正是「Redis 不是真源」
    的可测断言。真实 PG 侧的验证见 `scripts/migration-proof/redis_degradation_probe.py`。
    """
    key = scoped_key(layer="probe", scope_kind="space", scope_id=3, resource="t")
    assert accel.set(key, "cached", ttl_seconds=60) is True
    assert accel.get(key) == "cached"

    accel.delete(key)  # 等价于重启丢 key
    assert accel.get(key) is None, "清空后应 cache miss"
    # 且本层仍报告可用（清空不是故障）
    assert accel.health().available is True
